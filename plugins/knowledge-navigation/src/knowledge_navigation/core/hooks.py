"""知识导航 Hook — 兼容性 Shim。

原 hooks.py（1713 行）已按职责拆分为 hooks/ 子包：
- hooks/db.py:     PG 连接管理、embedding 和因果链 boost
- hooks/cache.py:  缓存类（Compaction/Hit/TaskTracker）、KT 懒加载、注入 LRU、eval 日志
- hooks/router.py: Router 决策、四路 recall、后处理、pre_llm_call 入口

本文件仅为向后兼容 shim，所有导入均转发至 hooks/ 子包。
"""

from knowledge_navigation.core.hooks import *  # noqa: F401, F403 — re-export from subpackage

__all__ = [
    "pre_llm_call",
    # db
    "_get_cached_conn",
    "_batch_embed",
    "_causal_boost",
    "_pg_conn_local",
    "_PG_CONN_TTL",
    # cache
    "_CompactionTracker",
    "_HitCounter",
    "_TaskTracker",
    "_compaction",
    "_hit_counter",
    "_task_tracker",
    "_ensure_kt_imported",
    "_recall_knowledge_tree",
    "_recall_knowledge_tree_raw",
    "_multi_hop_recall",
    "HAS_KNOWLEDGE_TREE",
    "_recall_executor",
    "_injected_ids",
    "_injected_session_ts",
    "_injected_lock",
    "_touch_injected_session",
    "_eval_logger",
    "_eval_queries",
    "_get_eval_logger",
    "_load_eval_queries",
    "_use_logger",
    "_get_use_logger",
    # router
    "_extract_keywords",
    "_normalize_eval_text",
    "_match_eval_query",
    "_build_mentioned_at_map",
    "_do_hindsight_recall",
    "_do_kt_recall",
    "_do_skill_match",
    "_do_sag_recall",
    "_normalize_kt_score",
    "_build_knowledge_tree_candidate",
    "_candidate_score",
    "_pass_gates",
    "_get_router_mask",
    "_execute_recall",
    "_dedup_and_budget",
    "_expand_multi_hop",
    "_assemble_xml_output",
    "_post_process_recall",
]