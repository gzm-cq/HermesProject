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


def _safe_int_env(key: str, field: str, values: dict) -> None:
    """安全解析整型环境变量，失败时记录警告。"""
    v = os.getenv(key)
    if v is None:
        return
    try:
        values[field] = int(v)
    except ValueError:
        logger.warning("环境变量 %s 不是有效整数: %r，已忽略", key, v)


def _safe_float_env(key: str, field: str, values: dict) -> None:
    """安全解析浮点型环境变量，失败时记录警告。"""
    v = os.getenv(key)
    if v is None:
        return
    try:
        values[field] = float(v)
    except ValueError:
        logger.warning("环境变量 %s 不是有效浮点数: %r，已忽略", key, v)


def _safe_bool_env(key: str, field: str, values: dict) -> None:
    """安全解析布尔型环境变量，失败时记录警告。"""
    v = os.getenv(key)
    if v is None:
        return
    v_lower = v.lower()
    if v_lower in ("true", "1", "yes", "on"):
        values[field] = True
    elif v_lower in ("false", "0", "no", "off"):
        values[field] = False
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
    4. MEMORY_CLEANUP_CONFIG_DIR 环境变量
    """
    path = Path(config_path)
    if path.is_absolute():
        return path.resolve()

    if path.exists():
        return path.resolve()

    try:
        import memory_cleanup

        pkg_root = Path(memory_cleanup.__file__).parent.parent.parent
        candidate = (pkg_root / path).resolve()
        if candidate.exists():
            return candidate
    except (ImportError, AttributeError):
        pass

    env_dir = os.getenv("MEMORY_CLEANUP_CONFIG_DIR")
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
    memory_path: str = "/root/.hermes/memories/MEMORY.md"
    user_path: str = "/root/.hermes/memories/USER.md"
    session_db_path: str = "/root/.hermes/state.db"
    hermes_agent_path: str = "/root/.hermes/hermes-agent"

    # ── LLM 参数 ──
    llm_url: str = "http://127.0.0.1:4142/v1/chat/completions"
    llm_key: str = ""
    llm_model: str = "s-deepseek-v4-flash"

    # ── Hindsight API ──
    hindsight_url: str = "http://127.0.0.1:9177/v1/default/banks/hermes/memories"

    # ── 并发与批处理 ──
    batch_size: int = 10
    user_batch_size: int = 10
    vote_count: int = 1
    max_workers: int = field(default_factory=lambda: min(32, (os.cpu_count() or 4) + 4))

    # ── 字符限制 ──
    memory_char_limit: int = 50000
    user_char_limit: int = 15000

    # ── 条目分隔符 ──
    entry_delimiter: str = "\n§\n"

    # ── 日志 ──
    log_level: str = "INFO"

    # ── 压缩质量校验 ──
    compress_strict_mode: bool = True
    compress_min_ratio_memory: float = 8.0
    compress_min_ratio_user: float = 5.0
    compress_keyword_overlap_memory: float = 0.30
    compress_keyword_overlap_user: float = 0.25
    compress_entity_retention_memory: float = 0.50

    # ── Hindsight 关键词回填 ──
    keyword_backfill: bool = True
    hindsight_keyword_count: int = 5

    # ── 生命周期管理（P3-4） ──
    cold_memory_eviction: bool = False
    cold_memory_days: int = 30
    hot_memory_promotion: bool = False
    hot_memory_access_count: int = 10
    l2_max_entries: int = 200

    # ── 生命周期关键词（从配置读取，避免硬编码） ──
    lifecycle_frequency_keywords: list[str] = field(default_factory=lambda: [
        "经常", "常常", "总是", "每次", "日常", "每天", "每周", "常用",
        "frequently", "often", "always", "daily", "weekly", "regularly",
        "常用工具", "常用命令", "偏好", "喜欢",
    ])
    lifecycle_historical_keywords: list[str] = field(default_factory=lambda: [
        "年", "月", "日",
        "完成", "建于", "始于", "创建于", "发布于",
        "之前", "以后", "以来",
    ])
    lifecycle_recent_keywords: list[str] = field(default_factory=lambda: [
        "经常", "每天", "每周", "日常", "常用", "偏好",
        "frequently", "daily", "regularly", "often",
    ])

    # ── 校验配置 ──
    _VALID_OUTPUT_MODES: ClassVar[set[str]] = {"human", "json"}

    def __post_init__(self) -> None:
        """校验配置值的合法性。"""
        if self.batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {self.batch_size}")
        if self.user_batch_size < 1:
            raise ValueError(f"user_batch_size must be >= 1, got {self.user_batch_size}")
        if self.vote_count < 1:
            raise ValueError(f"vote_count must be >= 1, got {self.vote_count}")
        if self.max_workers < 1:
            raise ValueError(f"max_workers must be >= 1, got {self.max_workers}")
        if self.memory_char_limit < 100:
            raise ValueError(f"memory_char_limit must be >= 100, got {self.memory_char_limit}")
        if self.user_char_limit < 100:
            raise ValueError(f"user_char_limit must be >= 100, got {self.user_char_limit}")
        if self.output_mode not in self._VALID_OUTPUT_MODES:
            raise ValueError(f"output_mode must be one of {self._VALID_OUTPUT_MODES}, got {self.output_mode!r}")
        if self.compress_min_ratio_memory < 1.0:
            raise ValueError(f"compress_min_ratio_memory must be >= 1.0, got {self.compress_min_ratio_memory}")
        if self.compress_min_ratio_user < 1.0:
            raise ValueError(f"compress_min_ratio_user must be >= 1.0, got {self.compress_min_ratio_user}")
        if not 0.0 <= self.compress_keyword_overlap_memory <= 1.0:
            raise ValueError(f"compress_keyword_overlap_memory must be in [0, 1], got {self.compress_keyword_overlap_memory}")
        if not 0.0 <= self.compress_keyword_overlap_user <= 1.0:
            raise ValueError(f"compress_keyword_overlap_user must be in [0, 1], got {self.compress_keyword_overlap_user}")
        if not 0.0 <= self.compress_entity_retention_memory <= 1.0:
            raise ValueError(f"compress_entity_retention_memory must be in [0, 1], got {self.compress_entity_retention_memory}")
        if self.hindsight_keyword_count < 3 or self.hindsight_keyword_count > 8:
            raise ValueError(f"hindsight_keyword_count must be in [3, 8], got {self.hindsight_keyword_count}")
        if self.cold_memory_days < 1:
            raise ValueError(f"cold_memory_days must be >= 1, got {self.cold_memory_days}")
        if self.hot_memory_access_count < 1:
            raise ValueError(f"hot_memory_access_count must be >= 1, got {self.hot_memory_access_count}")
        if self.l2_max_entries < 10:
            raise ValueError(f"l2_max_entries must be >= 10, got {self.l2_max_entries}")

    @classmethod
    def from_env(cls, defaults: dict[str, Any] | None = None) -> "AppConfig":
        """从环境变量加载配置，覆盖默认值。

        优先级：ENV 变量 > 传入的 defaults > 代码默认值。
        """
        values: dict[str, Any] = dict(defaults) if defaults else {}

        # 过滤未知字段，防止 YAML 中多余字段导致 TypeError
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        values = {k: v for k, v in values.items() if k in valid_fields}

        if v := os.getenv("MEMORY_CLEANUP_MEMORY_PATH"):
            values["memory_path"] = v
        if v := os.getenv("MEMORY_CLEANUP_USER_PATH"):
            values["user_path"] = v
        if v := os.getenv("MEMORY_CLEANUP_SESSION_DB_PATH"):
            values["session_db_path"] = v
        if v := os.getenv("MEMORY_CLEANUP_HERMES_AGENT_PATH"):
            values["hermes_agent_path"] = v
        if v := os.getenv("MEMORY_CLEANUP_LLM_URL"):
            values["llm_url"] = v
        if v := os.getenv("LITELLM_MASTER_KEY"):
            values["llm_key"] = v
        if v := os.getenv("MEMORY_CLEANUP_LLM_MODEL"):
            values["llm_model"] = v
        if v := os.getenv("MEMORY_CLEANUP_HINDSIGHT_URL"):
            values["hindsight_url"] = v
        _safe_int_env("MEMORY_CLEANUP_BATCH_SIZE", "batch_size", values)
        _safe_int_env("MEMORY_CLEANUP_USER_BATCH_SIZE", "user_batch_size", values)
        _safe_int_env("MEMORY_CLEANUP_VOTE_COUNT", "vote_count", values)
        _safe_int_env("MEMORY_CLEANUP_MAX_WORKERS", "max_workers", values)
        _safe_int_env("MEMORY_CLEANUP_MEMORY_CHAR_LIMIT", "memory_char_limit", values)
        _safe_int_env("MEMORY_CLEANUP_USER_CHAR_LIMIT", "user_char_limit", values)
        if v := os.getenv("MEMORY_CLEANUP_DELIMITER"):
            values["entry_delimiter"] = v
        if v := os.getenv("MEMORY_CLEANUP_LOG_LEVEL"):
            values["log_level"] = v
        if v := os.getenv("MEMORY_CLEANUP_OUTPUT_MODE"):
            values["output_mode"] = v
        _safe_bool_env("MEMORY_CLEANUP_COMPRESS_STRICT_MODE", "compress_strict_mode", values)
        _safe_float_env("MEMORY_CLEANUP_COMPRESS_MIN_RATIO_MEMORY", "compress_min_ratio_memory", values)
        _safe_float_env("MEMORY_CLEANUP_COMPRESS_MIN_RATIO_USER", "compress_min_ratio_user", values)
        _safe_float_env("MEMORY_CLEANUP_COMPRESS_KEYWORD_OVERLAP_MEMORY", "compress_keyword_overlap_memory", values)
        _safe_float_env("MEMORY_CLEANUP_COMPRESS_KEYWORD_OVERLAP_USER", "compress_keyword_overlap_user", values)
        _safe_float_env("MEMORY_CLEANUP_COMPRESS_ENTITY_RETENTION_MEMORY", "compress_entity_retention_memory", values)
        _safe_bool_env("MEMORY_CLEANUP_KEYWORD_BACKFILL", "keyword_backfill", values)
        _safe_int_env("MEMORY_CLEANUP_HINDSIGHT_KEYWORD_COUNT", "hindsight_keyword_count", values)
        _safe_bool_env("MEMORY_CLEANUP_COLD_MEMORY_EVICTION", "cold_memory_eviction", values)
        _safe_int_env("MEMORY_CLEANUP_COLD_MEMORY_DAYS", "cold_memory_days", values)
        _safe_bool_env("MEMORY_CLEANUP_HOT_MEMORY_PROMOTION", "hot_memory_promotion", values)
        _safe_int_env("MEMORY_CLEANUP_HOT_MEMORY_ACCESS_COUNT", "hot_memory_access_count", values)
        _safe_int_env("MEMORY_CLEANUP_L2_MAX_ENTRIES", "l2_max_entries", values)

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

    # ENV 覆盖（在 from_env 中统一处理，此处仅传递 yaml 值作为 defaults）
    return config


# 默认全局配置实例（从环境变量加载）
CONFIG = AppConfig.from_env()
