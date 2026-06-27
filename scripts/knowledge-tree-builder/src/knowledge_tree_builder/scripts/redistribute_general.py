"""重新分类 general/root 下的知识点到正确领域。

3 级漏斗策略：

| 级别 | 方法 | 预期覆盖率 | API 成本 |
|------|------|-----------|---------|
| L1 | 关键词规则匹配 | ~20% | 0 |
| L2 | 语义 cosine 匹配已有 domain centroid | ~50% | 1 次 batch_embed |
| L3 | LLM 批量判断残差 | ~30% | ~15 次 LLM 调用 |

用法:
    knowledge-tree-builder redistribute [--dry-run]
"""

from __future__ import annotations

import logging
import re
from typing import Any

from knowledge_tree_builder.adapters.database import DatabaseAdapter
from knowledge_tree_builder.core.embeddings import batch_embed, cosine_similarity
from knowledge_tree_builder.llm.client import call_llm_json

logger = logging.getLogger(__name__)

# ========== L1: 关键词规则 ==========

# 关键词 → 目标 domain 映射（权重低的排前面，避免误匹配）
_KEYWORD_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brtk-hermes\b"), "runtime"),
    (re.compile(r"\bhermes\s*atlas\b", re.I), "repowiki"),
    (re.compile(r"\bhermes\b", re.I), "repowiki"),          # Hermes 项目相关
    (re.compile(r"\brepowiki\b", re.I), "repowiki"),
    (re.compile(r"\bqoder\b", re.I), "repowiki"),
    (re.compile(r"\bruntime\b", re.I), "runtime"),
    (re.compile(r"\bcontext.engineering\b", re.I), "context-engineering"),
    (re.compile(r"\bctx.eng\b", re.I), "context-engineering"),
    (re.compile(r"\b知识导航\b"), "context-engineering"),
    (re.compile(r"\bclustering\b", re.I), "repowiki"),
    (re.compile(r"\b聚类\b"), "repowiki"),
    (re.compile(r"\b记忆清理\b"), "repowiki"),
    (re.compile(r"\bmemory.cleanup\b", re.I), "repowiki"),
    (re.compile(r"\bpgvector\b", re.I), "runtime"),
    (re.compile(r"\bpsycopg2?\b", re.I), "runtime"),
    (re.compile(r"\bsession\b", re.I), "runtime"),
    (re.compile(r"\bk.vector\b", re.I), "runtime"),
    (re.compile(r"\bdeploy\.sh\b"), "repowiki"),
    (re.compile(r"\bmanifest\b", re.I), "repowiki"),
]


def _match_keyword(text: str) -> str | None:
    """L1: 关键词规则匹配。匹配成功返回 domain 名，否则 None。"""
    for pattern, domain in _KEYWORD_RULES:
        if pattern.search(text):
            return domain
    return None


# ========== L2: 语义 cosine 匹配 ==========


def _load_domain_centroids(adapter: DatabaseAdapter) -> dict[str, list[float]]:
    """计算每个 domain 的 centroid k_vector（取 domain/root 子节点均值）。"""
    cursor = adapter.cursor
    cursor.execute(
        "SELECT kt.id, kt.name FROM knowledge_tree kt "
        "WHERE kt.parent_id IS NULL AND kt.node_type = 'subject' "
        "AND kt.name != 'general'"
    )
    domains = cursor.fetchall()

    centroids: dict[str, list[float]] = {}
    for domain_id, domain_name in domains:
        cursor.execute(
            "SELECT k_vector FROM knowledge_tree "
            "WHERE parent_id = %s AND k_vector IS NOT NULL "
            "LIMIT 200",
            (domain_id,),
        )
        rows = cursor.fetchall()
        if rows:
            import numpy as np
            vectors = [np.array(r[0], dtype=np.float32) for r in rows if r[0] is not None]
            if vectors:
                centroid = np.mean(vectors, axis=0).tolist()
                centroids[domain_name] = centroid

    return centroids


