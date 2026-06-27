"""增量放置 + 矛盾检测 — P1 阶段功能

增量知识点的树定位、去重、矛盾检测。
P1 阶段启用，依赖初始树建成后有足够的放置行为数据。
"""

from __future__ import annotations

from typing import Any, Callable

from knowledge_tree_builder.core.embeddings import cosine_similarity


def dedup_before_insert(
    new_point_text: str,
    leaf_nodes: list[dict[str, Any]],
    embed_fn: Callable[[list[str]], list[list[float]] | None],
    cosine_sim_fn: Callable[[Any, Any], float] = cosine_similarity,
    threshold: float = 0.95,
) -> str | None:
    """增量去重检查。

    对新知识点做 embedding，与已有叶子节点比较余弦相似度。
    > threshold 视为重复，返回匹配的已有节点 ID；否则返回 None。

    Args:
        new_point_text: 新知识点文本
        leaf_nodes: 已有叶子节点列表（各含 "id", "k_vector"）
        embed_fn: embedding 函数 (texts) → [embeddings]
        cosine_sim_fn: 余弦相似度函数，便于测试注入
        threshold: 去重余弦相似度阈值

    Returns:
        匹配到的已有节点 ID，或 None（无重复）
    """
    new_emb = embed_fn([new_point_text])
    if not new_emb:
        return None

    new_vec = new_emb[0]
    for existing in leaf_nodes:
        if existing.get("k_vector") is not None:
            sim = cosine_sim_fn(new_vec, existing["k_vector"])
            if sim > threshold:
                return existing["id"]

    return None


def detect_conflict(
    new_point_text: str,
    sibling_points: list[dict[str, Any]],
    embed_fn: Callable[[list[str]], list[list[float]] | None],
    cosine_sim_fn: Callable = cosine_similarity,
    conflict_threshold: float = 0.80,
    db_adapter=None,
) -> list[dict[str, Any]]:
    """矛盾检测。

    检查新知识点与同科兄弟节点是否存在语义矛盾。
    矛盾判定：语义相似（> conflict_threshold）但关键词对立。
    有 db_adapter 时将可疑矛盾存入 review_queue 表，推迟到 consolidation 处理。

    Args:
        new_point_text: 新知识点文本
        sibling_points: 同科兄弟节点列表（各含 "id", "name"）
        embed_fn: embedding 函数
        cosine_sim_fn: 余弦相似度函数
        conflict_threshold: 矛盾检测阈值
        db_adapter: PG 适配器（可选，传入时将结果存入 review_queue）

    Returns:
        可疑矛盾列表：[{"new": text, "existing": {id, name}, "reason": str}]
    """
    conflict_keywords = ["不", "不是", "不能", "无效", "不再", "相反", "错误", "不要"]

    new_emb = embed_fn([new_point_text])
    if not new_emb:
        return []

    new_vec = new_emb[0]
    new_has_negation = any(kw in new_point_text for kw in conflict_keywords)

    conflicts: list[dict[str, Any]] = []
    for existing in sibling_points:
        if not existing.get("k_vector"):
            continue

        sim = cosine_sim_fn(new_vec, existing["k_vector"])
        if sim > conflict_threshold:
            existing_has_negation = any(kw in (existing.get("name", "") or "") for kw in conflict_keywords)
            if new_has_negation != existing_has_negation:
                conflict = {
                    "new_text": new_point_text,
                    "existing": existing,
                    "reason": f"语义相似度 {sim:.3f} 但关键词对立",
                }
                # 存入 review_queue，等 consolidation 处理
                if db_adapter:
                    db_adapter.insert_review(
                        new_text=new_point_text,
                        existing_node_id=existing["id"],
                        existing_text=existing.get("name", ""),
                        conflict_type="negation_conflict",
                        similarity=sim,
                    )
                conflicts.append(conflict)

    return conflicts


def local_q(
    global_embedding: list[float],
    subject_offset: list[float] | None,
    offset_coefficient: float = 0.3,
    *,
    child_count: int = 0,
    cold_start_threshold: int = 20,
) -> list[float]:
    """在科目局部空间中的 Q 投影（TaxoGen 启示）。

    用于注意力定位时，Q 向量在科目局部空间中做校正。
    冷启动期（子节点数 < cold_start_threshold）回退到全局 embedding。

    Args:
        global_embedding: 全局 bge-m3 embedding
        subject_offset: 科目的局部偏移向量（None 或无子节点时返回原始 embedding）
        offset_coefficient: 偏移系数
        child_count: 该科目的子节点数（用于冷启动判断）
        cold_start_threshold: 冷启动阈值，子节点数少于此值时回退到余弦

    Returns:
        校正后的局部 Q 向量。冷启动期返回原始全局 embedding。
    """
    # 冷启动回退：子节点不足时直接用全局 embedding（等同余弦相似度）
    if child_count < cold_start_threshold:
        return global_embedding

    if subject_offset is None:
        return global_embedding

    return [
        g + o * offset_coefficient
        for g, o in zip(global_embedding, subject_offset)
    ]


def compute_subject_offset(
    subject_embeddings: list[list[float]],
    sibling_embeddings: list[list[float]],
) -> list[float]:
    """计算科目的局部偏移向量。

    offset = centroid(subject) - centroid(siblings)
    这个偏移自动捕捉了"这个科目跟兄弟科目比，独特在什么方向"。

    Args:
        subject_embeddings: 该科目下所有子节点 embedding 列表
        sibling_embeddings: 该科目所有兄弟节点 embedding 列表

    Returns:
        局部偏移向量
    """
    if not subject_embeddings:
        return [0.0] * (len(sibling_embeddings[0]) if sibling_embeddings else 1024)

    import numpy as np

    subject_centroid = np.mean(subject_embeddings, axis=0)
    sibling_centroid = np.mean(sibling_embeddings, axis=0) if sibling_embeddings else np.zeros_like(subject_centroid)

    offset = subject_centroid - sibling_centroid
    return offset.tolist()
