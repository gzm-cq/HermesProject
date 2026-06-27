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

from knowledge_tree_builder.adapters.database import DatabaseAdapter
from knowledge_tree_builder.core.embeddings import batch_embed

logger = logging.getLogger(__name__)


def backfill_k_vectors(
    adapter: DatabaseAdapter,
    *,
    dry_run: bool = False,
    batch_size: int = 20,
    embed_base_url: str = "https://api.siliconflow.cn/v1",
    embed_model: str = "BAAI/bge-m3",
    embed_api_key: str = "",
) -> dict[str, int]:
    """批量回填 k_vector。

    Args:
        adapter: PG 适配器
        dry_run: True 时只统计不写入
        batch_size: embedding 批量大小
        embed_base_url: embedding API 地址
        embed_model: embedding 模型名
        embed_api_key: API 密钥

    Returns:
        {"total": 待处理数, "filled": 成功回填数, "errors": 失败数}
    """
    stats: dict[str, int] = {"total": 0, "filled": 0, "errors": 0}

    # 1. 查出所有 k_vector IS NULL 的叶子节点（DISTINCT ON 去重多 text 节点）
    cursor = adapter.cursor
    cursor.execute(
        "SELECT DISTINCT ON (kt.id) kt.id, kpt.text "
        "FROM knowledge_tree kt "
        "JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id "
        "WHERE kt.node_type = 'knowledge_point' "
        "  AND kt.k_vector IS NULL "
        "ORDER BY kt.id, kpt.id"
    )
    rows = cursor.fetchall()

    # 2. 查出所有 k_vector IS NULL 的 subject 节点（用 name 做 text 回填）
    cursor.execute(
        "SELECT DISTINCT ON (kt.id) kt.id, kt.name "
        "FROM knowledge_tree kt "
        "WHERE kt.node_type = 'subject' "
        "  AND kt.k_vector IS NULL "
        "ORDER BY kt.id"
    )
    subject_rows = cursor.fetchall()
    all_rows = list(rows) + [(sid, name) for sid, name in subject_rows]
    if not all_rows:
        logger.info("没有需要回填的节点")
        return stats

    stats["total"] = len(all_rows)
    logger.info("需要回填 k_vector 的节点数: %d（知识点点: %d, 科目: %d）",
                stats["total"], len(rows), len(subject_rows))

    if dry_run:
        logger.info("[dry-run] 预览: %d 个节点待回填", stats["total"])
        return stats

    # 2. 分批处理
    for i in range(0, len(all_rows), batch_size):
        batch = all_rows[i : i + batch_size]
        node_ids = [r[0] for r in batch]
        texts = [r[1] for r in batch]

        try:
            embeddings = batch_embed(
                texts,
                base_url=embed_base_url,
                model=embed_model,
                api_key=embed_api_key,
                batch_size=batch_size,
            )
            if not embeddings:
                logger.warning("批次 %d~%d embedding 全部失败，跳过", i, i + len(batch))
                stats["errors"] += len(batch)
                continue

            if len(embeddings) != len(texts):
                logger.warning(
                    "批次 %d~%d embedding 数量不匹配: 期望 %d, 实际 %d, 跳过",
                    i, i + len(batch), len(texts), len(embeddings),
                )
                stats["errors"] += len(batch)
                continue

            for j, node_id in enumerate(node_ids):
                if j < len(embeddings) and embeddings[j] is not None:
                    adapter.update_k_vector(
                        node_id=node_id,
                        k_vector=embeddings[j],
                        placement_count=0,
                    )
                    stats["filled"] += 1
                else:
                    stats["errors"] += 1

            batch_num = i // batch_size + 1
            total_batches = (len(rows) + batch_size - 1) // batch_size
            if batch_num % 5 == 0 or batch_num == total_batches:
                logger.info("进度: %d/%d (filled=%d, errors=%d)",
                            min(i + len(batch), len(rows)),
                            stats["total"], stats["filled"], stats["errors"])

        except Exception as e:
            logger.warning("批次 %d~%d 处理失败: %s", i, i + len(batch), e)
            stats["errors"] += len(batch)

    logger.info("回填完成: total=%d, filled=%d, errors=%d",
                stats["total"], stats["filled"], stats["errors"])
    return stats