def _match_semantic(
    embedding: list[float],
    centroids: dict[str, list[float]],
    threshold: float = 0.50,
) -> str | None:
    """L2: 语义 cosine 匹配。返回最佳 domain 名，或 None。"""
    import numpy as np
    vec = np.array(embedding, dtype=np.float32)
    best_domain: str | None = None
    best_score = threshold

    for domain_name, centroid in centroids.items():
        score = cosine_similarity(vec, np.array(centroid, dtype=np.float32))
        if score > best_score:
            best_score = score
            best_domain = domain_name

    return best_domain


# ========== L3: LLM 批量判断（并行 + 超时控制）==========


def _domain_via_llm(
    texts: list[str],
    existing_domains: list[str],
    api_url: str,
    api_key: str,
    model: str,
    *,
    timeout_per_item: int = 30,
    max_workers: int = 5,
) -> list[str | None]:
    """L3: LLM 批量判断每条知识点的所属领域。

    使用 ThreadPoolExecutor 并行调用，每批 max_workers 路并发。
    每条知识点设 timeout_per_item 秒超时。

    Args:
        texts: 待判断的知识点文本列表
        existing_domains: 已有领域列表
        api_url: LLM API 地址
        api_key: API 密钥
        model: 模型名
        timeout_per_item: 每条 LLM 调用超时（秒）
        max_workers: 最大并发数

    Returns:
        [domain | None] 列表，None 表示 LLM 未给出有效判断
    """
    import concurrent.futures
    import functools

    def _judge_single(text: str) -> str | None:
        """单条判断，带超时。"""
        prompt = (
            f"判断以下知识点的知识领域。"
            f" 已有领域：{existing_domains}。"
            f" 选择最合适的，或提出新的领域名。"
            f" 如不自信可回答 general。"
            f"\n知识点：{text[:300]}"
        )
        sys_prompt = "只返回JSON：{\"domain\": \"领域路径\"}"

        # 带超时的 call_llm_json
        resp = call_llm_json(
            prompt,
            system_prompt=sys_prompt,
            temperature=0,
            retries=1,  # 减少重试次数节省时间
            api_url=api_url,
            api_key=api_key,
            model=model,
        )
        if "error" in resp:
            return None
        domain = str(resp.get("domain", "general")).strip()
        if domain == "general":
            return None
        return domain

    results: list[str | None] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_judge_single, text): i
            for i, text in enumerate(texts)
        }
        # 按提交顺序收集结果
        ordered = [(future_map[f], f) for f in future_map]
        ordered.sort(key=lambda x: x[0])
        for _, future in ordered:
            try:
                result = future.result(timeout=timeout_per_item)
                results.append(result)
            except concurrent.futures.TimeoutError:
                results.append(None)
            except Exception:
                results.append(None)

    return results


# ========== 主入口 ==========


