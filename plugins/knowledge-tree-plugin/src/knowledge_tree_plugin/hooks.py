"""Hermes Hook 实现 — post_llm_call 异步增量学习。

post_llm_call:
    LLM 响应后 → 轻量门控 → 入后台队列 → 异步提取知识点 → 增量放置到知识树

设计原则：知识学习是增强链路，不能阻塞主对话返回。
"""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass
from typing import Any

from knowledge_tree_plugin.adapters.database import PluginDatabaseAdapter
from knowledge_tree_plugin.config import PluginConfig
from knowledge_tree_plugin.extract_new import extract_from_dialog
from knowledge_tree_plugin.placement import place_new_knowledge_points

logger = logging.getLogger(__name__)

# 解耦 closure：让步 knowledge-tree-plugin 可在缺少 knowledge-navigation 时独立运行
def _load_turn_gate():
    """懒加载 turn_gate 门控函数，避免硬依赖 knowledge-navigation 导致的 import 失败。"""
    try:
        from knowledge_navigation.turn_gate import skip_non_user  # type: ignore[import-untyped]
        from knowledge_navigation.turn_gate import skip_post_llm_call as _skip_post
        from knowledge_navigation.turn_gate import skip_system_prompt
        return skip_non_user, _skip_post, skip_system_prompt
    except ImportError:
        logger.warning("knowledge_navigation.turn_gate 不可用，门控降级为全放行")
        def _passthrough(*args, **kwargs):
            return None
        def _passthrough_false(*args, **kwargs):
            return False
        return _passthrough, _passthrough, _passthrough_false

_skip_non_user, _skip_post_llm_call_fn, _skip_system_prompt = _load_turn_gate()

# 模块级全局状态（懒加载，线程安全）
_config: PluginConfig | None = None
_adapter: PluginDatabaseAdapter | None = None
_init_lock = threading.Lock()

# 后台提取队列：post hook 只入队，不同步执行 LLM 提取
_QUEUE_MAXSIZE = 100
_task_queue: queue.Queue["ExtractTask"] = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_worker_started = False
_worker_lock = threading.Lock()
_extract_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kt-extract-timeout")

# 轻量门控：匹配明显执行状态/日志/命令输出，避免无意义 LLM 提取
_STATUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(部署完成|测试通过|运行正常|gateway.*active|service.*active)", re.IGNORECASE),
    re.compile(r"(回滚命令|备份目录|文件级备份|防残留扫描)"),
    re.compile(r"(Syntax OK|\d+ passed in \d+(\.\d+)?s|DELETE \d+|INSERT 0 \d+)", re.IGNORECASE),
)
_CODE_OR_LOG_HINTS: tuple[str, ...] = (
    "Traceback",
    "diff --git",
    "--- a/",
    "+++ b/",
    "psql ",
    "systemctl ",
    "./deploy/",
    "```",
)


@dataclass(frozen=True)
class ExtractTask:
    """后台知识提取任务。"""

    session_id: str
    user_message: str
    assistant_response: str


def _get_config() -> PluginConfig:
    """懒加载插件配置（双重检查锁）。"""
    global _config
    if _config is None:
        with _init_lock:
            if _config is None:
                config_path = os.environ.get(
                    "KT_PLUGIN_CONFIG",
                    "/root/.hermes/plugins/knowledge-tree-plugin/config/default.yaml",
                )
                _config = PluginConfig.load(config_path)
    return _config


def _get_adapter() -> PluginDatabaseAdapter:
    """懒加载 PG 适配器（双重检查锁）。"""
    global _adapter
    if _adapter is None:
        with _init_lock:
            if _adapter is None:
                cfg = _get_config()
                if not cfg.db_url:
                    db_url = os.environ.get("KT_DB_URL", "")
                    if not db_url:
                        raise RuntimeError(
                            "KT_DB_URL 未配置。设置环境变量或 config/default.yaml 中的 db_url"
                        )
                    cfg.db_url = db_url
                _adapter = PluginDatabaseAdapter(cfg.db_url)
    return _adapter


def should_skip_extraction(user_message: str, assistant_response: str) -> str:
    """cheap gate：判断本轮是否明显没有知识提取价值。

    只做字符串/正则判断，避免额外 API 调用。
    返回空字符串表示不跳过，非空为跳过原因。
    """
    response = (assistant_response or "").strip()
    if not response:
        return "响应为空"

    # 短状态汇报/部署输出通常是经验轨迹，不是知识事实。
    if len(response) < 600:
        for pat in _STATUS_PATTERNS:
            if pat.search(response):
                return "执行状态类响应"

    # 命令/日志/代码块占比过高，优先交给 Hindsight 经验区，不进入知识树。
    hint_count = sum(1 for hint in _CODE_OR_LOG_HINTS if hint in response)
    if hint_count >= 2:
        return "命令日志类响应"

    # Markdown 代码块多且解释文本少，跳过提取。
    code_fences = response.count("```")
    if code_fences >= 4 and len(response) < 2000:
        return "代码块为主响应"

    return ""


def _ensure_worker_started() -> None:
    """确保后台 worker 已启动。"""
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(
            target=_worker_loop,
            name="knowledge-tree-extract-worker",
            daemon=True,
        )
        thread.start()
        _worker_started = True


def _worker_loop() -> None:
    """后台 worker 主循环。"""
    while True:
        task = _task_queue.get()
        try:
            _process_extract_task(task)
        except Exception as e:
            logger.warning(
                "knowledge-tree background task failed: %s",
                e,
                extra={"session_id": task.session_id, "event": "kt_background_failed"},
            )
        finally:
            _task_queue.task_done()


