"""Knowledge Tree Plugin 公共 API — 供知识导航插件调用"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from knowledge_tree_builder import batch_embed
from knowledge_tree_plugin.recall import locate_subject, attention_filter, format_context_lines, log_use, temporal_filter
from knowledge_tree_plugin.config import PluginConfig
from knowledge_tree_plugin.adapters.database import PluginDatabaseAdapter

logger = logging.getLogger(__name__)

# 独立的配置懒加载（不依赖 hooks 模块的私有状态，避免跨模块状态耦合）
_api_config: PluginConfig | None = None

# Thread-local adapter 池：每个线程复用 PG 连接，避免每次调用新建/关闭
# psycopg2 connection 非线程安全，thread-local 确保每线程独立连接
_adapter_local = threading.local()
_ADAPTER_TTL = 300  # 5 分钟无使用自动关闭重建


def _get_thread_adapter(db_url: str) -> PluginDatabaseAdapter | None:
    """从 thread-local 获取或创建 adapter，按 TTL 自动刷新。"""
    if not hasattr(_adapter_local, "entries"):
        _adapter_local.entries = {}  # type: ignore[attr-defined]

    entries = _adapter_local.entries  # type: ignore[attr-defined]
    now = time.time()

    # 清理过期 adapter
    stale = [k for k, (_, ts) in entries.items() if now - ts > _ADAPTER_TTL]
    for k in stale:
        try:
            entries[k][0].close()
        except Exception:
            pass
        del entries[k]

    # 返回缓存或新建
    if db_url in entries:
        adapter, _ = entries[db_url]
        try:
            # 健康检查
            adapter.cursor.execute("SELECT 1")
            entries[db_url] = (adapter, now)
            return adapter
        except Exception:
            try:
                adapter.close()
            except Exception:
                pass
            del entries[db_url]

    try:
        adapter = PluginDatabaseAdapter(db_url)
        entries[db_url] = (adapter, now)
        return adapter
    except Exception as e:
        logger.warning("thread adapter 创建失败: %s", e)
        return None


def _invalidate_thread_adapter(db_url: str) -> None:
    """失效当前线程的 adapter（出错时调用）。"""
    if hasattr(_adapter_local, "entries"):
        entries = _adapter_local.entries  # type: ignore[attr-defined]
        if db_url in entries:
            try:
                entries[db_url][0].close()
            except Exception:
                pass
            del entries[db_url]


def _get_api_config() -> PluginConfig:
    """懒加载公共 API 配置（独立于 hooks 模块）。"""
    global _api_config
    if _api_config is None:
        config_path = os.environ.get(
            "KT_PLUGIN_CONFIG",
            os.path.expanduser("~/.hermes/plugins/knowledge-tree-plugin/config/default.yaml"),
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
        adapter: DB 适配器
        owns_adapter: 是否由本函数创建（thread-local 池管理，调用方无需关闭）
    """
    owns_adapter = False
    db_url_used = ""

    try:
        cfg = cfg or _get_api_config()
        if adapter is None:
            db_url = cfg.db_url or os.environ.get("KT_DB_URL", "")
            if not db_url:
                raise RuntimeError("KT_DB_URL 未配置")
            cfg.db_url = db_url
            db_url_used = db_url
            # 使用 thread-local 池，adapter 由池管理
            adapter = _get_thread_adapter(db_url)
            if adapter is None:
                raise RuntimeError("知识树 DB 连接不可用")
            # owns_adapter 保持 False：调用方无需关闭 pooled adapter
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

        # Step 3.5: 时态过滤（P3-9）
        if cfg.enable_temporal_filter:
            kp_results = temporal_filter(
                kp_results,
                user_message,
                demote_factor=cfg.temporal_filter_demote_factor,
            )
            if not kp_results:
                return [], adapter, owns_adapter

        # Step 3.6: 跨域多跳扩展（主流程内建，不依赖调用方二次展开）
        # 修复背景（2026-08-30）：attention_filter 只做「科目内注意力召回」，
        # 跨科关联此前完全依赖调用方（KN _expand_multi_hop）二次调用才出现，
        # 导致 KT 插件单独调用时零跨域发现。下沉到主流程后，
        # recall_from_tree_raw 的返回结果自带跨域 KP，调用方按需去重。
        if cfg.enable_multi_hop_expand:
            try:
                _seed_ids = [
                    int(kp["id"]) for kp in kp_results
                    if kp.get("id") is not None and str(kp["id"]).isdigit()
                ]
                if _seed_ids:
                    _mh = multi_hop_recall(
                        _seed_ids, cfg=cfg, adapter=adapter,
                        top_k=cfg.multi_hop_top_k,
                    )
                    if _mh:
                        _existing_ids = {kp.get("id") for kp in kp_results}
                        _added = 0
                        for r in _mh:
                            if r.get("id") not in _existing_ids:
                                r["source"] = "multi-hop"
                                kp_results.append(r)
                                _existing_ids.add(r.get("id"))
                                _added += 1
                        logger.debug(
                            "跨域多跳扩展: +%d 条", _added,
                            extra={"session_id": session_id, "event": "cross_domain_expand", "count": _added},
                        )
            except Exception as e:
                logger.debug("跨域多跳扩展跳过: %s", e)

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
        # 异常时失效 pooled adapter，下次调用会重建
        if db_url_used:
            _invalidate_thread_adapter(db_url_used)
        return [], None, False


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
    kp_results, _inner_adapter, _owns_adapter = _recall_core(
        session_id, user_message, cfg, adapter
    )
    if not kp_results:
        return None
    context_lines = format_context_lines(kp_results)
    if not context_lines:
        return None
    return "\n".join(context_lines)


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
    kp_results, _inner_adapter, _owns_adapter = _recall_core(
        session_id, user_message, cfg, adapter
    )
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

    db_url_used = ""
    try:
        cfg = cfg or _get_api_config()
        if adapter is None:
            db_url = cfg.db_url or os.environ.get("KT_DB_URL", "")
            if not db_url:
                return []
            db_url_used = db_url
            adapter = _get_thread_adapter(db_url)
            if adapter is None:
                return []
    except Exception as e:
        logger.warning("多跳 recall 初始化失败: %s", e)
        return []

    try:
        # 三路策略，每路目标 top_k // 3 + 1（合并去重后再截断）
        per_route = max(3, top_k // 3 + 1)
        all_results: list[dict[str, Any]] = []

        # Route A: subject-based
        try:
            a_results = adapter.multi_hop_by_subject(seed_kp_ids, per_route)
            for r in a_results:
                r["strategy"] = "subject"
            all_results.extend(a_results)
            if a_results:
                logger.debug("多跳 Route A (subject): %d 条", len(a_results))
        except Exception as e:
            logger.debug("多跳 Route A 跳过: %s", e)

        # Route B: entity-based
        try:
            if adapter.has_entity_links():
                b_results = adapter.multi_hop_by_entity(seed_kp_ids, per_route)
                for r in b_results:
                    r["strategy"] = "entity"
                all_results.extend(b_results)
                if b_results:
                    logger.debug("多跳 Route B (entity): %d 条", len(b_results))
        except Exception as e:
            logger.debug("多跳 Route B 跳过: %s", e)

        # Route C: edge-based
        try:
            c_results = adapter.multi_hop_by_edge(seed_kp_ids, per_route)
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
        # 异常时失效 pooled adapter，下次调用会重建
        if db_url_used:
            _invalidate_thread_adapter(db_url_used)
        return []
