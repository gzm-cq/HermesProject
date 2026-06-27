"""Knowledge Tree Plugin 公共 API — 供知识导航插件调用"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from knowledge_tree_builder import batch_embed
from knowledge_tree_plugin.recall import locate_subject, attention_filter, format_context_lines, log_use
from knowledge_tree_plugin.config import PluginConfig
from knowledge_tree_plugin.adapters.database import PluginDatabaseAdapter

logger = logging.getLogger(__name__)

# 独立的配置懒加载（不依赖 hooks 模块的私有状态，避免跨模块状态耦合）
_api_config: PluginConfig | None = None

# 公共 API 被 knowledge-navigation 的 pre_llm_call 高频调用。
# 注意: psycopg2 连接/cursor 非线程安全，不能跨线程共享。
# 每次调用创建新适配器，由调用方负责关闭。


def _get_api_config() -> PluginConfig:
    """懒加载公共 API 配置（独立于 hooks 模块）。"""
    global _api_config
    if _api_config is None:
        config_path = os.environ.get(
            "KT_PLUGIN_CONFIG",
            "/root/.hermes/plugins/knowledge-tree-plugin/config/default.yaml",
        )
        _api_config = PluginConfig.load(config_path)
    return _api_config


def _recall_core(
    session_id: str,
    user_message: str,
    cfg: PluginConfig | None = None,
    adapter: PluginDatabaseAdapter | None = None,
) -> tuple[list[dict[str, Any]], PluginDatabaseAdapter | None, bool]:
    """知识树 recall 核心流程（返回结构化结果）。

    Returns:
        (kp_results, adapter, owns_adapter)
        kp_results: attention_filter 返回的知识点列表 [{id, name, text, score}]
        adapter: DB 适配器（调用方需根据 owns_adapter 决定是否关闭）
        owns_adapter: 是否由本函数创建（调用方负责关闭）
    """
    owns_adapter = False

    try:
        cfg = cfg or _get_api_config()
        if adapter is None:
            if not cfg.db_url:
                db_url = os.environ.get("KT_DB_URL", "")
                if not db_url:
                    raise RuntimeError("KT_DB_URL 未配置")
                cfg.db_url = db_url
            adapter = PluginDatabaseAdapter(cfg.db_url)
            if adapter is None:
                raise RuntimeError("知识树 DB 连接不可用")
            owns_adapter = True
    except Exception as e:
        logger.warning("知识树 recall 初始化失败: %s", e)
        return [], None, False

    try:
        # Step 1: query embedding
        query_raw = batch_embed(
            [user_message],
            base_url=cfg.embed_base_url,
            model=cfg.embed_model,
            api_key=cfg.embed_api_key,
            batch_size=1,
        )
        if not query_raw:
            return [], adapter, owns_adapter
        query_embedding = query_raw[0]

        # Step 2: 科目定位
        subject = locate_subject(
            query=user_message,
            query_embedding=query_embedding,
            adapter=adapter,
            cold_start_threshold=cfg.cold_start_threshold,
        )
        if not subject:
            return [], adapter, owns_adapter

        children = subject.get("children", [])
        child_count = subject.get("child_count", len(children))
        cold_start = child_count < cfg.cold_start_threshold

        # Step 3: 注意力筛选
        kp_results = attention_filter(
            query_embedding=query_embedding,
            child_nodes=children,
            cold_start=cold_start,
            min_score=cfg.recall_min_score,
            max_results=cfg.max_recall_results,
            local_offset=subject.get("local_offset"),
        )

        if not kp_results:
            return [], adapter, owns_adapter

        # Step 4: 回写 use_log
        node_ids = [kp.get("id", 0) for kp in kp_results if kp.get("id")]
        try:
            log_use(adapter, session_id, node_ids, user_message)
        except Exception:
            pass

        logger.info(
            "知识树 recall 成功",
            extra={
                "session_id": session_id,
                "event": "recall_success",
                "subject_id": subject.get("id"),
                "subject_name": subject.get("name", ""),
                "kp_count": len(kp_results),
            },
        )

        return kp_results, adapter, owns_adapter

    except Exception as e:
        logger.warning(
            "知识树 recall 异常: %s", e,
            extra={"session_id": session_id, "event": "recall_error"},
        )
        if owns_adapter and adapter is not None:
            try:
                adapter.close()
            except Exception:
                pass
            adapter = None
            owns_adapter = False
        return [], adapter, owns_adapter


def recall_from_tree(
    session_id: str,
    user_message: str,
    cfg: PluginConfig | None = None,
    adapter: PluginDatabaseAdapter | None = None,
) -> str | None:
    """从知识树中召回相关知识点，返回格式化注入文本。

    Args:
        session_id: 当前会话 ID
        user_message: 用户原始消息
        cfg: 插件配置（可选，默认从环境加载）
        adapter: DB 适配器（可选，默认从配置创建；函数内部创建时自动关闭）

    Returns:
        格式化注入文本，无可匹配知识点时返回 None
    """
    t0 = time.time()
    kp_results, inner_adapter, owns_adapter = _recall_core(
        session_id, user_message, cfg, adapter
    )
    try:
        if not kp_results:
            return None
        context_lines = format_context_lines(kp_results)
        if not context_lines:
            return None
        return "\n".join(context_lines)
    finally:
        if owns_adapter and inner_adapter is not None:
            try:
                inner_adapter.close()
            except Exception:
                pass


def recall_from_tree_raw(
    session_id: str,
    user_message: str,
    cfg: PluginConfig | None = None,
    adapter: PluginDatabaseAdapter | None = None,
) -> list[dict[str, Any]]:
    """从知识树中召回相关知识点，返回结构化结果（供融合去重使用）。

    Args:
        session_id: 当前会话 ID
        user_message: 用户原始消息
        cfg: 插件配置（可选，默认从环境加载）
        adapter: DB 适配器（可选，默认从配置创建；函数内部创建时自动关闭）

    Returns:
        知识点列表 [{id, name, text, score}]，无可匹配时返回空列表
    """
    kp_results, inner_adapter, owns_adapter = _recall_core(
        session_id, user_message, cfg, adapter
    )
    if owns_adapter and inner_adapter is not None:
        try:
            inner_adapter.close()
        except Exception:
            pass
    return kp_results


def multi_hop_recall(
    seed_kp_ids: list[int],
    cfg: PluginConfig | None = None,
    adapter: PluginDatabaseAdapter | None = None,
    top_k: int = 10,
    max_hops: int = 2,
) -> list[dict[str, Any]]:
    """三路策略多跳关联召回：合并多条路径的关联结果，去重后返回。

    策略路线（参考 SAG multi 模式）：
      Route A — subject: seed KPs → 同 subject 兄弟节点
      Route B — entity:  seed KPs → kt_entity_links → 共享实体的其他 KPs
      Route C — edge:    seed KPs → knowledge_tree_edges → 预建边关联 KPs

    各路线结果合并去重，标注 strategy 来源。
    某条路线数据为空时自动跳过，不阻塞其他路线。

    Args:
        seed_kp_ids: 初始召回到的 knowledge_point tree_node_id 列表
        cfg: 插件配置
        adapter: DB 适配器（可选）
        top_k: 最多返回条数
        max_hops: 暂未使用（保留参数接口）

    Returns:
        关联知识点列表 [{id, name, text, score, strategy}]
    """
    if not seed_kp_ids:
        return []

    owns_adapter = False
    try:
        cfg = cfg or _get_api_config()
        if adapter is None:
            db_url = cfg.db_url or os.environ.get("KT_DB_URL", "")
            if not db_url:
                return []
            adapter = PluginDatabaseAdapter(db_url)
            owns_adapter = True
    except Exception as e:
        logger.warning("多跳 recall 初始化失败: %s", e)
        return []

    try:
        cursor = adapter.cursor

        # 三路策略，每路目标 top_k // 3 + 1（合并去重后再截断）
        per_route = max(3, top_k // 3 + 1)
        all_results: list[dict[str, Any]] = []

        # Route A: subject-based
        try:
            a_results = _strategy_subject(cursor, seed_kp_ids, per_route)
            for r in a_results:
                r["strategy"] = "subject"
            all_results.extend(a_results)
            if a_results:
                logger.debug("多跳 Route A (subject): %d 条", len(a_results))
        except Exception as e:
            logger.debug("多跳 Route A 跳过: %s", e)

        # Route B: entity-based
        try:
            cursor.execute("SELECT count(*) FROM kt_entity_links")
            if cursor.fetchone()[0] > 0:
                b_results = _strategy_entity(cursor, seed_kp_ids, per_route)
                for r in b_results:
                    r["strategy"] = "entity"
                all_results.extend(b_results)
                if b_results:
                    logger.debug("多跳 Route B (entity): %d 条", len(b_results))
        except Exception as e:
            logger.debug("多跳 Route B 跳过: %s", e)

        # Route C: edge-based
        try:
            c_results = _strategy_edge(cursor, seed_kp_ids, per_route)
            for r in c_results:
                r["strategy"] = "edge"
            all_results.extend(c_results)
            if c_results:
                logger.debug("多跳 Route C (edge): %d 条", len(c_results))
        except Exception as e:
            logger.debug("多跳 Route C 跳过: %s", e)

        if not all_results:
            return []

        # 合并去重：相同 id 的只保留第一条（按策略优先级 A→B→C）
        seen_ids: set[int] = set()
        merged: list[dict[str, Any]] = []
        for r in all_results:
            rid = r.get("id")
            if rid is not None and rid not in seen_ids:
                seen_ids.add(rid)
                merged.append(r)

        # 按分数降序
        merged.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
        return merged[:top_k]

    except Exception as e:
        logger.warning("多跳 recall 查询失败: %s", e)
        return []
    finally:
        if owns_adapter and adapter is not None:
            try:
                adapter.close()
            except Exception:
                pass


def _strategy_subject(
    cursor,
    seed_kp_ids: list[int],
    top_k: int,
) -> list[dict[str, Any]]:
    """Route A: subject-based — 种子 KPs 的同 subject 兄弟节点。"""
    cursor.execute(
        """
        SELECT kt.id, kt.name, kpt.text
        FROM knowledge_tree kt
        JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id
        WHERE kt.parent_id IN (
            SELECT DISTINCT parent_id FROM knowledge_tree
            WHERE id = ANY(%s) AND parent_id IS NOT NULL
        )
        AND kt.id != ALL(%s)
        AND kt.node_type = 'knowledge_point'
        LIMIT %s
        """,
        (seed_kp_ids, seed_kp_ids, top_k),
    )
    return [
        {"id": row[0], "name": row[1], "text": row[2], "score": 0.5}
        for row in cursor.fetchall()
    ]


def _strategy_entity(
    cursor,
    seed_kp_ids: list[int],
    top_k: int,
) -> list[dict[str, Any]]:
    """Route B: entity-based — 通过 kt_entity_links 共享实体展开。"""
    cursor.execute(
        "SELECT DISTINCT entity FROM kt_entity_links WHERE kp_id = ANY(%s)",
        (seed_kp_ids,),
    )
    entities = [row[0] for row in cursor.fetchall()]
    if not entities:
        return []

    cursor.execute(
        """
        SELECT kt.id, kt.name, kpt.text, COUNT(kel.entity) as shared_count
        FROM kt_entity_links kel
        JOIN knowledge_tree kt ON kt.id = kel.kp_id
        JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id
        WHERE kel.entity = ANY(%s)
          AND kt.id != ALL(%s)
          AND kt.node_type = 'knowledge_point'
        GROUP BY kt.id, kt.name, kpt.text
        ORDER BY shared_count DESC
        LIMIT %s
        """,
        (entities, seed_kp_ids, top_k),
    )
    return [
        {"id": row[0], "name": row[1], "text": row[2],
         "score": min(1.0, row[3] / 5.0)}
        for row in cursor.fetchall()
    ]


def _strategy_edge(
    cursor,
    seed_kp_ids: list[int],
    top_k: int,
) -> list[dict[str, Any]]:
    """Route C: edge-based — 通过 knowledge_tree_edges 预建边展开。"""
    cursor.execute(
        """
        SELECT kt.id, kt.name, kpt.text, e.cooccurrence_count
        FROM knowledge_tree_edges e
        JOIN knowledge_tree kt ON kt.id = e.to_node_id
        JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id
        WHERE e.from_node_id = ANY(%s)
          AND kt.id != ALL(%s)
        ORDER BY e.cooccurrence_count DESC
        LIMIT %s
        """,
        (seed_kp_ids, seed_kp_ids, top_k),
    )
    results = [
        {"id": row[0], "name": row[1], "text": row[2],
         "score": min(1.0, row[3] / 3.0)}
        for row in cursor.fetchall()
    ]

    # 如果没有预建边，退化为向量桥接：同一 subject 下高相似度 KPs
    if not results:
        cursor.execute(
            """
            SELECT kt.id, kt.name, kpt.text,
                   1 - (kt.k_vector <=> sq.kv) as sim
            FROM knowledge_tree kt
            JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id
            JOIN (SELECT k_vector as kv FROM knowledge_tree WHERE id = ANY(%s) AND k_vector IS NOT NULL LIMIT 1) sq ON true
            WHERE kt.id != ALL(%s)
              AND kt.node_type = 'knowledge_point'
              AND kt.k_vector IS NOT NULL
              AND 1 - (kt.k_vector <=> sq.kv) > 0.80
            ORDER BY sim DESC
            LIMIT %s
            """,
            (seed_kp_ids, seed_kp_ids, top_k),
        )
        results = [
            {"id": row[0], "name": row[1], "text": row[2], "score": float(row[3])}
            for row in cursor.fetchall()
        ]

    return results
