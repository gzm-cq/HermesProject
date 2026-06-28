"""配置管理 — 支持 YAML + ENV 覆盖"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

import yaml

logger = logging.getLogger(__name__)


def _safe_int_env(key: str, field_name: str, values: dict) -> None:
    """安全解析整型环境变量，失败时记录警告。"""
    v = os.getenv(key)
    if v is None:
        return None
    try:
        values[field_name] = int(v)
    except ValueError:
        logger.warning("环境变量 %s 不是有效整数: %r，已忽略", key, v)


def _safe_float_env(key: str, field_name: str, values: dict) -> None:
    """安全解析浮点型环境变量，失败时记录警告。"""
    v = os.getenv(key)
    if v is None:
        return None
    try:
        values[field_name] = float(v)
    except ValueError:
        logger.warning("环境变量 %s 不是有效浮点数: %r，已忽略", key, v)


def _safe_bool_env(key: str, field_name: str, values: dict) -> None:
    """安全解析布尔型环境变量，失败时记录警告。"""
    v = os.getenv(key)
    if v is None:
        return None
    v_lower = v.lower()
    if v_lower in ("true", "1", "yes", "on"):
        values[field_name] = True
    elif v_lower in ("false", "0", "no", "off"):
        values[field_name] = False
    else:
        logger.warning("环境变量 %s 不是有效布尔值: %r，已忽略", key, v)


class JSONFormatter(logging.Formatter):
    """统一 JSON 日志格式器。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """初始化日志系统。"""
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )


def _resolve_config_path(config_path: str) -> Path:
    """解析配置文件路径为绝对路径。

    优先级：
    1. 绝对路径，直接使用
    2. 相对路径存在，解析为绝对路径
    3. 包安装位置向上查找
    4. RECALL_EVAL_CONFIG_DIR 环境变量
    """
    path = Path(config_path)
    if path.is_absolute():
        return path.resolve()

    if path.exists():
        return path.resolve()

    try:
        import recall_eval

        pkg_root = Path(recall_eval.__file__).parent.parent.parent
        candidate = (pkg_root / path).resolve()
        if candidate.exists():
            return candidate
    except (ImportError, AttributeError):
        pass

    env_dir = os.getenv("RECALL_EVAL_CONFIG_DIR")
    if env_dir:
        candidate = Path(env_dir) / path
        if candidate.exists():
            return candidate.resolve()

    return path.resolve()


@dataclass
class AppConfig:
    """应用配置，支持 ENV 变量覆盖。"""

    # ── 输出模式 ──
    output_mode: str = "human"

    # ── 文件路径 ──
    dataset_path: str = "data/eval_queries.json"
    output_path: str = "reports"

    # ── LLM 评估参数 ──
    eval_model: str = "s-deepseek-v4-flash"
    eval_api_url: str = "http://127.0.0.1:4142/v1/chat/completions"
    eval_api_key: str = ""

    # ── Hindsight API ──
    hindsight_url: str = "http://127.0.0.1:9177/v1/default/banks/hermes/memories/search"

    # ── 并发与批处理 ──
    batch_size: int = 10
    max_workers: int = field(default_factory=lambda: min(8, (os.cpu_count() or 4) + 2))

    # ── 日志 ──
    log_level: str = "INFO"

    # ── 校验配置 ──
    _VALID_OUTPUT_MODES: ClassVar[set[str]] = {"human", "json"}

    def __post_init__(self) -> None:
        """校验配置值的合法性。"""
        if self.batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {self.batch_size}")
        if self.max_workers < 1:
            raise ValueError(f"max_workers must be >= 1, got {self.max_workers}")
        if self.output_mode not in self._VALID_OUTPUT_MODES:
            raise ValueError(
                f"output_mode must be one of {self._VALID_OUTPUT_MODES}, got {self.output_mode!r}"
            )

    @classmethod
    def from_env(cls, defaults: dict[str, Any] | None = None) -> "AppConfig":
        """从环境变量加载配置，覆盖默认值。

        优先级：ENV 变量 > 传入的 defaults > 代码默认值。
        """
        values: dict[str, Any] = dict(defaults) if defaults else {}

        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        values = {k: v for k, v in values.items() if k in valid_fields}

        if v := os.getenv("RECALL_EVAL_DATASET_PATH"):
            values["dataset_path"] = v
        if v := os.getenv("RECALL_EVAL_OUTPUT_PATH"):
            values["output_path"] = v
        if v := os.getenv("RECALL_EVAL_MODEL"):
            values["eval_model"] = v
        if v := os.getenv("RECALL_EVAL_API_URL"):
            values["eval_api_url"] = v
        if v := os.getenv("LITELLM_MASTER_KEY"):
            values["eval_api_key"] = v
        if v := os.getenv("RECALL_EVAL_HINDSIGHT_URL"):
            values["hindsight_url"] = v
        _safe_int_env("RECALL_EVAL_BATCH_SIZE", "batch_size", values)
        _safe_int_env("RECALL_EVAL_MAX_WORKERS", "max_workers", values)
        if v := os.getenv("RECALL_EVAL_LOG_LEVEL"):
            values["log_level"] = v
        if v := os.getenv("RECALL_EVAL_OUTPUT_MODE"):
            values["output_mode"] = v

        return cls(**values)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        """从字典创建配置实例，忽略未知字段。"""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


def load_config(config_path: str) -> dict[str, Any]:
    """从 YAML 文件加载配置，支持 ENV 覆盖。

    优先级：ENV 变量 > YAML 文件 > 代码默认值
    """
    config: dict[str, Any] = {}

    resolved_path = _resolve_config_path(config_path)
    if resolved_path.exists():
        with open(resolved_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    return config


CONFIG = AppConfig.from_env()
