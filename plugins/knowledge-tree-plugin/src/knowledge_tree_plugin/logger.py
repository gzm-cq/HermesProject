"""日志工具 — JSON 格式日志组件。

复用 knowledge-navigation 插件的 JSONFormatter 模式。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """统一 JSON 日志格式器。

    输出格式：{timestamp, level, logger, message, module, line, ...extra}
    extra 字段通过 logger.info(..., extra={...}) 传入，会被展开为 LogRecord 属性。
    """

    # Python logging.LogRecord 的标准属性，不属于 extra
    _STANDARD_ATTRS = frozenset({
        "name", "msg", "args", "created", "relativeCreated",
        "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "filename", "module", "pathname", "thread", "threadName",
        "process", "processName", "levelname", "levelno", "message",
        "msecs", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        # 收集 extra 字段：非标准属性且不以 _ 开头的都是用户 extra
        for key, value in record.__dict__.items():
            if key not in self._STANDARD_ATTRS and not key.startswith("_"):
                log_entry[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False, default=str)


def setup_logging(
    name: str = "knowledge_tree_plugin",
    level: int = logging.INFO,
) -> logging.Logger:
    """初始化 JSON 日志。

    Args:
        name: 日志器名称
        level: 日志级别

    Returns:
        配置好的日志器实例
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 handler
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)

    return logger
