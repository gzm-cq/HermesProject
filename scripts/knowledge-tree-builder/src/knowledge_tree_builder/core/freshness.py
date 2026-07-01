"""Embedding 新鲜度检查 — 检测 text 更新后 k_vector 需要同步更新的节点"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from knowledge_tree_builder.adapters.database import DatabaseAdapter

logger = logging.getLogger(__name__)


def compute_text_hash(text: str) -> str:
    """对 text 计算 MD5 hash，用于快速比较 text 是否变化。

    Args:
        text: 知识点文本

    Returns:
        32 位 MD5 十六进制字符串
    """
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def check_freshness(adapter: "DatabaseAdapter") -> list[tuple[int, str]]:
    """查询找出 text 更新了但 k_vector 需要更新的节点。

    逻辑：
    1. 如果 knowledge_tree 表没有 last_text_hash 列，返回空列表
    2. 一次 JOIN 查询同时拿到 text 和 last_text_hash
    3. 比对当前 text 的 hash 与记录的 last_text_hash

    Args:
        adapter: DatabaseAdapter 实例

    Returns:
        [(node_id, text), ...] 需要重新 embedding 的节点列表
    """
    stale: list[tuple[int, str]] = []

    try:
        adapter.cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'knowledge_tree' AND column_name = 'last_text_hash'
            """
        )
        if not adapter.cursor.fetchone():
            logger.debug("knowledge_tree 表无 last_text_hash 列，跳过新鲜度检查")
            return stale
    except Exception as e:
        logger.warning("新鲜度检查列存在性查询失败: %s", e)
        return stale

    try:
        adapter.cursor.execute(
            """
            SELECT kt.id, kpt.text, kt.last_text_hash
            FROM knowledge_tree kt
            JOIN knowledge_point_texts kpt ON kt.id = kpt.tree_node_id
            WHERE kt.node_type = 'knowledge_point'
              AND kt.k_vector IS NOT NULL
            ORDER BY kt.id
            """
        )
        rows = adapter.cursor.fetchall()
    except Exception as e:
        logger.warning("新鲜度检查查询失败: %s", e)
        return stale

    for row in rows:
        node_id = int(row[0])
        current_text = row[1] or ""
        stored_hash = row[2]
        current_hash = compute_text_hash(current_text)

        if stored_hash is None or stored_hash != current_hash:
            stale.append((node_id, current_text))

    return stale


def batch_update_text_hash(
    adapter: "DatabaseAdapter",
    items: list[tuple[int, str]],
) -> int:
    """批量更新节点的 last_text_hash 字段。

    Args:
        adapter: DatabaseAdapter 实例
        items: [(node_id, text_hash), ...] 要更新的节点列表

    Returns:
        成功更新的节点数
    """
    if not items:
        return 0

    updated = 0
    try:
        for node_id, text_hash in items:
            adapter.cursor.execute(
                "UPDATE knowledge_tree SET last_text_hash = %s WHERE id = %s",
                (text_hash, node_id),
            )
            updated += 1
        adapter.conn.commit()
    except Exception as e:
        logger.warning("批量更新 last_text_hash 失败: %s", e)
        adapter.conn.rollback()
        return 0
    return updated


def update_text_hash(adapter: "DatabaseAdapter", node_id: int, text_hash: str) -> None:
    """更新单个节点的 last_text_hash 字段（兼容旧接口，内部走批量）。

    Args:
        adapter: DatabaseAdapter 实例
        node_id: 知识树节点 ID
        text_hash: text 的 MD5 hash
    """
    batch_update_text_hash(adapter, [(node_id, text_hash)])


def ensure_last_text_hash_column(adapter: "DatabaseAdapter") -> bool:
    """确保 knowledge_tree 表有 last_text_hash 列（幂等执行）。

    使用 ALTER TABLE ADD COLUMN IF NOT EXISTS 兼容 PostgreSQL 10+。
    如果是新建列，自动回填现有节点的 text hash，避免首次运行全量误判 stale。

    Args:
        adapter: DatabaseAdapter 实例

    Returns:
        True 表示列已存在或新建成功，False 表示失败
    """
    try:
        adapter.cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'knowledge_tree' AND column_name = 'last_text_hash'
            """
        )
        col_exists = adapter.cursor.fetchone() is not None
    except Exception as e:
        logger.warning("查询 last_text_hash 列是否存在失败: %s", e)
        return False

    was_new = False
    if not col_exists:
        try:
            adapter.cursor.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'knowledge_tree' AND column_name = 'last_text_hash'
                    ) THEN
                        ALTER TABLE knowledge_tree ADD COLUMN last_text_hash VARCHAR(32);
                    END IF;
                END $$;
                """
            )
            adapter.conn.commit()
            was_new = True
        except Exception as e:
            logger.warning("添加 last_text_hash 列失败: %s", e)
            adapter.conn.rollback()
            return False

    if was_new:
        backfilled = backfill_all_text_hashes(adapter)
        if backfilled > 0:
            logger.info("新建 last_text_hash 列后已回填 %d 个节点的 text hash", backfilled)

    return True


def backfill_all_text_hashes(adapter: "DatabaseAdapter") -> int:
    """回填所有 knowledge_point 节点的 last_text_hash。

    首次添加列时调用，避免首次运行全量误判为 stale。

    Args:
        adapter: DatabaseAdapter 实例

    Returns:
        回填的节点数
    """
    try:
        adapter.cursor.execute(
            """
            SELECT kt.id, kpt.text
            FROM knowledge_tree kt
            JOIN knowledge_point_texts kpt ON kt.id = kpt.tree_node_id
            WHERE kt.node_type = 'knowledge_point'
              AND kt.last_text_hash IS NULL
            ORDER BY kt.id
            """
        )
        rows = adapter.cursor.fetchall()
    except Exception as e:
        logger.warning("回填 text hash 查询失败: %s", e)
        return 0

    items: list[tuple[int, str]] = []
    for row in rows:
        node_id = int(row[0])
        text = row[1] or ""
        text_hash = compute_text_hash(text)
        items.append((node_id, text_hash))

    return batch_update_text_hash(adapter, items)
