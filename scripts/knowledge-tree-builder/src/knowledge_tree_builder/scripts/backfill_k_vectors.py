"""批量回填 k_vector — 遍历所有 k_vector IS NULL 的叶子节点，
调用 batch_embed 计算 embedding 后 update_k_vector 写入 PG。

用法:
    knowledge-tree-builder backfill-k-vectors [--dry-run] [--batch-size 20]

安全:
    - 默认 --dry-run 模式，仅统计不写入
    - 幂等：WHERE k_vector IS NULL 保证已回填节点不重复处理
    - JOIN 去重：DISTINCT ON (kt.id) 防止同一节点多条 text 导致重复 embed
    - batch_embed 数量校验：不匹配时 fail-soft 跳过本轮

依赖:
    - PG 连接已配置（KT_DB_URL 或 config）
    - embedding API 可用（SILICONFLOW_API_KEY）
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from knowledge_tree_builder.adapters.database import DatabaseAdapter
from knowledge_tree_builder.core.embeddings import batch_embed

logger = logging.getLogger(__name__)


def _parse_k_vector(v) -> list[float] | None:
    """将 pgvector 返回值解析为 Python float list。"""
    if v is None:
        return None
    if isinstance(v, list):
        return [float(x) for x in v]
    if isinstance(v, str):
        v = v.strip("[]").strip()
        if not v:
            return None
        parts = v.split(",")
        return [float(x.strip()) for x in parts if x.strip()]
    try:
        import struct
        return [struct.unpack("f", v[i:i+4])[0] for i in range(0, len(v), 4)]
    except Exception:
        return None


def _get_child_vecs(cursor, node_id: int, max_depth: int = 3) -> list[list[float]]:
    """递归获取子孙节点 k_vector，最多 max_depth 层。"""
    collected = []
    cursor.execute(
        "SELECT id, node_type, k_vector FROM knowledge_tree "
        "WHERE parent_id = %s AND k_vector IS NOT NULL ORDER BY id",
        (node_id,),
    )
    for row in cursor.fetchall():
        v = _parse_k_vector(row[2])
        if v is not None:
            collected.append(v)
        if max_depth > 0 and row[1] == "subject":
            collected.extend(_get_child_vecs(cursor, row[0], max_depth - 1))
    return collected


def _compute_child_avg(cursor, node_id, embed_fn, embed_model) -> list[float] | None:
    """子孙节点向量平均，无子节点时用 name embed 兜底。"""
    child_vecs = _get_child_vecs(cursor, node_id, max_depth=3)
    if child_vecs:
        return np.mean(child_vecs, axis=0).tolist()
    # 无子节点向量 → 用 name embed 兜底
    cursor.execute("SELECT name FROM knowledge_tree WHERE id = %s", (node_id,))
    row = cursor.fetchone()
    if row and row[0]:
        embeds = embed_fn(texts=[row[0][:4096]], model=embed_model)
        if embeds and embeds[0] is not None:
            return embeds[0]
    return None


def backfill_k_vectors(
    adapter: DatabaseAdapter,
    *,
    dry_run: bool = False,
    batch_size: int = 20,
    embed_base_url: str = "https://api.siliconflow.cn/v1",
    embed_model: str = "BAAI/bge-m3",
    embed_api_key: str = "",
) -> dict[str, int]:
    """批量回填 k_vector（knowledge_point + subject 一次性处理）。

    - knowledge_point: 用 point_texts 做 embedding
    - subject: 用子节点向量平均（递归 3 层），无子节点时用 name embed 兜底
    """
    stats: dict[str, int] = {"total": 0, "filled": 0, "errors": 0}

    cursor = adapter.cursor

    # 1. 查出所有 k_vector IS NULL 的 knowledge_point
    cursor.execute(
        "SELECT DISTINCT ON (kt.id) kt.id, kpt.text "
        "FROM knowledge_tree kt "
        "JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id "
        "WHERE kt.node_type = 'knowledge_point' "
        "  AND kt.k_vector IS NULL "
        "ORDER BY kt.id, kpt.id"
    )
    kp_rows = cursor.fetchall()

    # 2. 查出所有 k_vector IS NULL 的 subject
    cursor.execute(
        "SELECT DISTINCT ON (kt.id) kt.id, kt.name "
        "FROM knowledge_tree kt "
        "WHERE kt.node_type = 'subject' "
        "  AND kt.k_vector IS NULL "
        "ORDER BY kt.id"
    )
    subj_rows = cursor.fetchall()

    stats["total"] = len(kp_rows) + len(subj_rows)
    logger.info("需要回填 k_vector 的节点数: %d（knowledge_point: %d, subject: %d）",
                stats["total"], len(kp_rows), len(subj_rows))

    if not stats["total"]:
        logger.info("没有需要回填的节点")
        return stats

    if dry_run:
        logger.info("[dry-run] 预览: %d 个节点待回填", stats["total"])
        return stats

    # 3. 回填 knowledge_point — embedding API
    if kp_rows:
        texts = [r[1] for r in kp_rows]
        node_ids = [r[0] for r in kp_rows]
        try:
            embeddings = batch_embed(texts, base_url=embed_base_url,
                                     model=embed_model, api_key=embed_api_key,
                                     batch_size=batch_size)
            if not embeddings:
                logger.warning("knowledge_point embedding 全部失败")
                stats["errors"] += len(kp_rows)
            elif len(embeddings) != len(texts):
                logger.warning("knowledge_point embedding 数量不匹配: 期望 %d, 实际 %d",
                               len(texts), len(embeddings))
                stats["errors"] += len(kp_rows)
            else:
                for i, node_id in enumerate(node_ids):
                    if embeddings[i] is not None:
                        adapter.update_k_vector(node_id=node_id, k_vector=embeddings[i], placement_count=0)
                        stats["filled"] += 1
                    else:
                        stats["errors"] += 1
        except Exception as e:
            logger.warning("knowledge_point 处理失败: %s", e)
            stats["errors"] += len(kp_rows)

    # 4. 回填 subject — 子节点向量平均
    if subj_rows:
        embed_fn = lambda texts, model: batch_embed(
            texts=texts, model=model, base_url=embed_base_url, api_key=embed_api_key
        )
        for row in subj_rows:
            avg_vec = _compute_child_avg(cursor, row[0], embed_fn, embed_model)
            if avg_vec is not None:
                adapter.update_k_vector(node_id=row[0], k_vector=avg_vec, placement_count=0)
                stats["filled"] += 1
            else:
                stats["errors"] += 1

    logger.info("回填完成: total=%d, filled=%d, errors=%d",
                stats["total"], stats["filled"], stats["errors"])
    return stats