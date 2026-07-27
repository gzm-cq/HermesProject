"""知识导航 Hook — 缓存与状态管理。

拆分自 hooks.py 的缓存相关代码：
- _CompactionTracker, _HitCounter, _TaskTracker
- 知识树懒加载（KT_IMPORT_TRIED / HAS_KNOWLEDGE_TREE）
- 注入 LRU 去重（_injected_ids / _touch_injected_session）
- 评测日志（_eval_logger / _load_eval_queries）
- 使用日志（_use_logger / _get_use_logger）
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter, defaultdict
from collections import OrderedDict as _OrderedDict
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from typing import Any

from knowledge_navigation.config import CONFIG, JSONFormatter
from knowledge_navigation.core.use_log import UseLogger

logger = logging.getLogger(__name__)

__all__ = [
    "_CompactionTracker",
    "_HitCounter",
    "_TaskTracker",
    "_compaction",
    "_compaction_tracker",
    "_ensure_kt_imported",
    "_eval_logger",
    "_eval_queries",
    "_get_eval_logger",
    "_get_use_logger",
    "_hit_counter",
    "_injected_ids",
    "_injected_lock",
    "_injected_session_ts",
    "_INJECTED_LRU_MAX",
    "_INJECTED_SESSION_HARD_CAP",
    "_INJECTED_SESSION_TTL",
    "_load_eval_queries",
    "_multi_hop_recall",
    "_recall_executor",
    "_recall_knowledge_tree",
    "_recall_knowledge_tree_raw",
    "_task_tracker",
    "_touch_injected_session",
    "_use_logger",
    "HAS_KNOWLEDGE_TREE",
]

# ========== 知识树集成（通过 knowledge-tree-plugin 公共 API）==========
_KT_IMPORT_TRIED = False
_KT_MODULE = None


def _ensure_kt_imported():
    """延迟导入 knowledge_tree_plugin，只在首次调用时执行。

    避免模块顶层 import 因插件加载顺序问题导致 ImportError。
    成功后缓存模块引用，后续调用零开销。
    """
    global _KT_IMPORT_TRIED, _KT_MODULE, HAS_KNOWLEDGE_TREE
    if _KT_IMPORT_TRIED:
        return _KT_MODULE is not None
    _KT_IMPORT_TRIED = True
    try:
        from knowledge_tree_plugin.public_api import (
            recall_from_tree as _kt_recall,
            recall_from_tree_raw as _kt_recall_raw,
            multi_hop_recall as _kt_multi_hop,
        )
        _KT_MODULE = True
        HAS_KNOWLEDGE_TREE = True
        global _recall_knowledge_tree, _recall_knowledge_tree_raw, _multi_hop_recall
        _recall_knowledge_tree = _kt_recall
        _recall_knowledge_tree_raw = _kt_recall_raw
        _multi_hop_recall = _kt_multi_hop
        logger.info("知识树模块加载成功，知识树 recall 已启用")
        return True
    except ImportError as _kt_err:
        HAS_KNOWLEDGE_TREE = False
        logger.warning("知识树模块不可用（knowledge_tree_plugin.public_api 未找到），知识树 recall 降级为空: %s", _kt_err)
        return False


def _recall_knowledge_tree(*args: object, **kwargs: object) -> None:
    if _ensure_kt_imported():
        return _recall_knowledge_tree(*args, **kwargs)
    return None


def _recall_knowledge_tree_raw(*args: object, **kwargs: object) -> list:
    if _ensure_kt_imported():
        return _recall_knowledge_tree_raw(*args, **kwargs)
    return []


def _multi_hop_recall(*args: object, **kwargs: object) -> list:
    """占位函数，首次调用时触发延迟导入。"""
    if _ensure_kt_imported():
        return _multi_hop_recall(*args, **kwargs)
    return []


HAS_KNOWLEDGE_TREE = False

# 共享线程池
_recall_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="recall")


# ========== C-P1-1: 轻量级 Compaction ==========

class _CompactionTracker:
    """跟踪调用次数，超阈值后限制注入，防止 Hindsight 行数反噬 context。"""

    def __init__(self, max_rounds: int = 20) -> None:
        self._rounds: dict[str, int] = defaultdict(int)
        self._max_rounds = max_rounds

    def get_effective_max_results(self, session_id: str, default_max: int) -> int:
        self._rounds[session_id] += 1
        if self._rounds[session_id] > self._max_rounds:
            return 1
        return default_max


# ========== C-P1-4: 本地重要性缓存 ==========

class _HitCounter:
    """记录记忆被命中的次数，高频记忆获得分数 boost。"""

    _MAX_ENTRIES = 200

    def __init__(self, boost_factor: float = 0.1) -> None:
        self._counts: dict[str, int] = defaultdict(int)
        self._boost_factor = boost_factor

    def boost_scores(self, memories: list[dict], rerank_map: dict[str, float]) -> None:
        for m in memories:
            node_id = m.get("id", "")
            if node_id:
                self._counts[node_id] += 1
                hits = self._counts[node_id]
                if hits > 1 and node_id in rerank_map:
                    boost = min(1 + self._boost_factor * (hits - 1), 2.0)
                    rerank_map[node_id] = min(rerank_map[node_id] * boost, 2.0)
        self._evict_stale()

    def _evict_stale(self) -> None:
        if len(self._counts) > self._MAX_ENTRIES:
            to_remove = list(self._counts)[:self._MAX_ENTRIES // 2]
            for k in to_remove:
                del self._counts[k]


# ========== C-P1-3: 定期任务回述 ==========

class _TaskTracker:
    """每 N 轮注射任务状态摘要，避免长对话中目标被稀释。"""

    _MAX_ENTRIES = 200

    def __init__(self, interval: int = 5) -> None:
        self._rounds: dict[str, int] = defaultdict(int)
        self._interval = interval

    def get_summary_prompt(self, session_id: str) -> str | None:
        self._rounds[session_id] += 1
        self._evict_stale()
        if self._rounds[session_id] % self._interval == 0:
            return (
                "[任务状态摘要]\n"
                f"当前进度：第 {self._rounds[session_id]} 轮\n"
                "请简要总结当前进展并确认是否仍在正向推进。"
            )
        return None

    def current_round(self, session_id: str) -> int:
        return self._rounds.get(session_id, 0)

    def _evict_stale(self) -> None:
        if len(self._rounds) > self._MAX_ENTRIES:
            to_remove = list(self._rounds)[:self._MAX_ENTRIES // 2]
            for k in to_remove:
                del self._rounds[k]


_compaction = _CompactionTracker()
_hit_counter = _HitCounter()
_task_tracker = _TaskTracker()

# P2-2 Phase A: 记忆使用日志
_use_logger: UseLogger | None = None


def _get_use_logger() -> UseLogger | None:
    global _use_logger
    if not CONFIG.enable_use_log:
        return None
    if _use_logger is not None:
        return _use_logger
    try:
        _use_logger = UseLogger(
            enabled=CONFIG.enable_use_log,
            batch_size=CONFIG.use_log_batch_size,
            flush_interval_seconds=CONFIG.use_log_flush_interval_seconds,
            log_path=CONFIG.use_log_path,
        )
    except Exception as e:
        logger.debug("UseLogger init failed silently: %s", e)
        _use_logger = None
    return _use_logger


# Turn-to-turn 去重
_injected_ids: dict[str, _OrderedDict] = defaultdict(_OrderedDict)
_injected_session_ts: dict[str, float] = {}
_injected_lock = threading.Lock()
_INJECTED_LRU_MAX = 256
_INJECTED_SESSION_TTL = 86400
_INJECTED_SESSION_HARD_CAP = 2000


def _touch_injected_session(session_id: str) -> None:
    now = time.time()
    with _injected_lock:
        _injected_session_ts[session_id] = now
        if len(_injected_ids) <= _INJECTED_SESSION_HARD_CAP:
            return
        stale = [sid for sid, ts in _injected_session_ts.items() if now - ts > _INJECTED_SESSION_TTL]
        for sid in stale:
            _injected_ids.pop(sid, None)
            _injected_session_ts.pop(sid, None)
        if len(_injected_ids) > _INJECTED_SESSION_HARD_CAP:
            for sid, _ in sorted(_injected_session_ts.items(), key=lambda kv: kv[1])[: len(_injected_ids) // 2]:
                _injected_ids.pop(sid, None)
                _injected_session_ts.pop(sid, None)


# ========== 评测日志 ==========

_eval_logger: logging.Logger | None = None


def _get_eval_logger() -> logging.Logger | None:
    global _eval_logger
    if _eval_logger is not None:
        return _eval_logger
    log_path = CONFIG.eval_log_path
    if not log_path:
        return None
    try:
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        el = logging.getLogger("knowledge_navigation.eval")
        el.setLevel(logging.INFO)
        el.propagate = False
        fh = RotatingFileHandler(
            log_path,
            mode="a",
            maxBytes=CONFIG.max_trace_bytes,
            backupCount=CONFIG.backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(JSONFormatter())
        el.addHandler(fh)
        _eval_logger = el
    except Exception:
        _eval_logger = None
    return _eval_logger


_eval_queries: list[dict[str, Any]] | None = None


def _load_eval_queries() -> list[dict[str, Any]]:
    global _eval_queries
    if _eval_queries is not None:
        return _eval_queries
    path = CONFIG.eval_queries_path
    if not path:
        _eval_queries = []
        return _eval_queries
    try:
        import json
        with open(path, "r") as f:
            raw = json.load(f)
        required_fields = {"query_id", "query", "dimension"}
        validated = []
        for item in raw:
            if isinstance(item, dict) and required_fields.issubset(item.keys()):
                validated.append(item)
            else:
                missing = required_fields - set(item.keys()) if isinstance(item, dict) else required_fields
                logger.warning(
                    "eval query 缺少必需字段，跳过: %s, missing=%s",
                    item.get("query_id", "unknown"),
                    ", ".join(sorted(missing)),
                )
        _eval_queries = validated
    except Exception as e:
        logger.warning(
            "eval queries load failed",
            extra={"path": path, "error": str(e)},
        )
        _eval_queries = []
    return _eval_queries