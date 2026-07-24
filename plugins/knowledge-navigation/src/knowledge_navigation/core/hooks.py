"""Hermes Hook 实现。

每次 LLM 调用前自动执行：三层门控 → LLM Router {h,kt,s} mask → 按 mask 条件执行 HS/KT/SK → 后处理注入。
使用 Hindsight trace 模式获取 rerank_score 做精度过滤，日志记录监控。
知识树 recall 通过 knowledge-tree-plugin 公共 API 调用。

因果链数据由 build-causal-links.py 一次性补齐，Hook 只做读取。
Router（core/router.py）替代了旧版 _classify_intent 规则分类，基于 need analysis 做三路决策。
"""

import json
import html
import logging
import os
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from logging.handlers import RotatingFileHandler
from typing import Any

# 顶层可选依赖：网关运行时应始终可用；导入失败时降级为 None，函数内做 None 检查
try:
    import requests as _requests  # noqa: N816 —— 与函数内旧别名 _req 保持语义一致
except ImportError:  # pragma: no cover — 极端隔离环境降级
    _requests = None  # type: ignore[assignment]

try:
    import psycopg2 as _psycopg2  # noqa: N816 —— 与函数内旧别名 _pg 保持语义一致
except ImportError:  # pragma: no cover — psycopg2 未安装时因果链 boost 自动禁用
    _psycopg2 = None  # type: ignore[assignment]




from knowledge_navigation.adapters.hindsight import HindsightClient
from knowledge_navigation.config import CONFIG, JSONFormatter
from knowledge_navigation.turn_gate import skip_pre_llm_call, skip_non_user, skip_system_prompt
from knowledge_navigation.core.env_loader import get_env

logger = logging.getLogger(__name__)

# 知识树集成（通过 knowledge-tree-plugin 公共 API）
# 延迟导入：knowledge-tree-plugin 必须先于 knowledge-navigation 加载，
# 但为确保在插件加载顺序异常时仍能工作，使用 lazy import 在首次调用时加载。
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
        # 注入到模块全局
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

# 初始占位函数，首次调用时触发延迟导入
def _recall_knowledge_tree(*args: object, **kwargs: object) -> None:
    if _ensure_kt_imported():
        return _recall_knowledge_tree(*args, **kwargs)
    return None

def _recall_knowledge_tree_raw(*args: object, **kwargs: object) -> list:
    if _ensure_kt_imported():
        return _recall_knowledge_tree_raw(*args, **kwargs)
    return []

HAS_KNOWLEDGE_TREE = False  # False = 尚未尝试或失败，True = 已成功导入

# 共享线程池：复用 ThreadPoolExecutor 避免每次 pre_llm_call 创建/销毁开销
# 最多 4 路并行 recall（Hindsight + KnowledgeTree + SkillMatch + SAG）
_recall_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="recall")

# PG 连接缓存：thread-local 存储，每个线程拥有独立连接
# psycopg2 connection 不是线程安全的，多线程并发使用同一连接会导致竞态
# _causal_boost 使用，避免每次 recall 新建连接
_pg_conn_local = threading.local()
_PG_CONN_TTL = 300  # 5 分钟无使用自动关闭


def _get_cached_conn(db_url: str, pg_module) -> "psycopg2.extensions.connection | None":
    """从 thread-local 获取复用 PG 连接，超时未用的自动关闭。

    使用 threading.local() 确保每个线程拥有独立连接，
    避免 psycopg2 connection 被多线程并发使用的竞态问题。
    """
    now = time.time()

    # 获取当前线程的连接缓存
    if not hasattr(_pg_conn_local, "conn_map"):
        _pg_conn_local.conn_map = {}  # type: ignore[attr-defined]

    conn_map = _pg_conn_local.conn_map  # type: ignore[attr-defined]

    # 清理当前线程的过期连接
    stale = [k for k, (_, ts) in conn_map.items() if now - ts > _PG_CONN_TTL]
    for k in stale:
        conn_entry = conn_map.pop(k, None)
        if conn_entry:
            try:
                conn_entry[0].close()
            except Exception:
                logger.debug("PG 连接关闭失败（缓存清理路径）: %s", k)

    # 返回缓存连接或新建
    if db_url in conn_map:
        conn, _ = conn_map[db_url]
        try:
            # 健康检查
            with conn.cursor() as _cur:
                _cur.execute("SELECT 1")
            conn_map[db_url] = (conn, now)
            return conn
        except Exception:
            # 连接失效，清理后重新创建
            conn_entry = conn_map.pop(db_url, None)
            if conn_entry:
                try:
                    conn_entry[0].close()
                except Exception:
                    pass
    try:
        conn = pg_module.connect(db_url, connect_timeout=5)
        conn_map[db_url] = (conn, now)
        return conn
    except Exception:
        return None


from knowledge_navigation.core.filtering import (
    apply_token_budget,
    calculate_score_stats,
    cross_domain_dedup,
    estimate_tokens,
    exclude_marked,
    extract_rerank_scores,
    extract_score,
    filter_by_score,
)
from knowledge_navigation.core.recall_logger import RecallLogger
from knowledge_navigation.core.router import route as _router_route
from knowledge_navigation.core.use_log import UseLogger

# 评测日志（独立文件，用于评估灵活匹配效果）
_eval_logger: logging.Logger | None = None


# ========== C-P1-1: 轻量级 Compaction ==========

class _CompactionTracker:
    """跟踪调用次数，超阈值后限制注入，防止 Hindsight 行数反噬 context。"""

    def __init__(self, max_rounds: int = 20) -> None:
        self._rounds: dict[str, int] = defaultdict(int)
        self._max_rounds = max_rounds

    def get_effective_max_results(self, session_id: str, default_max: int) -> int:
        self._rounds[session_id] += 1
        if self._rounds[session_id] > self._max_rounds:
            return 1  # 超阈值后只注入 1 条
        return default_max


# ========== C-P1-4: 本地重要性缓存 ==========

