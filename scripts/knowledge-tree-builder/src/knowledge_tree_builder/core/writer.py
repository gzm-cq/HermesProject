"""Step 5: 树写入 PG + 去重 + 矛盾检测"""

from __future__ import annotations

import logging
from typing import Any

from knowledge_tree_builder.adapters.database import DatabaseAdapter

logger = logging.getLogger(__name__)


def write_tree(
    tree: list[dict[str, Any]],
    adapter: DatabaseAdapter,
    source_article_id: int | None = None,
    *,
    log_use: bool = False,
) -> dict[str, int]:
    """将命名后的树写入 PG。

    采用 DFS 遍历：先插入父节点，再递归插入子节点。

    Args:
        tree: 命名后的树结构列表（每个节点有 "name", "type", "children"/"points"）
        adapter: PG 适配器
        source_article_id: 来源文章 ID（可选）

    Returns:
        dict: 写入统计（nodes, points, errors）
    """
    stats: dict[str, Any] = {"nodes": 0, "points": 0, "errors": 0, "node_ids": []}

    for node in tree:
        _write_node(node, adapter, None, 0, source_article_id, stats)

    # 记录使用事件（使用真实 DB node ID）
    if log_use and stats["node_ids"]:
        try:
            adapter.log_use("tree_build", stats["node_ids"], "batch_build")
        except Exception as exc:
            logger.warning("log_use 记录失败: %s", exc)

    return stats


def _write_node(
    node: dict[str, Any],
    adapter: DatabaseAdapter,
    parent_id: int | None,
    display_order: int,
    source_article_id: int | None,
    stats: dict[str, Any],
) -> int | None:
    """递归写入单个节点。"""
    try:
        node_type = _map_node_type(node)

        node_id = adapter.insert_node(
            name=node.get("name", "未命名"),
            node_type=node_type,
            parent_id=parent_id,
            display_order=display_order,
            source_ids=[source_article_id] if source_article_id else None,
        )
        stats["nodes"] += 1
        if node_id is not None:
            stats["node_ids"].append(node_id)

        children = node.get("children", [])
        points = node.get("points", [])

        if children:
            # 非叶子节点：递归写入子节点
            for i, child in enumerate(children):
                _write_node(child, adapter, node_id, i, source_article_id, stats)
        elif points:
            # 叶子节点：写入知识点原文
            for pt in points:
                adapter.insert_point_text(
                    tree_node_id=node_id,
                    text=pt,
                    source_id=source_article_id,
                )
                stats["points"] += 1

        return node_id

    except Exception as exc:
        stats["errors"] += 1
        return None


def _map_node_type(node: dict[str, Any]) -> str:
    """将内部节点类型映射到 PG 的 node_type。"""
    internal_type = node.get("type", "leaf")
    children = node.get("children", [])

    if children:
        return "subject"
    if internal_type == "leaf":
        return "knowledge_point"
    return "subject"


def write_tree_with_dedup(
    new_points: list[str],
    adapter: DatabaseAdapter,
    source_article_id: int | None = None,
    *,
    embed_fn=None,
    cosine_similarity_fn=None,
    dedup_threshold: float = 0.95,
) -> dict[str, Any]:
    """增量写入新知识点（带去重）。

    检查新知识点与已有叶子节点是否语义重复。
    重复则不创建新节点，只合并 source_ids。

    Args:
        new_points: 新增知识点文本列表
        adapter: PG 适配器
        source_article_id: 来源文章 ID
        embed_fn: embedding 函数 (texts) → [embeddings]，提供时启用向量去重
        cosine_similarity_fn: 余弦相似度计算函数 (a, b) → float
        dedup_threshold: 去重阈值

    Returns:
        dict: {"new_nodes": int, "merged_ids": list[int], "errors": int}
    """
    result: dict[str, Any] = {"new_nodes": 0, "merged_ids": [], "errors": 0}

    if cosine_similarity_fn is None or embed_fn is None:
        # 没有 embedding 时走简单文本去重
        for pt in new_points:
            existing = adapter.search_point_texts(pt)
            if existing:
                # 文本前半部分匹配视为重复
                result["merged_ids"].append(existing[0]["tree_node_id"])
                if source_article_id:
                    adapter.update_source_ids(existing[0]["tree_node_id"], source_article_id)
            else:
                _insert_single_point(pt, adapter, source_article_id, result)
        return result

    # 有 embedding 时走余弦相似度去重
    leaf_nodes = adapter.get_leaf_nodes()
    for pt in new_points:
        is_dup = False
        pt_emb = embed_fn([pt])
        if not pt_emb:
            _insert_single_point(pt, adapter, source_article_id, result)
            continue
        new_vec = pt_emb[0]
        for leaf in leaf_nodes:
            if leaf.get("k_vector"):
                sim = cosine_similarity_fn(new_vec, leaf["k_vector"])
                if sim > dedup_threshold:
                    result["merged_ids"].append(leaf["id"])
                    if source_article_id:
                        adapter.update_source_ids(leaf["id"], source_article_id)
                    is_dup = True
                    break
        if not is_dup:
            _insert_single_point(pt, adapter, source_article_id, result)

    return result


def _insert_single_point(
    text: str,
    adapter: DatabaseAdapter,
    source_article_id: int | None,
    result: dict[str, Any],
) -> None:
    """插入单条知识点作为独立叶子节点"""
    try:
        node_id = adapter.insert_node(
            name=text[:30],
            node_type="knowledge_point",
            display_order=0,
            source_ids=[source_article_id] if source_article_id else None,
        )
        adapter.insert_point_text(tree_node_id=node_id, text=text, source_id=source_article_id)
        result["new_nodes"] += 1
    except Exception as exc:
        logger.warning("插入知识点失败: %s", exc)
        result["errors"] += 1