def _extract_with_timeout(task: ExtractTask, cfg: PluginConfig) -> list[str]:
    """带 hard timeout 的 LLM 提取。超时返回空列表。"""
    request_timeout_seconds = getattr(cfg, "extract_llm_timeout_seconds", 30)
    llm_retries = max(1, getattr(cfg, "extract_llm_retries", 1))
    # knowledge-tree-builder 的 llm_retries 是总尝试次数；内部每次重试前后有指数退避。
    retry_backoff_seconds = sum((2**attempt) + (2 ** (attempt - 1)) for attempt in range(1, llm_retries))
    hard_timeout_seconds = request_timeout_seconds * llm_retries + retry_backoff_seconds + 5
    future = _extract_executor.submit(
        extract_from_dialog,
        user_message=task.user_message,
        llm_response=task.assistant_response,
        min_length=cfg.min_knowledge_point_length,
        max_input_length=cfg.extract_max_input_length,
        api_url=cfg.llm_api_url,
        api_key=cfg.llm_api_key,
        model=cfg.llm_model,
        llm_retries=llm_retries,
        llm_timeout_seconds=request_timeout_seconds,
    )
    try:
        return future.result(timeout=hard_timeout_seconds)
    except FuturesTimeout:
        future.cancel()
        logger.warning(
            "post_llm_call 知识提取超时，跳过本轮",
            extra={
                "session_id": task.session_id,
                "event": "extract_timeout",
                "timeout_seconds": hard_timeout_seconds,
            },
        )
        return []


def _process_extract_task(task: ExtractTask) -> None:
    """执行后台知识提取与放置。"""
    try:
        cfg = _get_config()
    except Exception as e:
        logger.debug("post_llm_call 配置加载失败: %s", e)
        return

    if not cfg.extract_enabled:
        return

    total_len = len(task.user_message) + len(task.assistant_response)
    if total_len < cfg.extract_min_dialog_length:
        logger.debug(
            "post_llm_call: 对话太短 (%d < %d)，跳过",
            total_len,
            cfg.extract_min_dialog_length,
        )
        return

    t0 = time.time()
    new_points = _extract_with_timeout(task, cfg)
    if not new_points:
        logger.debug(
            "post_llm_call: 无新知识点",
            extra={"session_id": task.session_id, "event": "no_extracted_kp"},
        )
        return

    try:
        adapter = _get_adapter()
        result = place_new_knowledge_points(
            new_points=new_points,
            adapter=adapter,
            session_id=task.session_id,
            user_message=task.user_message,
            embed_base_url=cfg.embed_base_url,
            embed_model=cfg.embed_model,
            embed_api_key=cfg.embed_api_key,
            embed_batch_size=cfg.embed_batch_size,
            dedup_threshold=cfg.dedup_cosine_threshold,
            conflict_threshold=cfg.conflict_cosine_threshold,
            cold_start_threshold=cfg.cold_start_threshold,
            k_vector_alpha_max=cfg.k_vector_alpha_max,
        )
    except Exception as e:
        logger.warning(
            "增量放置失败",
            extra={
                "session_id": task.session_id,
                "error": f"{type(e).__name__}: {e}",
                "extracted_count": len(new_points),
            },
        )
        return

    latency_ms = int((time.time() - t0) * 1000)
    logger.info(
        "post_llm_call 知识提取完成",
        extra={
            "session_id": task.session_id,
            "event": "post_llm_extract",
            "extracted_count": len(new_points),
            "new_nodes": result.get("new_nodes", 0),
            "dedup_merged": result.get("dedup_merged", 0),
            "conflicts": result.get("conflicts", 0),
            "parent_id": result.get("parent_id"),
            "latency_ms": latency_ms,
        },
    )


# pre_llm_call 已移除：知识树 recall 由 knowledge-navigation 插件通过
# public_api.recall_from_tree_raw() 统一调用，无需本插件注册。


def post_llm_call(
    session_id: str,
    user_message: str,
    assistant_response: str,
    **kwargs: Any,
) -> None:
    """每次 LLM 调用后，将知识提取任务入队并立即返回。"""
    logger.debug(
        "post_llm_call 被调用",
        extra={"session_id": session_id, "event": "post_llm_entered"},
    )

    user_message = user_message or ""
    assistant_response = assistant_response or ""

    if _skip_non_user(kwargs.get("platform", "")):
        logger.debug(
            "非用户平台跳过 post_llm_call",
            extra={"session_id": session_id, "event": "skip_non_user"},
        )
        return

    if _skip_system_prompt(user_message, kwargs.get("is_first_turn", False)):
        logger.debug(
            "系统提示词跳过 post_llm_call",
            extra={"session_id": session_id, "event": "skip_system_prompt"},
        )
        return

    skip_reason = _skip_post_llm_call_fn(assistant_response)
    if skip_reason:
        logger.debug(
            "turn_gate 跳过 post_llm_call: %s",
            skip_reason,
            extra={"session_id": session_id, "event": "skip_operational"},
        )
        return

    cheap_reason = should_skip_extraction(user_message, assistant_response)
    if cheap_reason:
        logger.debug(
            "cheap gate 跳过知识提取: %s",
            cheap_reason,
            extra={"session_id": session_id, "event": "skip_cheap_gate"},
        )
        return

    task = ExtractTask(session_id, user_message, assistant_response)
    try:
        _ensure_worker_started()
        _task_queue.put_nowait(task)
        logger.debug(
            "post_llm_call 知识提取任务已入队",
            extra={
                "session_id": session_id,
                "event": "kt_task_enqueued",
                "queue_size": _task_queue.qsize(),
            },
        )
    except queue.Full:
        logger.warning(
            "知识树提取队列已满，跳过本轮",
            extra={"session_id": session_id, "event": "kt_queue_full"},
        )