class _HitCounter:
    """记录记忆被命中的次数，高频记忆获得分数 boost。"""

    _MAX_ENTRIES = 200  # 内存保护：超过时淘汰最早条目

    def __init__(self, boost_factor: float = 0.1) -> None:
        self._counts: dict[str, int] = defaultdict(int)
        self._boost_factor = boost_factor

    def boost_scores(self, memories: list[dict], rerank_map: dict[str, float]) -> None:
        """提高高频被命中记忆的 rerank_score，boost 上限 2x，绝对分数上限 2.0。"""
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
        """条目超限时淘汰最早的一半。"""
        if len(self._counts) > self._MAX_ENTRIES:
            to_remove = list(self._counts)[:self._MAX_ENTRIES // 2]
            for k in to_remove:
                del self._counts[k]


# ========== C-P1-3: 定期任务回述 ==========

class _TaskTracker:
    """每 N 轮注射任务状态摘要，避免长对话中目标被稀释。"""

    _MAX_ENTRIES = 200  # 内存保护

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
        """返回当前 session 的轮次号，用于日志打点。"""
        return self._rounds.get(session_id, 0)

    def _evict_stale(self) -> None:
        """条目超限时淘汰最早的一半。"""
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
    """懒加载 UseLogger 实例。

    Feature Flag: CONFIG.enable_use_log=false 时返回 None，完全不启用。
    """
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

# Turn-to-turn 去重：session 隔离的 LRU，记录本轮已注入的 memory node_id
# 结构：{session_id: OrderedDict{node_id: timestamp}}
# OrderedDict 保证插入顺序，超限时淘汰最早的一半
# 线程安全：被 4-worker ThreadPoolExecutor 并发读写，必须加锁保护，
# 否则 defaultdict 自动建 key 与 OrderedDict.popitem 并发会破坏内部状态。
from collections import OrderedDict as _OrderedDict
_injected_ids: dict[str, _OrderedDict] = defaultdict(_OrderedDict)
_injected_session_ts: dict[str, float] = {}  # session 最后活动时间，用于 TTL 淘汰
_injected_lock = threading.Lock()  # 保护 _injected_ids 和 _injected_session_ts 的并发访问
_INJECTED_LRU_MAX = 256  # 每个 session 最多保存 256 条
_INJECTED_SESSION_TTL = 86400  # session 24h 未活动则整个删除，防网关常驻进程内存泄漏
_INJECTED_SESSION_HARD_CAP = 2000  # session 总数硬上限，极端情况下触发清理


def _touch_injected_session(session_id: str) -> None:
    """记录 session 活动，并惰性清理过期/超限 session（线程安全）。"""
    now = time.time()
    with _injected_lock:
        _injected_session_ts[session_id] = now
        # 惰性清理：session 数量较多时才扫描，避免每轮都遍历
        if len(_injected_ids) <= _INJECTED_SESSION_HARD_CAP:
            return
        stale = [sid for sid, ts in _injected_session_ts.items() if now - ts > _INJECTED_SESSION_TTL]
        for sid in stale:
            _injected_ids.pop(sid, None)
            _injected_session_ts.pop(sid, None)
        # TTL 清理后仍超硬上限：按最后活动时间淘汰最早的一半
        if len(_injected_ids) > _INJECTED_SESSION_HARD_CAP:
            for sid, _ in sorted(_injected_session_ts.items(), key=lambda kv: kv[1])[: len(_injected_ids) // 2]:
                _injected_ids.pop(sid, None)
                _injected_session_ts.pop(sid, None)


from knowledge_navigation.core.circuit_breaker import (
    circuit_is_open, circuit_record_failure, circuit_record_success,
    sag_circuit_is_open, sag_circuit_record_failure, sag_circuit_record_success,
)


# ========== 因果链 boost ==========


def _batch_embed(texts: list[str]) -> list[list[float]] | None:
    """调用 SiliconFlow embedding API（bge-m3），供 cross_domain_dedup 使用。"""
    if _requests is None:
        logger.debug("_batch_embed: requests 模块不可用")
        return None
    api_key = get_env("SILICONFLOW_API_KEY", "")
    if not api_key:
        logger.debug("_batch_embed: SILICONFLOW_API_KEY 未设置")
        return None
    try:
        resp = _requests.post(
            "https://api.siliconflow.cn/v1/embeddings",
            json={"model": "BAAI/bge-m3", "input": texts},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug("_batch_embed: HTTP %s — %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        return [d["embedding"] for d in data.get("data", [])]
    except Exception as e:
        logger.debug("_batch_embed 异常: %s", e)
        return None


def _causal_boost(
    raw_results: list[dict],
    rerank_map: dict[str, float],
    alpha: float = 0.15,
    cap: float = 1.3,
) -> None:
    """查询 memory_links 表，将被因果链引用的记忆 rerank_score 提升。

    只 boost 已在候选池中的记忆（不新增候选），
    确保因果链只微调排序，不引入无关结果。

    Args:
        raw_results: Hindsight recall 的原始结果
        rerank_map: {memory_id: rerank_score}，原地修改
        alpha: 提权系数，越大因果影响越强
        cap: 最大提权上限，防翻转排序
    """
    if _psycopg2 is None:
        return
    db_url = get_env("KT_DB_URL", "")
    if not db_url:
        return
    recalled_ids = [str(m.get("id", "")) for m in raw_results if m.get("id")]
    if not recalled_ids:
        return
    try:
        # 从连接缓存获取复用连接（thread-local）
        conn = _get_cached_conn(db_url, _psycopg2)
        if conn is None:
            return
        with conn.cursor() as cursor:
            # 查询：候选池中 memory_links 的因果链（含 weight 列）
            # 显式 CAST SELECT 列到 text，防止 uuid ≠ str 的类型不匹配
            cursor.execute(
                "SELECT from_unit_id::text, to_unit_id::text, link_type, weight "
                "FROM memory_links "
                "WHERE from_unit_id::text = ANY(%s) "
                "  AND to_unit_id::text = ANY(%s) "
                "  AND link_type IN ('causes', 'caused_by')",
                (recalled_ids, list(rerank_map.keys())),
            )
            for from_id, to_id, link_type, weight in cursor.fetchall():
                from_score = rerank_map.get(from_id, 0.0)
                if from_score <= 0 or to_id not in rerank_map:
                    continue
                # 因果提权：原因记忆分数越高 + weight 越大 → 结果记忆受益越大
                boost = 1.0 + alpha * from_score * (weight or 0.5)
                current = rerank_map[to_id]
                rerank_map[to_id] = current * min(boost, cap)
        # 更新 thread-local 缓存时间戳
        if hasattr(_pg_conn_local, "conn_map"):
            _pg_conn_local.conn_map[db_url] = (conn, time.time())  # type: ignore[attr-defined]
    except Exception as e:
        logger.warning("causal_boost 失败: %s", e)


    # 评测日志（独立文件，用于评估灵活匹配效果）


def _get_eval_logger() -> logging.Logger | None:
    """获取或初始化评测日志记录器。"""
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
        el.propagate = False  # 不向上传播，独立写文件
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


# 评测查询缓存
_eval_queries: list[dict[str, Any]] | None = None


def _load_eval_queries() -> list[dict[str, Any]]:
    """懒加载评测查询列表，带 Schema 验证。"""
    global _eval_queries
    if _eval_queries is not None:
        return _eval_queries
    path = CONFIG.eval_queries_path
    if not path:
        _eval_queries = []
        return _eval_queries
    try:
        with open(path, "r") as f:
            raw = json.load(f)
        # Schema 验证：必需字段
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


# 中文停用字 — 单字过滤，避免噪声匹配
# 注：完整定义已迁至 core.text_utils.CJK_STOP_CHARS
from knowledge_navigation.core.text_utils import CJK_STOP_CHARS as _CJK_STOP_CHARS


def _extract_keywords(text: str) -> set[str]:
    """提取文本中的有意义关键词（仅用于 eval query 匹配）。

    委托 core.text_utils.extract_keywords，保守配置：
    英文 >=2 字符 + CJK 2-gram（无原始整段）
    """
    from knowledge_navigation.core.text_utils import extract_keywords as _tu_extract
    return _tu_extract(
        text,
        min_en_length=2,
        include_cjk_bigrams=True,
        include_cjk_full=False,
    )


def _normalize_eval_text(text: str) -> str:
    """规范化 eval query 文本：去掉显式标记并折叠空白。"""
    text = re.sub(r"\[EVAL:[^\]]+\]", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _match_eval_query(user_message: str) -> dict | None:
    """匹配评测查询，但只让 exact / explicit 进入 recall@k 计数。

    生产自然对话中的关键词相似匹配只记录为候选（eval_counted=false），
    不写 eval_query_id，也不计算 eval_recall_hit，避免把普通对话误判为
    离线评测失败。

    Returns:
        {
          "query_id": str, "expected_ids": list[str],
          "match_method": "exact|explicit_id|fuzzy",
          "confidence": float, "counted": bool,
        } | None
    """
    queries = _load_eval_queries()
    if not queries:
        return None

    el = _get_eval_logger()
    eval_log_data: dict[str, Any] = {
        "event": "eval_match",
        "user_message_trunc": user_message[:60],
    }

    by_id = {str(item.get("query_id", "")): item for item in queries if item.get("query_id")}

    def _build_result(item: dict, method: str, confidence: float, counted: bool) -> dict:
        return {
            "query_id": item.get("query_id", ""),
            "expected_ids": item.get("expected_ids", []),
            "match_method": method,
            "confidence": confidence,
            "counted": counted,
        }

    # 1. 显式触发：[EVAL:query_id]，仅用于人工/离线评测
    explicit = re.search(r"\[EVAL:([^\]]+)\]", user_message, flags=re.IGNORECASE)
    if explicit:
        explicit_id = explicit.group(1).strip()
        item = by_id.get(explicit_id)
        eval_log_data["match_type"] = "explicit_id"
        eval_log_data["matched_query_id"] = explicit_id if item else None
        eval_log_data["score"] = 1.0 if item else 0.0
        eval_log_data["accepted"] = item is not None
        eval_log_data["counted"] = item is not None
        if el:
            el.info("eval_match", extra=eval_log_data)
        return _build_result(item, "explicit_id", 1.0, True) if item else None

    normalized_user = _normalize_eval_text(user_message)

    # 同时也是主日志，确保空 recall 时 eval_match 不被丢弃
    main_log_data: dict[str, Any] = {
        "event": "eval_match",
        "user_message_trunc": user_message[:60],
    }

    # 2. 规范化精确匹配（最高精度，进入 recall@k 计数）
    for item in queries:
        if _normalize_eval_text(str(item.get("query", ""))) == normalized_user:
            eval_log_data["match_type"] = "exact"
            eval_log_data["matched_query_id"] = item.get("query_id")
            eval_log_data["matched_query_trunc"] = item.get("query", "")[:60]
            eval_log_data["score"] = 1.0
            eval_log_data["accepted"] = True
            eval_log_data["counted"] = True
            main_log_data["match_type"] = "exact"
            main_log_data["matched_query_id"] = item.get("query_id")
            main_log_data["matched_query_trunc"] = item.get("query", "")[:60]
            main_log_data["score"] = 1.0
            main_log_data["accepted"] = True
            main_log_data["counted"] = True
            main_log_data["query_id"] = item["query_id"]
            logger.info("eval_match", extra=main_log_data)
            if el:
                el.info("eval_match", extra=eval_log_data)
            return _build_result(item, "exact", 1.0, True)

    # 3. 关键词重叠只作为候选观测，不计入 recall@k
    user_keywords = _extract_keywords(user_message)
    if not user_keywords:
        eval_log_data["match_type"] = "fuzzy"
        eval_log_data["user_keywords"] = []
        eval_log_data["candidates"] = []
        eval_log_data["matched_query_id"] = None
        eval_log_data["accepted"] = False
        eval_log_data["counted"] = False
        if el:
            el.info("eval_match", extra=eval_log_data)
        return None

    candidates: list[dict[str, Any]] = []
    best_item: dict | None = None
    best_score = 0.0

    for item in queries:
        query_text = item.get("query", "")
        query_id = item.get("query_id", "")
        query_keywords = _extract_keywords(query_text)
        if not query_keywords:
            continue
        intersection = user_keywords & query_keywords
        overlap_score = len(intersection) / len(query_keywords)
        candidates.append({
            "query_id": query_id,
            "score": round(overlap_score, 4),
            "matched_keywords": list(intersection),
        })
        if overlap_score > best_score:
            best_score = overlap_score
            best_item = item

    threshold = CONFIG.eval_min_score
    accepted = best_item is not None and best_score >= threshold

    eval_log_data["match_type"] = "fuzzy"
    eval_log_data["user_keywords"] = list(user_keywords)
    eval_log_data["candidates"] = candidates
    eval_log_data["matched_query_id"] = best_item.get("query_id") if best_item else None
    eval_log_data["score"] = round(best_score, 4)
    eval_log_data["threshold"] = threshold
    eval_log_data["accepted"] = accepted
    eval_log_data["counted"] = False

    if el:
        el.info("eval_match", extra=eval_log_data)

    return _build_result(best_item, "fuzzy", best_score, False) if accepted else None


def _build_mentioned_at_map(raw_results: list[dict]) -> dict[str, str]:
    """从召回结果提取 mentioned_at 映射。"""
    return {r["id"]: r["mentioned_at"] for r in raw_results if r.get("id") and r.get("mentioned_at")}


# ========== 并行 recall 辅助函数（2026-06-13） ==========


def _do_hindsight_recall(query: str) -> dict | None:
    """执行 Hindsight recall，使用共享 Session。

    异常不在此处吞掉：由外层 future.result() 的 except 分支统一记录
    熔断器失败计数 + 日志，确保熔断器在 Hindsight 持续故障时正常触发。
    """
    client = HindsightClient(CONFIG.hindsight_api_url, CONFIG.timeout_seconds)
    try:
        return client.recall(
            query,
            max_results=CONFIG.max_results * 3,  # 多取一些，给过滤留余量
        )
    except Exception:
        raise
    finally:
        client.close()


def _do_kt_recall(session_id: str, query: str) -> list[dict]:
    """执行知识树 recall，有异常时返回空列表。"""
    if not HAS_KNOWLEDGE_TREE:
        _ensure_kt_imported()
    if not HAS_KNOWLEDGE_TREE:
        return []
    try:
        return _recall_knowledge_tree_raw(session_id, query)
    except Exception as e:
        logger.warning(
            "知识树 recall 异常（跳过）",
            extra={"session_id": session_id, "error": f"{type(e).__name__}: {e}"},
        )
        return []



def _do_skill_match(query: str) -> str:
    """执行 skill 匹配（LLM 全量匹配 + 读盘注入全文），返回注入文本或空字符串。"""
    from knowledge_navigation.core.skill_matcher import match_skills, strip_frontmatter  # type: ignore[import-untyped]

    try:
        matched = match_skills(query)
        if not matched:
            return ""
        lines: list[str] = ["", "<auto_loaded_skills>"]
        lines.append("以下技能与当前问题相关，已自动加载完整内容：")
        for s in matched:
            # 读 SKILL.md，去 frontmatter（复用 skill_matcher 的统一实现）
            path = s["path"]
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read()
                body = strip_frontmatter(raw)
                # 截断保护（4000 字上限）
                truncated = len(body) > 4000
                content = body[:4000] if truncated else body
            except Exception:
                content = f"（无法读取 {s['name']}）"
                truncated = False
            lines.append(
                f"\n### {s['name']} (match={s['score']})\n"
                f"{content}"
            )
            if truncated:
                lines.append(
                    f"\u2139\ufe0f 该技能内容已截断，如需完整内容请调用 skill_view(name='{s['name']}') 加载。"
                )
        lines.append("\n</auto_loaded_skills>")
        logger.info(
            "Skill match: %s",
            [m["name"] for m in matched],
            extra={"event": "skill_match", "skills": [m["name"] for m in matched]},
        )
        return "\n".join(lines)
    except Exception as e:
        logger.debug("Skill match error: %s", e)
        return ""


def _do_sag_recall(query: str) -> list[dict]:
    """执行 SAG 文档检索（4th route），通过 REST API 调 /search。"""
    if sag_circuit_is_open():
        logger.debug("SAG 熔断器开启，跳过 recall")
        return []
    t0 = time.time()
    try:
        import requests as _req
        # SAG /search 要求 sourceIds 为 array，从配置解析（逗号分隔）
        source_ids = [s.strip() for s in CONFIG.sag_source_ids.split(",") if s.strip()]
        payload = {
            "query": query,
            "topK": CONFIG.sag_search_top_k,
            "searchMode": "fast",
            "sourceIds": source_ids,
        }
        resp = _req.post(
            f"{CONFIG.sag_api_url}/search",
            json=payload,
            timeout=CONFIG.sag_search_timeout,
        )
        if resp.status_code != 200:
            sag_circuit_record_failure("service_error")
            logger.warning(
                "SAG recall HTTP %d, 耗时 %.1fms",
                resp.status_code, (time.time() - t0) * 1000,
            )
            return []
        data = resp.json()
        sections = data.get("sections", [])
        sag_circuit_record_success()
        logger.info(
            "SAG recall: %d sections, 耗时 %.1fms",
            len(sections), (time.time() - t0) * 1000,
            extra={"event": "sag_recall", "count": len(sections)},
        )
        return sections
    except Exception as e:
        sag_circuit_record_failure("exception")
        logger.debug("SAG recall 异常（跳过）: %s, 耗时 %.1fms", e, (time.time() - t0) * 1000)
        return []


def _normalize_kt_score(raw_score: Any) -> tuple[float, str]:
    """把知识树 score 统一映射到可比较的 final_score。

    知识树 attention_filter 在冷启动时可能返回 cosine（约 0~1），
    非冷启动时返回 softmax attention（常低于 0.6）。这里采用保守映射：
    有 score 时压到 [0.5, 0.9]，无 score fallback 到 0.45，避免无分候选
    污染 score_stats。

    注意：所有输入 score（含 >1 的异常值）都先 clamp 到 [0,1] 再映射，
    否则 score=1.1 会得到 1.1（>score=1.0 映射后的 0.9），
    导致异常高分支配的候选反转排序。
    """
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return 0.45, "fallback"
    if score < 0:
        score = 0.0
    if score > 1.0:
        score = 1.0
    return round(0.5 + 0.4 * score, 4), "tree_score"


def _build_knowledge_tree_candidate(kp: dict[str, Any]) -> dict[str, Any] | None:
    """把知识树结果对齐到统一候选结构。"""
    text = (kp.get("text", "") or kp.get("name", "")).strip()
    if not text:
        return None
    final_score, score_source = _normalize_kt_score(kp.get("score"))
    return {
        "id": str(kp.get("id", "")),
        "text": text,
        "source": "knowledge_tree",
        "base_score": final_score,
        "tree_score": kp.get("score"),
        "final_score": final_score,
        "rerank_score": final_score,
        "score_source": score_source,
    }


def _candidate_score(result: dict[str, Any]) -> float:
    """统一读取候选最终分数（委托给 filtering.extract_score，避免重复实现）。"""
    return extract_score(result)


def _pass_gates(session_id: str, user_message: str, platform: str, is_first_turn: bool) -> tuple[bool, dict | None]:
    """三层门控 + eval bypass 判断。

    Returns:
        (should_continue, eval_match)
        should_continue=False 表示 pre_llm_call 应立即 return None
        eval_match: 命中 eval query 时返回匹配详情，否则为 None
    """
    # 第0道门：eval query 无条件放行（跳过生产门控）
    eval_match = _match_eval_query(user_message) if CONFIG.eval_match_enabled else None
    if eval_match:
        logger.info(
            "eval_query bypass gate",
            extra={
                "session_id": session_id,
                "eval_query_id": eval_match.get("query_id"),
                "event": "eval_query_bypass",
            },
        )
        return True, eval_match

    # 第一道门：非用户平台
    if skip_non_user(platform):
        logger.debug(
            "非用户平台跳过 pre_llm_call",
            extra={"session_id": session_id, "event": "skip_non_user"},
        )
        return False, None

    # 第一.五道门：系统提示词
    if skip_system_prompt(user_message, is_first_turn):
        logger.debug(
            "系统提示词跳过 pre_llm_call",
            extra={"session_id": session_id, "event": "skip_system_prompt"},
        )
        return False, None

    # 第二道门：文本门控 — 操作型对话跳过
    skip_reason = skip_pre_llm_call(user_message)
    if skip_reason:
        logger.debug(
            "turn_gate 跳过 pre_llm_call: %s",
            skip_reason,
            extra={"session_id": session_id, "event": "skip_operational"},
        )
        return False, None

    return True, None


def _get_router_mask(session_id: str, user_message: str) -> dict[str, bool]:
    """调用 Router 决策四路 mask，异常时 fallback 全开。"""
    try:
        # 运行时读 env 覆盖 CONFIG 单例的缓存值
        timeout = int(os.getenv("KN_ROUTER_TIMEOUT", str(CONFIG.router_timeout)))
        mask = _router_route(
            session_id,
            user_message,
            CONFIG.router_model,
            CONFIG.router_api_url,
            CONFIG.router_api_key,
            timeout,
        )
        mask.setdefault("h", True)
        mask.setdefault("kt", True)
        mask.setdefault("s", True)
        # SAG fallback 必须为 False（见 project_memory：normal 和 exception 路径一致）
        mask.setdefault("sag", False)
    except Exception as e:
        logger.warning("Router 调用异常 (%s)，fallback 三路全开，SAG 关闭", e)
        mask = {"h": True, "kt": True, "s": True, "sag": False}
    logger.info(
        "Router mask: h=%s kt=%s s=%s sag=%s",
        mask["h"], mask["kt"], mask["s"], mask["sag"],
        extra={"session_id": session_id, "event": "router_mask", "mask": mask},
    )
    return mask


def _execute_recall(
    session_id: str,
    user_message: str,
    hs_active: bool,
    kt_active: bool,
    s_active: bool,
    sag_active: bool,
    active_count: int,
    t0: float,
    query_trunc: str,
    recall_logger: RecallLogger,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str, list[dict[str, Any]]]:
    """执行四路 recall（Hindsight + 知识树 + Skill + SAG），并行或串行。

    Returns:
        (hindsight_result, kt_raw_results, skill_context, sag_raw_results)
    """
    result: dict[str, Any] | None = None
    kt_raw_results: list[dict[str, Any]] = []
    skill_context = ""
    sag_raw_results: list[dict[str, Any]] = []

    if active_count >= 2:
        # 多路并行（使用共享线程池，避免创建/销毁开销）
        hs_future = _recall_executor.submit(_do_hindsight_recall, user_message) if hs_active else None
        kt_future = _recall_executor.submit(_do_kt_recall, session_id, user_message) if kt_active else None
        sk_future = _recall_executor.submit(_do_skill_match, user_message) if s_active else None
        sag_future = _recall_executor.submit(_do_sag_recall, user_message) if sag_active else None
        try:
            if hs_future is not None:
                _hs_t0 = time.time()
                try:
                    result = hs_future.result(timeout=CONFIG.timeout_seconds)
                    _hs_latency = (time.time() - _hs_t0) * 1000
                    _hs_results = result.get("results", []) if result else []
                    recall_logger.record(
                        "hindsight", _hs_results, _hs_latency,
                        session_id=session_id, query=user_message,
                    )
                    if result is None:
                        circuit_record_failure("service_error")
                    else:
                        circuit_record_success()
                except FuturesTimeout:
                    hs_future.cancel()
                    circuit_record_failure("exception")
                    recall_logger.record(
                        "hindsight", [], (time.time() - _hs_t0) * 1000,
                        session_id=session_id, query=user_message,
                        error=f"timeout({CONFIG.timeout_seconds}s)",
                    )
                    logger.error(
                        "Hindsight recall 超时（%d 秒）",
                        CONFIG.timeout_seconds,
                        extra={"session_id": session_id, "query_trunc": query_trunc, "event": "recall_timeout"},
                    )
                except Exception as e:
                    circuit_record_failure("exception")
                    recall_logger.record(
                        "hindsight", [], (time.time() - _hs_t0) * 1000,
                        session_id=session_id, query=user_message,
                        error=f"{type(e).__name__}: {e}",
                    )
                    logger.error(
                        "Hindsight recall error",
                        extra={"session_id": session_id, "query_trunc": query_trunc, "error": f"{type(e).__name__}: {e}", "event": "recall_error"},
                    )

            if kt_future is not None:
                remaining = max(0.1, CONFIG.timeout_seconds - (time.time() - t0))
                _kt_t0 = time.time()
                try:
                    kt_raw_results = kt_future.result(timeout=remaining)
                    _kt_latency = (time.time() - _kt_t0) * 1000
                    recall_logger.record(
                        "knowledge_tree", kt_raw_results, _kt_latency,
                        session_id=session_id, query=user_message,
                    )
                except FuturesTimeout:
                    kt_future.cancel()
                    recall_logger.record(
                        "knowledge_tree", [], (time.time() - _kt_t0) * 1000,
                        session_id=session_id, query=user_message,
                        error=f"timeout({remaining:.1f}s)",
                    )
                    logger.warning("知识树 recall 超时（%.1f 秒）", remaining)
                    kt_raw_results = []
                except Exception as e:
                    recall_logger.record(
                        "knowledge_tree", [], (time.time() - _kt_t0) * 1000,
                        session_id=session_id, query=user_message,
                        error=f"{type(e).__name__}: {e}",
                    )
                    logger.warning("知识树 recall 异常（跳过）: %s", e)
                    kt_raw_results = []

            if sk_future is not None:
                _sk_t0 = time.time()
                try:
                    skill_context = sk_future.result(timeout=CONFIG.timeout_seconds)
                    _sk_latency = (time.time() - _sk_t0) * 1000
                    _sk_results = [{"id": "skill_context", "score": 1.0}] if skill_context else []
                    recall_logger.record(
                        "skill", _sk_results, _sk_latency,
                        session_id=session_id, query=user_message,
                    )
                except Exception as e:
                    recall_logger.record(
                        "skill", [], (time.time() - _sk_t0) * 1000,
                        session_id=session_id, query=user_message,
                        error=f"{type(e).__name__}: {e}",
                    )
                    logger.debug("Skill match future error: %s", e)

            if sag_future is not None:
                sag_remaining = max(0.1, CONFIG.sag_search_timeout - (time.time() - t0))
                _sag_t0 = time.time()
                try:
                    sag_raw_results = sag_future.result(timeout=sag_remaining)
                    _sag_latency = (time.time() - _sag_t0) * 1000
                    recall_logger.record(
                        "sag", sag_raw_results, _sag_latency,
                        session_id=session_id, query=user_message,
                    )
                except FuturesTimeout:
                    sag_future.cancel()
                    sag_circuit_record_failure("timeout")
                    recall_logger.record(
                        "sag", [], (time.time() - _sag_t0) * 1000,
                        session_id=session_id, query=user_message,
                        error=f"timeout({sag_remaining:.1f}s)",
                    )
                    logger.debug("SAG recall 超时（%.1f 秒）", sag_remaining)
                except Exception as e:
                    sag_circuit_record_failure("exception")
                    recall_logger.record(
                        "sag", [], (time.time() - _sag_t0) * 1000,
                        session_id=session_id, query=user_message,
                        error=f"{type(e).__name__}: {e}",
                    )
                    logger.debug("SAG recall future error: %s", e)
        finally:
            # 取消未完成的 future（共享线程池不 shutdown）
            if hs_future is not None and not hs_future.done():
                hs_future.cancel()
            if kt_future is not None and not kt_future.done():
                kt_future.cancel()
            if sk_future is not None and not sk_future.done():
                sk_future.cancel()
            if sag_future is not None and not sag_future.done():
                sag_future.cancel()
    else:
        # 单路串行（节省线程开销，延迟无差别）
        if hs_active:
            _hs_t0 = time.time()
            try:
                result = _do_hindsight_recall(user_message)
                _hs_latency = (time.time() - _hs_t0) * 1000
                _hs_results = result.get("results", []) if result else []
                recall_logger.record(
                    "hindsight", _hs_results, _hs_latency,
                    session_id=session_id, query=user_message,
                )
                if result is None:
                    circuit_record_failure("exception")
                else:
                    circuit_record_success()
            except Exception as e:
                circuit_record_failure("exception")
                recall_logger.record(
                    "hindsight", [], (time.time() - _hs_t0) * 1000,
                    session_id=session_id, query=user_message,
                    error=f"{type(e).__name__}: {e}",
                )
                logger.error(
                    "Hindsight recall error",
                    extra={"session_id": session_id, "query_trunc": query_trunc, "error": f"{type(e).__name__}: {e}", "event": "recall_error"},
                )
                result = None
        if kt_active:
            _kt_t0 = time.time()
            try:
                kt_raw_results = _do_kt_recall(session_id, user_message)
                _kt_latency = (time.time() - _kt_t0) * 1000
                recall_logger.record(
                    "knowledge_tree", kt_raw_results, _kt_latency,
                    session_id=session_id, query=user_message,
                )
            except Exception as e:
                recall_logger.record(
                    "knowledge_tree", [], (time.time() - _kt_t0) * 1000,
                    session_id=session_id, query=user_message,
                    error=f"{type(e).__name__}: {e}",
                )
                logger.warning("知识树 recall 异常（跳过）: %s", e)
                kt_raw_results = []
        if s_active:
            _sk_t0 = time.time()
            try:
                skill_context = _do_skill_match(user_message)
                _sk_latency = (time.time() - _sk_t0) * 1000
                _sk_results = [{"id": "skill_context", "score": 1.0}] if skill_context else []
                recall_logger.record(
                    "skill", _sk_results, _sk_latency,
                    session_id=session_id, query=user_message,
                )
            except Exception as e:
                recall_logger.record(
                    "skill", [], (time.time() - _sk_t0) * 1000,
                    session_id=session_id, query=user_message,
                    error=f"{type(e).__name__}: {e}",
                )
                logger.debug("Skill match error: %s", e)
        if sag_active:
            _sag_t0 = time.time()
            try:
                sag_raw_results = _do_sag_recall(user_message)
                _sag_latency = (time.time() - _sag_t0) * 1000
                recall_logger.record(
                    "sag", sag_raw_results, _sag_latency,
                    session_id=session_id, query=user_message,
                )
            except Exception as e:
                sag_circuit_record_failure("exception")
                recall_logger.record(
                    "sag", [], (time.time() - _sag_t0) * 1000,
                    session_id=session_id, query=user_message,
                    error=f"{type(e).__name__}: {e}",
                )
                logger.debug("SAG recall error: %s", e)

    return result, kt_raw_results, skill_context, sag_raw_results


def _dedup_and_budget(
    kept: list[dict[str, Any]],
    session_id: str,
    skill_context: str,
) -> tuple[list[dict[str, Any]], str]:
    """Turn-to-turn 去重 + 文本去重 + Token 预算守门。"""
    # Turn-to-turn 去重：session 级 LRU（线程安全：所有 _injected_ids 访问需持有 _injected_lock）
    _touch_injected_session(session_id)
    with _injected_lock:
        _session_history = _injected_ids[session_id]
        _turn_dedup_count = 0
        if CONFIG.turn_to_turn_dedup_mode == "demote":
            demoted = 0
            for r in kept:
                nid = str(r.get("id", ""))
                if nid and nid in _session_history:
                    demoted_score = _candidate_score(r) * 0.1
                    r["final_score"] = demoted_score
                    r["rerank_score"] = demoted_score
                    demoted += 1
            if demoted:
                logger.info("turn-to-turn 降权: %d 条已注入记忆分数降至 0.1x", demoted)
            kept.sort(key=_candidate_score, reverse=True)
        else:
            for r in list(kept):
                nid = str(r.get("id", ""))
                if nid and nid in _session_history:
                    kept.remove(r)
                    _turn_dedup_count += 1
            if _turn_dedup_count:
                logger.info("turn-to-turn 去重: 移除 %d 条已注入记忆", _turn_dedup_count)
        for r in kept:
            nid = str(r.get("id", ""))
            if nid:
                _session_history[nid] = time.time()
        if len(_session_history) > _INJECTED_LRU_MAX:
            for _ in range(_INJECTED_LRU_MAX // 2):
                _session_history.popitem(last=False)

    from knowledge_navigation.core.filtering import dedup_by_text as _dedup_by_text
    kept = _dedup_by_text(kept)

    if CONFIG.enable_token_budget:
        hs_list = [r for r in kept if r.get("source", "hindsight") == "hindsight"]
        sag_list = [r for r in kept if r.get("source") == "sag"]
        kt_list = [r for r in kept if r.get("source") == "knowledge_tree"]
        skill_list = []
        if skill_context:
            skill_list = [{"text": skill_context, "source": "skill", "final_score": 1.0}]

        hs_tokens_before = sum(estimate_tokens(str(r.get("text", ""))) for r in hs_list)
        sag_tokens_before = sum(estimate_tokens(str(r.get("text", ""))) for r in sag_list)
        kt_tokens_before = sum(estimate_tokens(str(r.get("text", ""))) for r in kt_list)
        skill_tokens_before = estimate_tokens(skill_context) if skill_context else 0

        hs_sag_combined = hs_list + sag_list
        hs_kept_tb, kt_kept_tb, skill_kept_tb = apply_token_budget(
            hs_sag_combined, kt_list, skill_list,
            CONFIG.token_budget_total,
            CONFIG.token_budget_hindsight_ratio,
            CONFIG.token_budget_kt_ratio,
            CONFIG.token_budget_skill_ratio,
        )

        hs_tokens_after = sum(estimate_tokens(str(r.get("text", ""))) for r in hs_kept_tb if r.get("source", "hindsight") == "hindsight")
        sag_tokens_after = sum(estimate_tokens(str(r.get("text", ""))) for r in hs_kept_tb if r.get("source") == "sag")
        kt_tokens_after = sum(estimate_tokens(str(r.get("text", ""))) for r in kt_kept_tb)
        skill_tokens_after = estimate_tokens(skill_kept_tb[0]["text"]) if skill_kept_tb else 0

        kept = hs_kept_tb + kt_kept_tb
        if skill_kept_tb:
            skill_context = skill_kept_tb[0]["text"]
        else:
            skill_context = ""

        logger.info(
            "Token budget: hs %d→%d, sag %d→%d, kt %d→%d, skill %d→%d (total budget=%d)",
            hs_tokens_before, hs_tokens_after,
            sag_tokens_before, sag_tokens_after,
            kt_tokens_before, kt_tokens_after,
            skill_tokens_before, skill_tokens_after,
            CONFIG.token_budget_total,
            extra={
                "event": "token_budget",
                "hs_tokens_before": hs_tokens_before,
                "hs_tokens_after": hs_tokens_after,
                "sag_tokens_before": sag_tokens_before,
                "sag_tokens_after": sag_tokens_after,
                "kt_tokens_before": kt_tokens_before,
                "kt_tokens_after": kt_tokens_after,
                "skill_tokens_before": skill_tokens_before,
                "skill_tokens_after": skill_tokens_after,
                "total_budget": CONFIG.token_budget_total,
            },
        )

    return kept, skill_context


def _expand_multi_hop(
    kt_raw_results: list[dict[str, Any]],
    kt_active: bool,
    session_id: str,
) -> list[dict[str, Any]]:
    """多跳关联展开：从 KT 召回的知识点出发，沿 subject 展开同科目关联知识点。"""
    if not kt_raw_results or not kt_active:
        return kt_raw_results
    try:
        _seed_ids = [int(r["id"]) for r in kt_raw_results if r.get("id") and str(r["id"]).isdigit()]
        if _seed_ids:
            _mh_results = _multi_hop_recall(_seed_ids, top_k=2)
            if _mh_results:
                logger.info(
                    "多跳关联展开: %d 条",
                    len(_mh_results),
                    extra={"session_id": session_id, "event": "multi_hop_expand", "count": len(_mh_results)},
                )
                _existing_ids = {r.get("id") for r in kt_raw_results}
                for _mh in _mh_results:
                    if _mh.get("id") not in _existing_ids:
                        _mh["source"] = "multi-hop"
                        kt_raw_results.append(_mh)
                        _existing_ids.add(_mh.get("id"))
    except Exception as e:
        logger.debug("多跳 recall 异常（跳过）: %s", e)
    return kt_raw_results


def _assemble_xml_output(
    kept: list[dict[str, Any]],
    skill_context: str,
    session_id: str,
    user_message: str,
    ctx: dict[str, Any],
) -> str | None:
    """组装 XML 标签化上下文 + 记录日志 + 返回最终字符串。

    ctx 包含: query_trunc, eval_match, raw_results, latency_ms, kt_raw_results,
    excluded_count, kept_before_kt, kt_dedup_removed, score_comparison,
    summary, kept_before_compress
    """
    query_trunc = ctx["query_trunc"]
    eval_match = ctx["eval_match"]
    raw_results = ctx["raw_results"]
    latency_ms = ctx["latency_ms"]
    kt_raw_results = ctx["kt_raw_results"]
    excluded_count = ctx["excluded_count"]
    kept_before_kt = ctx["kept_before_kt"]
    kt_dedup_removed = ctx["kt_dedup_removed"]
    score_comparison = ctx["score_comparison"]
    summary = ctx["summary"]
    kept_before_compress = ctx.get("kept_before_compress", len(kept))

    # 分离 Hindsight、知识树、SAG 结果
    hs_kept = [r for r in kept if r.get("source", "hindsight") == "hindsight"]
    kt_kept = [r for r in kept if r.get("source") == "knowledge_tree"]
    sag_kept = [r for r in kept if r.get("source") == "sag"]

    context_lines: list[str] = []

    # 1. 用户原始消息
    context_lines.append(f"<user_query>\n{html.escape(user_message, quote=False)}\n</user_query>")

    # 2. Hindsight 回忆
    if hs_kept:
        avg_score = sum(_candidate_score(r) for r in hs_kept) / max(len(hs_kept), 1)
        hs_xml = "\n".join(
            f'  <memory source="hindsight" node_id="{html.escape(str(r.get("id", "")), quote=True)}">'
            f'{html.escape(str(r.get("text", ""))[:CONFIG.max_text_length], quote=False)}</memory>'
            for r in hs_kept
        )
        context_lines.append(
            f"<recalled_memory source=\"hindsight\" count=\"{len(hs_kept)}\" score_avg=\"{avg_score:.2f}\">\n"
            f"{hs_xml}\n"
            f"</recalled_memory>"
        )

    # 3. 知识树节点
    if kt_kept:
        kt_xml = "\n".join(
            f'  <memory source="knowledge_tree" node_id="{html.escape(str(r.get("id", "")), quote=True)}">'
            f'{html.escape(str(r.get("text", ""))[:CONFIG.max_text_length], quote=False)}</memory>'
            for r in kt_kept
        )
        context_lines.append(
            f"<knowledge source=\"knowledge_tree\" count=\"{len(kt_kept)}\">\n"
            f"{kt_xml}\n"
            f"</knowledge>"
        )

    # 3.5 SAG 文档结果
    if sag_kept:
        sag_xml = "\n".join(
            f'  <memory source="sag" document_id="{html.escape(str(r.get("document_id", "")), quote=True)}">'
            f'{html.escape(str(r.get("text", ""))[:CONFIG.max_text_length], quote=False)}</memory>'
            for r in sag_kept
        )
        context_lines.append(
            f"<knowledge source=\"sag\" count=\"{len(sag_kept)}\"\n"
            f"{sag_xml}\n"
            f"</knowledge>"
        )

    # 4. 任务摘要（原样保留）
    if summary:
        context_lines.append(summary)

    # 5. 系统状态
    context_lines.append(
        f"<system_state>\n"
        f"pwd: {os.getcwd()}\n"
        f"time: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"</system_state>"
    )

    # 提取已召回并过滤后的 memory_id（用于离线 recall@k 评估）
    recalled_ids = [m.get("id", "") for m in kept if m.get("id")]

    # 统一使用 RecallLogger 输出汇总日志
    _recall_logger = ctx.get("recall_logger")
    if _recall_logger is not None:
        _compressed_from = kept_before_compress if CONFIG.enable_score_span_compress and kept_before_compress > len(kept) else None
        _compressed_to = len(kept) if _compressed_from is not None else None
        _summary_round = _task_tracker.current_round(session_id) if summary else None
        log_extra = _recall_logger.summary(
            kept_results=kept,
            session_id=session_id,
            query_trunc=query_trunc,
            excluded_count=excluded_count,
            kt_dedup_removed=kt_dedup_removed,
            total_chars=sum(len(line) for line in context_lines),
            injected_count=len(context_lines),
            score_comparison=score_comparison,
            eval_match=eval_match,
            total_latency_ms=latency_ms,
            compressed_from=_compressed_from,
            compressed_to=_compressed_to,
            task_summary_round=_summary_round,
            has_knowledge_tree=len(kt_raw_results) > 0,
        )
        log_extra["dropped_results"] = len(raw_results) - kept_before_kt
    else:
        # fallback：无 RecallLogger 时走旧逻辑
        log_extra: dict[str, Any] = {
            "session_id": session_id,
            "query_trunc": query_trunc,
            "event": "recall_success",
            "total_results": len(raw_results),
            "excluded_marked": excluded_count,
            "kept_results": len(kept),
            "dropped_results": len(raw_results) - kept_before_kt,
            "score_stats": calculate_score_stats([_candidate_score(r) for r in kept]),
            "injected_count": len(context_lines),
            "total_chars": sum(len(line) for line in context_lines),
            "has_knowledge_tree": len(kt_raw_results) > 0,
            "kt_dedup_removed": kt_dedup_removed,
            "latency_ms": latency_ms,
            "score_comparison": score_comparison,
            "recalled_ids": recalled_ids,
            "hs_kept": len(hs_kept),
            "kt_kept": len(kt_kept),
            "sag_kept": len(sag_kept),
        }
        if CONFIG.enable_score_span_compress and kept_before_compress > len(kept):
            log_extra["compressed_from"] = kept_before_compress
            log_extra["compressed_to"] = len(kept)
        if eval_match:
            log_extra["eval_match_method"] = eval_match.get("match_method", "none")
            log_extra["eval_match_confidence"] = round(float(eval_match.get("confidence", 0.0)), 4)
            log_extra["eval_counted"] = bool(eval_match.get("counted"))
            if eval_match.get("counted"):
                log_extra["eval_query_id"] = eval_match["query_id"]
                if eval_match["expected_ids"]:
                    expected_set = set(eval_match["expected_ids"])
                    log_extra["eval_expected_ids"] = eval_match["expected_ids"]
                    log_extra["eval_recall_hit"] = len(expected_set & set(recalled_ids))
                    log_extra["eval_recall_k"] = len(eval_match["expected_ids"])
            else:
                log_extra["eval_candidate_id"] = eval_match["query_id"]
        if summary:
            log_extra["task_summary_round"] = _task_tracker.current_round(session_id)
        logger.info("recall success", extra=log_extra)

    # 追加自动加载的技能内容
    if skill_context:
        context_lines.append(skill_context)

    if not context_lines:
        return None
    return "\n".join(context_lines)


def _post_process_recall(
    result: dict[str, Any] | None,
    kt_raw_results: list[dict[str, Any]],
    hs_active: bool,
    kt_active: bool,
    skill_context: str,
    session_id: str,
    user_message: str,
    query_trunc: str,
    t0: float,
    eval_match_param: dict | None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """后处理：降级 + 使用日志 + 过滤 + boost + 因果链 + 压缩 + 跨域去重 + KT对齐。

    Returns:
        (kept, meta) — kept 为 None 时调用方应早期返回。
        meta 包含 latency_ms, raw_results, excluded_count 等中间统计值。
    """
    # ===== Hindsight 失败/空结果时，知识树独立降级 =====
    if not result and hs_active:
        if not kt_raw_results:
            logger.info(
                "recall empty (Hindsight + KT)",
                extra={
                    "session_id": session_id,
                    "query_trunc": query_trunc,
                    "event": "recall_empty",
                    "latency_ms": int((time.time() - t0) * 1000),
                },
            )
            return None, {}
        logger.info(
            "Hindsight 无结果，使用 KT-only fallback",
            extra={"session_id": session_id, "query_trunc": query_trunc, "event": "hindsight_fail_kt_fallback", "kt_count": len(kt_raw_results)},
        )
        result = {"results": [], "trace": {}}

    if result is None:
        result = {"results": [], "trace": {}}

    latency_ms = int((time.time() - t0) * 1000)
    raw_results = result.get("results", [])

    if not raw_results and not kt_raw_results and not skill_context:
        if hs_active:
            circuit_record_success()
        logger.info(
            "recall empty results",
            extra={
                "session_id": session_id,
                "query_trunc": query_trunc,
                "event": "recall_empty_results",
                "latency_ms": latency_ms,
            },
        )
        return None, {}

    if raw_results:
        circuit_record_success()

    filtered_raw, excluded_count = exclude_marked(raw_results) if raw_results else ([], 0)
    trace_data = result.get("trace", {})
    rerank_map = extract_rerank_scores(trace_data) if raw_results else {}

    if rerank_map and filtered_raw:
        for r in filtered_raw:
            nid = r.get("id", "")
            score = rerank_map.get(nid)
            if score is not None:
                r.setdefault("score", score)
                r.setdefault("rerank_score", score)

    if filtered_raw:
        _hit_counter.boost_scores(filtered_raw, rerank_map)

    if CONFIG.enable_causal_chain and rerank_map:
        try:
            _causal_boost(filtered_raw, rerank_map, CONFIG.causal_boost_alpha, CONFIG.causal_boost_cap)
        except Exception:
            logger.debug("causal_boost 异常（非关键路径，跳过）")

    effective_max = _compaction.get_effective_max_results(session_id, CONFIG.max_results)
    mentioned_at_map = _build_mentioned_at_map(filtered_raw)
    kept, all_scores, score_comparison = filter_by_score(
        filtered_raw, rerank_map,
        min_score=CONFIG.min_score,
        max_results=effective_max,
        enable_temporal=CONFIG.enable_temporal,
        mentioned_at_map=mentioned_at_map,
    )

    kept_before_compress = len(kept)

    if CONFIG.enable_score_span_compress and kept:
        from knowledge_navigation.core.filtering import extract_ce_raw_scores, compress_by_score_span
        ce_raw_map = extract_ce_raw_scores(trace_data)
        kept = compress_by_score_span(
            kept, ce_raw_map, effective_max,
            CONFIG.score_span_top3_threshold,
            CONFIG.score_span_half_threshold,
        )
    kept_before_kt = len(kept)

    if eval_match_param is None:
        eval_match = _match_eval_query(user_message) if CONFIG.eval_match_enabled else None
    else:
        eval_match = eval_match_param

    summary = _task_tracker.get_summary_prompt(session_id)

    if not kept and summary is None:
        if not kt_active:
            return None, {}
        # 知识树可能返回结果，继续

    kt_dedup_removed = 0
    if kt_raw_results and kept:
        dedup_action = CONFIG.cross_domain_dedup_action
        dedup_demote_factor = CONFIG.cross_domain_dedup_demote_factor
        if CONFIG.cross_domain_dedup_mode == "text_embedding":
            kt_raw_results, kt_dedup_removed = cross_domain_dedup(
                hindsight_results=kept, kt_results=kt_raw_results,
                threshold=0.65, embed_fn=_batch_embed,
                action=dedup_action, demote_factor=dedup_demote_factor,
            )
        else:
            kt_raw_results, kt_dedup_removed = cross_domain_dedup(
                hindsight_results=kept, kt_results=kt_raw_results,
                threshold=0.65, embed_fn=None,
                action=dedup_action, demote_factor=dedup_demote_factor,
            )
        if kt_dedup_removed:
            logger.info(
                "跨域去重（%s）移除 %d 条知识树重复结果",
                dedup_action, kt_dedup_removed,
                extra={"session_id": session_id, "kt_dedup_removed": kt_dedup_removed},
            )

    for kp in kt_raw_results:
        candidate = _build_knowledge_tree_candidate(kp)
        if candidate:
            kept.append(candidate)

    return kept, {
        "latency_ms": latency_ms,
        "raw_results": raw_results,
        "excluded_count": excluded_count,
        "kept_before_kt": kept_before_kt,
        "kt_dedup_removed": kt_dedup_removed,
        "score_comparison": score_comparison,
        "kept_before_compress": kept_before_compress,
        "summary": summary,
        "eval_match": eval_match,
        "kt_raw_results": kt_raw_results,
    }


def pre_llm_call(session_id: str, user_message: str, **kwargs: Any) -> str | None:
    """每次 LLM 调用前自动执行：三层门控 → LLM Router → 三路 mask 条件执行 → 后处理注入。"""

    # ===== 门控阶段（三层门控 + eval bypass）=====
    _should_continue, _eval_match = _pass_gates(
        session_id,
        user_message,
        kwargs.get("platform", ""),
        kwargs.get("is_first_turn", False),
    )
    if not _should_continue:
        return None

    # 熔断器：连续失败后跳过 Hindsight recall（知识树不受熔断影响）
    _hs_circuit_open = False
    if circuit_is_open():
        logger.info("熔断器跳过 Hindsight recall，知识树仍尝试")
        _hs_circuit_open = True

    # ===== LLM Router: 三路 mask 决策 =====
    mask = _get_router_mask(session_id, user_message)

    # 全 false → 不注入任何内容
    if not any(mask.values()):
        logger.info(
            "Router 全关闭，跳过所有 recall",
            extra={"session_id": session_id, "event": "skip_router_all_off"},
        )
        return None

    t0 = time.time()
    query_trunc = user_message[:60]

    _hs_active = mask["h"] and not _hs_circuit_open
    _kt_active = mask["kt"] and (HAS_KNOWLEDGE_TREE or _ensure_kt_imported())
    _s_active = mask["s"]
    _sag_active = mask.get("sag", False)
    _active_count = sum([_hs_active, _kt_active, _s_active, _sag_active])

    _recall_logger = RecallLogger(use_logger=_get_use_logger())

    result, kt_raw_results, _skill_context, sag_raw_results = _execute_recall(
        session_id, user_message,
        _hs_active, _kt_active, _s_active, _sag_active, _active_count,
        t0, query_trunc, _recall_logger,
    )

    # 多跳关联展开
    kt_raw_results = _expand_multi_hop(kt_raw_results, _kt_active, session_id)

    # ===== 后处理（降级 + 过滤 + boost + 因果链 + 压缩 + 跨域去重 + KT对齐）=====
    _pp_result, _pp_meta = _post_process_recall(
        result, kt_raw_results, _hs_active, _kt_active, _skill_context,
        session_id, user_message, query_trunc, t0, _eval_match,
    )
    if _pp_result is None:
        if sag_raw_results and _sag_active:
            kept = []
            latency_ms = int((time.time() - t0) * 1000)
            raw_results = []
            excluded_count = 0
            kept_before_kt = 0
            kt_dedup_removed = 0
            score_comparison = {}
            kept_before_compress = 0
            summary = None
            eval_match = _eval_match
        else:
            return _skill_context if _skill_context else None
    else:
        kept = _pp_result
        latency_ms = _pp_meta["latency_ms"]
        raw_results = _pp_meta["raw_results"]
        excluded_count = _pp_meta["excluded_count"]
        kept_before_kt = _pp_meta["kept_before_kt"]
        kt_dedup_removed = _pp_meta["kt_dedup_removed"]
        score_comparison = _pp_meta["score_comparison"]
        kept_before_compress = _pp_meta.get("kept_before_compress", len(kept))
        summary = _pp_meta.get("summary")
        eval_match = _pp_meta.get("eval_match", _eval_match)
        kt_raw_results = _pp_meta.get("kt_raw_results", kt_raw_results)

    # ===== SAG 结果合并（4th route）=====
    if sag_raw_results and _sag_active:
        sag_count = 0
        sag_candidates: list[dict[str, Any]] = []
        for sec in sag_raw_results:
            try:
                raw_score = float(sec.get("score", 0.5))
            except (TypeError, ValueError):
                raw_score = 0.5
            content = sec.get("content", "")
            heading = sec.get("heading", "")
            # 超长内容改指针模式：不注入全文，注入精简指针
            if len(content) > CONFIG.sag_pointer_threshold:
                preview = content[:80].replace("\n", " ").strip()
                text = (
                    f"[SAG 指针] heading: {heading} | score: {raw_score:.2f} | "
                    f"preview: {preview}... | "
                    f"如需完整内容，使用 sag_search 工具查询: {heading}"
                )
            else:
                text = content
            candidate = {
                "id": sec.get("chunkId", f"sag_{sag_count}"),
                "text": text,
                "score": raw_score,
                "base_score": raw_score,
                "final_score": raw_score,
                "rerank_score": raw_score,
                "source": "sag",
                "heading": heading,
                "document_id": sec.get("documentId", ""),
            }
            if candidate["final_score"] >= CONFIG.sag_min_score:
                sag_candidates.append(candidate)
                sag_count += 1
        # 按分数排序取 top N，防止 multi-hop 20+ 条灌入
        sag_candidates.sort(key=lambda x: x["final_score"], reverse=True)
        sag_candidates = sag_candidates[: CONFIG.sag_max_inject]
        kept.extend(sag_candidates)

        logger.info(
            "SAG recall: %d sections merged (capped to %d)",
            sag_count,
            len(sag_candidates),
            extra={"session_id": session_id, "event": "sag_merge", "count": len(sag_candidates)},
        )

    kept, _skill_context = _dedup_and_budget(kept, session_id, _skill_context)

    return _assemble_xml_output(kept, _skill_context, session_id, user_message, {
        "query_trunc": query_trunc,
        "eval_match": eval_match,
        "raw_results": raw_results,
        "latency_ms": latency_ms,
        "kt_raw_results": kt_raw_results,
        "excluded_count": excluded_count,
        "kept_before_kt": kept_before_kt,
        "kt_dedup_removed": kt_dedup_removed,
        "score_comparison": score_comparison,
        "summary": summary,
        "kept_before_compress": kept_before_compress,
        "recall_logger": _recall_logger,
    })
