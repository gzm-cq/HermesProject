"""增量放置 — 去重 → 矛盾检测 → 注意力定位 → 写入 PG + K 向量更新。

本模块实现 post_llm_call 的核心放置逻辑。性能关键点：
- 新知识点 embedding 只计算一次，dedup/conflict 复用该向量。
- 同一批次共享 parent/sibling/leaf 候选。
- parent K 向量在内存中按原 EMA 顺序累计，最后只写一次。
- get_leaf_nodes() 有 TTL 缓存，避免每轮查 PG。
- 新增知识点用 batch INSERT 一次写入，大幅减少 PG 往返。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from knowledge_tree_builder import batch_embed, cosine_similarity
from knowledge_tree_plugin.recall import (
    _CJK_STOP_CHARS as _CJK_STOP_WORDS,
    locate_best_subject,
)

logger = logging.getLogger(__name__)

_LEAF_CACHE_TTL = 60.0  # 秒
_leaf_cache: list[dict[str, Any]] | None = None
_leaf_cache_at: float = 0.0
_leaf_cache_lock = threading.Lock()

_CONFLICT_KEYWORDS = ("不", "不是", "不能", "无效", "不再", "相反", "错误", "不要")

# 实体提取常见后缀（技术名词识别）
_TECH_SUFFIXES = ("树", "表", "库", "器", "层", "线", "网", "图", "集", "据", "件", "法", "式")


@dataclass
class PlacementContext:
    """单次增量放置共享上下文。"""

    parent_node: dict[str, Any] | None
    leaf_nodes: list[dict[str, Any]]
    sibling_nodes: list[dict[str, Any]]
    parent_k_vector: list[float] | None
    placement_count: int


# ========== 主入口 ==========


def place_new_knowledge_points(
    new_points: list[str] | list[dict[str, str]],
    adapter: Any,
    session_id: str,
    user_message: str,
    *,
    embed_base_url: str,
    embed_model: str,
    embed_api_key: str,
    embed_batch_size: int = 20,
    dedup_threshold: float = 0.95,
    conflict_threshold: float = 0.80,
    cold_start_threshold: int = 20,
    k_vector_alpha_max: float = 0.1,
) -> dict[str, Any]:
    """将新知识点增量放置到知识树中。

    完整流程：Embedding → 去重 → 矛盾检测 → 注意力定位 → batch 写入 → K 向量更新。
    """
    started_at = time.perf_counter()
    result: dict[str, Any] = {
        "total": len(new_points),
        "dedup_merged": 0,
        "new_nodes": 0,
        "conflicts": 0,
        "parent_id": None,
        "errors": 0,
    }

    if not new_points:
        return result

    point_texts = _normalize_new_points(new_points)
    if not point_texts:
        result["errors"] = len(new_points)
        return result

    t_embed = time.perf_counter()
    embeddings = batch_embed(
        point_texts,
        base_url=embed_base_url,
        model=embed_model,
        api_key=embed_api_key,
        batch_size=embed_batch_size,
    )
    embed_ms = (time.perf_counter() - t_embed) * 1000
    if not embeddings:
        logger.warning("place_new: embedding 全部失败")
        result["errors"] = len(point_texts)
        return result

    t_locate = time.perf_counter()
    parent_node = _locate_parent(point_texts, embeddings, adapter, user_message)
    locate_ms = (time.perf_counter() - t_locate) * 1000
    if parent_node is not None:
        result["parent_id"] = parent_node["id"]
    else:
        logger.warning(
            "_locate_parent 返回 None，%d 个知识点无匹配科目将被丢弃",
            len(point_texts),
        )

    t_context = time.perf_counter()
    context = _prepare_placement_context(adapter, parent_node)
    context_ms = (time.perf_counter() - t_context) * 1000

    parent_k = context.parent_k_vector
    placement_count = context.placement_count
    inserted_parent_embeddings: list[list[float]] = []
    pending_records: list[tuple[str, str]] = []
    pending_embeddings: list[list[float]] = []
    pending_cache_nodes: list[dict[str, Any]] = []
    dedup_ms = 0.0
    conflict_ms = 0.0
    db_write_ms = 0.0

    for i, point_text in enumerate(point_texts):
        if i >= len(embeddings):
            result["errors"] += 1
            continue
        point_embedding = embeddings[i]

        if context.parent_node is not None:
            t = time.perf_counter()
            existing_id = _dedup_before_insert_with_embedding(
                new_embedding=point_embedding,
                leaf_nodes=context.leaf_nodes,
                threshold=dedup_threshold,
            )
            dedup_ms += (time.perf_counter() - t) * 1000
            if existing_id is not None:
                result["dedup_merged"] += 1
                continue

        if context.parent_node is not None:
            t = time.perf_counter()
            conflicts = _detect_conflict_with_embedding(
                new_point_text=point_text,
                new_embedding=point_embedding,
                sibling_points=context.sibling_nodes,
                conflict_threshold=conflict_threshold,
            )
            conflict_ms += (time.perf_counter() - t) * 1000
            result["conflicts"] += len(conflicts)
            _insert_conflict_reviews(adapter, point_text, conflicts, conflict_threshold)

        # 暂存待批量写入
        pending_records.append((point_text[:30], point_text))
        pending_embeddings.append(point_embedding)

        # 批内去重上下文（嵌入向量已在，ID 用占位，batch insert 后回填）
        temp_id = -(len(pending_records))
        new_node = {"id": temp_id, "name": point_text[:30], "k_vector": point_embedding}
        pending_cache_nodes.append(new_node)
        context.leaf_nodes.append(new_node)
        context.sibling_nodes.append(new_node)

        if context.parent_node is not None:
            parent_k, placement_count = _next_k_vector(
                old_k=parent_k,
                placement_count=placement_count,
                new_embedding=point_embedding,
                alpha_max=k_vector_alpha_max,
            )
            inserted_parent_embeddings.append(point_embedding)

    # === 批量写入 PG（节点插入 + k_vector 更新 + 实体链接放在同一事务，保证原子性）===
    parent_node_id = context.parent_node["id"] if context.parent_node is not None else None
    if pending_records and parent_node_id is not None:
        t = time.perf_counter()
        try:
            # 使用 atomic() 上下文管理器：节点插入 + 实体链接 + k_vector 更新要么全部成功，要么全部回滚
            with adapter.atomic():
                # commit=False：节点插入不立即提交，等待同事务内其他写入完成后一起提交
                node_ids = adapter.batch_insert_knowledge_points(
                    pending_records,
                    parent_id=parent_node_id,
                    k_vectors=pending_embeddings,
                    commit=False,
                )
                for node, node_id in zip(pending_cache_nodes, node_ids):
                    node["id"] = node_id
                result["new_nodes"] = len(node_ids)
                # 同一事务内写入实体关系（失败回滚整体操作）
                for node_id, (name, text) in zip(node_ids, pending_records):
                    entities = _extract_entities(text or name)
                    if entities:
                        adapter.insert_entity_links(node_id, entities)
                # 同一事务内更新父节点 k_vector（commit=False 在最后统一提交）
                if inserted_parent_embeddings and parent_k is not None:
                    adapter.update_k_vector(
                        node_id=parent_node_id,
                        k_vector=parent_k,
                        placement_count=placement_count,
                        commit=False,
                    )
        except Exception as e:
            logger.warning("batch insert/update failed (transaction rolled back): %s", e)
            result["errors"] += len(pending_records)
        else:
            # 写入成功后失效 leaf cache，确保后续 recall 能看到新知识点
            _reset_leaf_cache()
        db_write_ms += (time.perf_counter() - t) * 1000
    elif context.parent_node is not None and inserted_parent_embeddings and parent_k is not None:
        # 兜底：无 pending_records 但有 parent 更新（如 dedup 合并场景）
        try:
            t = time.perf_counter()
            adapter.update_k_vector(
                node_id=parent_node_id,
                k_vector=parent_k,
                placement_count=placement_count,
            )
            db_write_ms += (time.perf_counter() - t) * 1000
        except Exception as e:
            logger.warning("parent k_vector update failed: %s", e)
            result["errors"] += 1

    logger.info(
        "place_new_knowledge_points completed",
        extra={
            "session_id": session_id,
            "total": result["total"],
            "new_nodes": result["new_nodes"],
            "dedup_merged": result["dedup_merged"],
            "conflicts": result["conflicts"],
            "embed_ms": round(embed_ms, 2),
            "locate_ms": round(locate_ms, 2),
            "context_ms": round(context_ms, 2),
            "dedup_ms": round(dedup_ms, 2),
            "conflict_ms": round(conflict_ms, 2),
            "db_write_ms": round(db_write_ms, 2),
            "total_ms": round((time.perf_counter() - started_at) * 1000, 2),
        },
    )
    return result


# ========== 辅助函数 ==========


def _normalize_new_points(new_points: list[str] | list[dict[str, str]]) -> list[str]:
    """兼容 list[str] 与 AtomicKnowledge dict。"""
    if not new_points:
        return []
    if isinstance(new_points[0], dict):
        return [str(p.get("text", "")).strip() for p in new_points if str(p.get("text", "")).strip()]  # type: ignore[union-attr]
    return [str(p).strip() for p in new_points if str(p).strip()]


def _extract_entities(text: str) -> list[str]:
    """从知识点文本中提取命名实体。

    策略：
    - 英文标识符/缩写（>= 2 字符，转小写）
    - CJK 词组（>= 2 字，首字非停用字）
    - 技术名词（含技术后缀）
    返回去重列表。
    """
    entities: set[str] = set()

    # 英文实体：驼峰/大写缩写/标识符
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}", text):
        lower = token.lower()
        if lower not in ("the", "and", "for", "are", "not", "can", "use", "all"):
            entities.add(lower)

    # CJK 二字组（首字非停用字）
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    for i in range(len(cjk_chars) - 1):
        if cjk_chars[i] not in _CJK_STOP_WORDS:
            bigram = cjk_chars[i] + cjk_chars[i + 1]
            entities.add(bigram)

    # 三字技术名词: 若二字组后缀匹配技术术语特征，扩展为三字组
    for i in range(len(cjk_chars) - 2):
        bigram = cjk_chars[i + 1] + cjk_chars[i + 2]
        if bigram and bigram[-1] in _TECH_SUFFIXES:
            triple = cjk_chars[i] + cjk_chars[i + 1] + cjk_chars[i + 2]
            entities.add(triple)

    return sorted(entities)[:20]  # 最多 20 实体/知识点


def _get_cached_leaf_nodes(adapter: Any) -> list[dict[str, Any]]:
    """TTL 缓存的 get_leaf_nodes，避免每轮 placement 都查 PG。线程安全。"""
    global _leaf_cache, _leaf_cache_at
    now = time.time()
    with _leaf_cache_lock:
        if _leaf_cache is not None and (now - _leaf_cache_at) < _LEAF_CACHE_TTL:
            return _leaf_cache
        _leaf_cache = adapter.get_leaf_nodes()
        _leaf_cache_at = now
        assert _leaf_cache is not None
        return _leaf_cache


def _reset_leaf_cache() -> None:
    """重置 leaf cache（供测试使用）。线程安全。"""
    global _leaf_cache, _leaf_cache_at
    with _leaf_cache_lock:
        _leaf_cache = None
        _leaf_cache_at = 0.0


def _prepare_placement_context(adapter: Any, parent_node: dict[str, Any] | None) -> PlacementContext:
    """读取并缓存一次放置批次需要的共享上下文。"""
    if parent_node is None:
        return PlacementContext(
            parent_node=None,
            leaf_nodes=[],
            sibling_nodes=[],
            parent_k_vector=None,
            placement_count=0,
        )

    parent_id = parent_node["id"]
    return PlacementContext(
        parent_node=parent_node,
        leaf_nodes=_get_cached_leaf_nodes(adapter),
        sibling_nodes=adapter.get_child_nodes(parent_id),
        parent_k_vector=adapter.get_node_embedding(parent_id),
        placement_count=adapter.get_placement_count(parent_id),
    )


def _locate_parent(
    new_points: list[str],
    embeddings: list[list[float]],
    adapter: Any,
    user_message: str,
) -> dict[str, Any] | None:
    """定位新知识点在树中的最佳父节点。

    关键词匹配与 embedding 定位使用同一来源，避免 user_message（疑问句）
    与知识点（陈述句）语义错位导致挂错科目：
    - Phase 1 关键词匹配：用新知识点拼接文本（陈述句，与科目名更接近）
    - Phase 2 embedding 定位：用新知识点向量均值

    user_message 仅作为日志上下文保留，不再参与科目定位决策。
    """
    avg_embedding = np.mean(embeddings, axis=0)
    # 用知识点文本做关键词匹配，与 embedding 来源保持一致
    locate_query = " ".join(new_points) if new_points else user_message
    return locate_best_subject(locate_query, avg_embedding, adapter)


def _batch_cosine_similarity(
    query_vec: np.ndarray,
    candidate_vectors: list[list[float]],
) -> np.ndarray:
    """批量计算查询向量与所有候选向量的余弦相似度（向量化）。

    Args:
        query_vec: shape (D,) 查询向量
        candidate_vectors: N 个候选向量列表

    Returns:
        shape (N,) 的相似度数组，无向量的位置为 0.0
    """
    if not candidate_vectors:
        return np.array([], dtype=np.float32)
    valid_vectors = []
    valid_indices = []
    for i, v in enumerate(candidate_vectors):
        if v is not None:
            valid_vectors.append(np.array(v, dtype=np.float32))
            valid_indices.append(i)
    if not valid_vectors:
        return np.zeros(len(candidate_vectors), dtype=np.float32)
    matrix = np.stack(valid_vectors, axis=0)  # (N_valid, D)
    dot = matrix @ query_vec  # (N_valid,)
    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query_vec) + 1e-10
    sims_valid = dot / norms
    result = np.zeros(len(candidate_vectors), dtype=np.float32)
    for idx, sim in zip(valid_indices, sims_valid):
        result[idx] = float(sim)
    return result


def _dedup_before_insert_with_embedding(
    *,
    new_embedding: list[float],
    leaf_nodes: list[dict[str, Any]],
    threshold: float = 0.95,
) -> int | None:
    """用已计算的新知识点向量做去重检查，避免重复 embedding API 调用。"""
    new_vec = np.array(new_embedding, dtype=np.float32)
    k_vectors = [node.get("k_vector") for node in leaf_nodes]
    sims = _batch_cosine_similarity(new_vec, k_vectors)
    best_idx = int(np.argmax(sims)) if len(sims) else -1
    if best_idx >= 0 and sims[best_idx] > threshold:
        return leaf_nodes[best_idx].get("id")
    return None


def _detect_conflict_with_embedding(
    *,
    new_point_text: str,
    new_embedding: list[float],
    sibling_points: list[dict[str, Any]],
    conflict_threshold: float = 0.80,
) -> list[dict[str, Any]]:
    """用已计算的新知识点向量做矛盾检测（向量化批量计算）。"""
    new_vec = np.array(new_embedding, dtype=np.float32)
    new_has_negation = any(kw in new_point_text for kw in _CONFLICT_KEYWORDS)
    conflicts: list[dict[str, Any]] = []

    k_vectors = [p.get("k_vector") for p in sibling_points]
    sims = _batch_cosine_similarity(new_vec, k_vectors)

    for i, existing in enumerate(sibling_points):
        sim = float(sims[i])
        if sim <= conflict_threshold:
            continue
        existing_name = existing.get("name", "") or ""
        existing_has_negation = any(kw in existing_name for kw in _CONFLICT_KEYWORDS)
        if new_has_negation != existing_has_negation:
            conflicts.append({
                "new_text": new_point_text,
                "existing": existing,
                "similarity": sim,
                "reason": f"语义相似度 {sim:.3f} 但关键词对立",
            })
    return conflicts


def _insert_conflict_reviews(
    adapter: Any,
    point_text: str,
    conflicts: list[dict[str, Any]],
    default_similarity: float,
) -> None:
    """写入矛盾审查队列；单条失败不影响整体放置。"""
    try:
        for conflict in conflicts:
            existing_id = conflict["existing"]["id"]
            new_text = conflict.get("new_text", point_text)

            # 去重保护：已有相同冲突则跳过
            if hasattr(adapter, "review_exists"):
                if adapter.review_exists(
                    new_text=new_text,
                    existing_node_id=existing_id,
                    conflict_type="semantic_conflict",
                    status="pending",
                ):
                    continue

            adapter.insert_review(
                new_text=new_text,
                existing_node_id=existing_id,
                existing_text=conflict["existing"].get("name", ""),
                conflict_type="semantic_conflict",
                similarity=float(conflict.get("similarity", default_similarity)),
            )
    except Exception as e:
        logger.warning("insert_review failed: %s", e)


def _next_k_vector(
    *,
    old_k: list[float] | None,
    placement_count: int,
    new_embedding: list[float],
    alpha_max: float = 0.1,
) -> tuple[list[float], int]:
    """计算下一步 K 向量，不直接写 DB。"""
    next_count = placement_count + 1
    if old_k is None or len(old_k) != len(new_embedding):
        if old_k is not None and len(old_k) != len(new_embedding):
            logger.warning(
                "K 向量维度不匹配 (old=%d, new=%d)，重置为新 embedding",
                len(old_k), len(new_embedding),
            )
        return list(new_embedding), next_count

    alpha = min(1.0 / next_count, alpha_max)
    new_k = [(1.0 - alpha) * o + alpha * n for o, n in zip(old_k, new_embedding)]
    return new_k, next_count


def _update_k_vector(
    adapter: Any,
    node_id: int,
    new_embedding: list[float],
    alpha_max: float = 0.1,
) -> None:
    """更新父节点的 K 向量（兼容旧单条调用与既有测试）。"""
    old_k = adapter.get_node_embedding(node_id)
    placement_count = adapter.get_placement_count(node_id)
    new_k, next_count = _next_k_vector(
        old_k=old_k,
        placement_count=placement_count,
        new_embedding=new_embedding,
        alpha_max=alpha_max,
    )
    adapter.update_k_vector(
        node_id=node_id,
        k_vector=new_k,
        placement_count=next_count,
    )