def redistribute_general(
    adapter: DatabaseAdapter,
    *,
    dry_run: bool = False,
    llm_api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    llm_api_key: str = "",
    llm_model: str = "s-deepseek-v4-flash",
    embed_base_url: str = "https://api.siliconflow.cn/v1",
    embed_model: str = "BAAI/bge-m3",
    embed_api_key: str = "",
    batch_size: int = 20,
) -> dict[str, int]:
    """重新分类 general/root 下所有知识点。

    Args:
        adapter: PG 适配器
        dry_run: True 时只统计不写入
        llm_api_url: LLM API 地址
        llm_api_key: API 密钥
        llm_model: LLM 模型名
        embed_base_url: embedding API 地址
        embed_model: embedding 模型名
        embed_api_key: embedding API 密钥
        batch_size: embedding 批量大小

    Returns:
        {"total": 待迁移数, "migrated": 迁移成功数, "llm": LLM 判断数, "errors": 失败数}
    """
    stats: dict[str, int] = {"total": 0, "migrated": 0, "llm": 0, "errors": 0}

    # 1. 查出 general/root 节点 ID
    cursor = adapter.cursor
    cursor.execute(
        "SELECT id FROM knowledge_tree "
        "WHERE name = 'general/root' AND node_type = 'subject'"
    )
    general_row = cursor.fetchone()
    if not general_row:
        logger.info("general/root 不存在，无需迁移")
        return stats
    general_id = general_row[0]

    # 2. 查出 general/root 下的所有叶子节点
    cursor.execute(
        "SELECT kt.id, kpt.text, kt.k_vector "
        "FROM knowledge_tree kt "
        "LEFT JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id "
        "WHERE kt.parent_id = %s AND kt.node_type = 'knowledge_point'",
        (general_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        logger.info("general/root 下无知识点，无需迁移")
        return stats

    stats["total"] = len(rows)
    logger.info("general/root 下待迁移知识点: %d", stats["total"])

    # 3. 预加载已有 domain 列表和 centroid
    cursor.execute(
        "SELECT DISTINCT name FROM knowledge_tree "
        "WHERE parent_id IS NULL AND node_type = 'subject' "
        "AND name != 'general' ORDER BY name"
    )
    existing_domains = [r[0] for r in cursor.fetchall()]
    centroids = _load_domain_centroids(adapter)
    logger.info("已有 domain: %s", existing_domains)

    # 4. 3 级漏斗逐条判断
    migrated_count = 0
    llm_count = 0
    error_count = 0

    # 先做 L1+L2（关键词+语义），残差走 L3（LLM）
    l1l2_results: list[tuple[int, str | None]] = []  # (node_id, target_domain | None)
    llm_candidates: list[tuple[int, str]] = []       # (node_id, text)

    import numpy as np

    for node_id, text, k_vector in rows:
        text_str = text or ""

        # L1: 关键词规则
        domain = _match_keyword(text_str)
        if domain:
            l1l2_results.append((node_id, domain))
            continue

        # L2: 语义 cosine（需要 k_vector）
        if k_vector is not None and centroids:
            domain = _match_semantic(k_vector, centroids)
            if domain:
                l1l2_results.append((node_id, domain))
                continue

        # L1+L2 未命中 → 留给 L3 LLM
        llm_candidates.append((node_id, text_str))

    # L3: LLM 批量判断残差
    if llm_candidates and not dry_run:
        domains_from_llm = _domain_via_llm(
            [t for _, t in llm_candidates],
            existing_domains,
            llm_api_url, llm_api_key, llm_model,
        )
        llm_count = sum(1 for d in domains_from_llm if d is not None)
        for (node_id, _), domain in zip(llm_candidates, domains_from_llm):
            if domain:
                l1l2_results.append((node_id, domain))

    # 5. 执行迁移
    if dry_run:
        # 统计目标 domain 分布
        domain_dist: dict[str, int] = {}
        for _, domain in l1l2_results:
            domain_dist[domain] = domain_dist.get(domain, 0) + 1
        if llm_candidates:
            domain_dist["(LLM 待判断)"] = len(llm_candidates)

        print(f"   📊 迁移预览:")
        for domain, count in sorted(domain_dist.items(), key=lambda x: -x[1]):
            print(f"      {domain}: {count} 条")
        print(f"      (保留 general: {stats['total'] - sum(domain_dist.values())} 条)")
        stats["migrated"] = len(l1l2_results)
        return stats

    for node_id, target_domain in l1l2_results:
        try:
            # 查找或创建目标 domain 的 subject 节点
            cursor.execute(
                "SELECT id FROM knowledge_tree "
                "WHERE name = %s AND parent_id IS NULL AND node_type = 'subject'",
                (f"{target_domain}/root",),
            )
            target = cursor.fetchone()
            if target:
                target_id = target[0]
            else:
                # 创建新的 domain subject
                cursor.execute(
                    "INSERT INTO knowledge_tree (name, node_type, parent_id, display_order) "
                    "VALUES (%s, 'subject', NULL, 0) RETURNING id",
                    (f"{target_domain}/root",),
                )
                target_id = cursor.fetchone()[0]

            # 迁移节点：更新 parent_id
            cursor.execute(
                "UPDATE knowledge_tree SET parent_id = %s WHERE id = %s",
                (target_id, node_id),
            )
            migrated_count += 1

        except Exception as e:
            logger.warning("迁移节点 %d 到 %s 失败: %s", node_id, target_domain, e)
            error_count += 1

    adapter.conn.commit()

    stats["migrated"] = migrated_count
    stats["llm"] = llm_count
    stats["errors"] = error_count
    logger.info("迁移完成: %d/%d (LLM=%d, errors=%d)",
                migrated_count, stats["total"], llm_count, error_count)
    return stats
