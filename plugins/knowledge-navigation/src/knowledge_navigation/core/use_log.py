"""记忆使用日志模块（P2-2 Phase A）。

提供 UseLogger 类，管理使用日志的内存缓冲和批量写入。
写入 JSONL 文件，默认路径 ~/.hermes/knowledge_use_log.jsonl。
失败时静默降级，不影响主功能。
"""

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_LOG_PATH = os.path.expanduser("~/.hermes/knowledge_use_log.jsonl")


class UseLogger:
    """记忆使用日志记录器。

    支持批量写入、定时刷盘，失败时静默降级。
    线程安全：log_recall / log_usage / flush 可在多线程环境下调用。
    """

    def __init__(
        self,
        enabled: bool = True,
        batch_size: int = 10,
        flush_interval_seconds: int = 30,
        log_path: str = "",
    ) -> None:
        self._enabled = enabled
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._log_path = log_path or _DEFAULT_LOG_PATH

        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_flush_ts = time.time()
        self._timer: threading.Timer | None = None
        self._shutdown = False

        if self._enabled:
            self._ensure_dir()
            self._schedule_timer()

    def _ensure_dir(self) -> None:
        """确保日志目录存在。"""
        try:
            log_dir = os.path.dirname(self._log_path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
        except Exception as e:
            logger.debug("UseLogger: create log dir failed: %s", e)

    def _schedule_timer(self) -> None:
        """启动定时刷盘定时器。"""
        if self._flush_interval <= 0 or self._shutdown:
            return
        try:
            self._timer = threading.Timer(self._flush_interval, self._timer_flush)
            self._timer.daemon = True
            self._timer.start()
        except Exception as e:
            logger.debug("UseLogger: schedule timer failed: %s", e)

    def _timer_flush(self) -> None:
        """定时器触发的刷盘操作。"""
        try:
            self.flush()
        finally:
            if not self._shutdown:
                self._schedule_timer()

    def log_recall(
        self,
        query: str,
        results: list[dict],
        source: str,
        session_id: str = "",
    ) -> None:
        """记录一次召回。

        Args:
            query: 用户查询
            results: 召回结果列表，每个元素应包含 id/score 或类似字段
            source: 来源（hindsight / knowledge_tree / skill）
            session_id: 会话 ID
        """
        if not self._enabled or self._shutdown:
            return

        try:
            entry = self._build_recall_entry(query, results, source, session_id)
            with self._lock:
                self._buffer.append(entry)
                if len(self._buffer) >= self._batch_size:
                    self._flush_locked()
        except Exception as e:
            logger.debug("UseLogger: log_recall failed silently: %s", e)

    def log_usage(
        self,
        node_id: str,
        source: str,
        query: str = "",
        session_id: str = "",
    ) -> None:
        """记录单条记忆被使用。

        注：LLM 引用信号暂不实现，此方法为未来扩展预留。

        Args:
            node_id: 记忆节点 ID
            source: 来源
            query: 关联的查询
            session_id: 会话 ID
        """
        if not self._enabled or self._shutdown:
            return

        try:
            entry = {
                "ts": int(time.time()),
                "session_id": session_id,
                "query": query,
                "source": source,
                "type": "usage",
                "node_id": node_id,
            }
            with self._lock:
                self._buffer.append(entry)
                if len(self._buffer) >= self._batch_size:
                    self._flush_locked()
        except Exception as e:
            logger.debug("UseLogger: log_usage failed silently: %s", e)

    def _build_recall_entry(
        self,
        query: str,
        results: list[dict],
        source: str,
        session_id: str,
    ) -> dict[str, Any]:
        """构建 recall 日志条目。"""
        result_list = []
        for r in results:
            node_id = str(r.get("id", r.get("node_id", "")))
            score = None
            for key in ("final_score", "rerank_score", "base_score", "score", "tree_score"):
                val = r.get(key)
                if val is not None:
                    try:
                        score = float(val)
                        break
                    except (TypeError, ValueError):
                        continue
            result_list.append({
                "node_id": node_id,
                "score": score if score is not None else 0.0,
            })

        return {
            "ts": int(time.time()),
            "session_id": session_id,
            "query": query,
            "source": source,
            "type": "recall",
            "results": result_list,
        }

    def flush(self) -> None:
        """强制刷盘。"""
        if not self._enabled or self._shutdown:
            return
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        """刷盘（调用方需持有 _lock）。"""
        if not self._buffer:
            self._last_flush_ts = time.time()
            return

        # 原子交换 buffer，避免在持有锁期间进行 I/O
        buffer_to_write = self._buffer[:]  # 复制一份，防止外部修改
        self._buffer = []
        self._last_flush_ts = time.time()

        try:
            self._write_buffer(buffer_to_write)
        except Exception as e:
            logger.debug("UseLogger: flush write failed silently: %s", e)

    def _write_buffer(self, entries: list[dict]) -> None:
        """将缓冲区写入 JSONL 文件。"""
        with open(self._log_path, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def close(self) -> None:
        """关闭 logger，刷盘剩余日志并停止定时器。"""
        if self._shutdown:
            return
        try:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        except Exception:
            pass
        try:
            self.flush()
        except Exception:
            pass
        self._shutdown = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def buffer_size(self) -> int:
        with self._lock:
            return len(self._buffer)
