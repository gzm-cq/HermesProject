"""AI报告生成系统配置模块

遵循Hermes工程标准规范，统一配置管理。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, TypeAlias, Literal

import yaml

logger = logging.getLogger(__name__)

# ── 类型别名 ──────────────────────────────────────────────

ConfigDict: TypeAlias = dict[str, Any]


# ── 报告配置 ──────────────────────────────────────────────

@dataclass
class ReportConfig:
    """报告生成配置"""

    report_type: Literal["tech", "market", "product", "research"] = "tech"
    language: Literal["zh", "en"] = "zh"
    max_length: int = 5000  # 最大报告长度（字符）
    include_diagrams: bool = True
    diagram_style: Literal["excalidraw", "architecture", "ascii", "infographic"] = "excalidraw"
    quality_level: Literal["basic", "standard", "strict"] = "standard"

    def to_dict(self) -> ConfigDict:
        """转换为字典格式"""
        return {
            "report_type": self.report_type,
            "language": self.language,
            "max_length": self.max_length,
            "include_diagrams": self.include_diagrams,
            "diagram_style": self.diagram_style,
            "quality_level": self.quality_level,
        }


@dataclass
class SearchConfig:
    """搜索配置"""

    search_timeout: int = 30  # 搜索超时时间（秒）
    max_results: int = 5      # 最大搜索结果数
    cache_enabled: bool = True
    cache_ttl: int = 3600     # 缓存有效期（秒）
    search_methods: list[str] = field(default_factory=lambda: ["browser", "web"])

    def __post_init__(self) -> None:
        """初始化后验证"""
        if self.search_timeout <= 0:
            raise ValueError(f"无效的search_timeout: {self.search_timeout}")
        if self.max_results <= 0:
            raise ValueError(f"无效的max_results: {self.max_results}")
        if self.cache_ttl <= 0:
            raise ValueError(f"无效的cache_ttl: {self.cache_ttl}")

    def to_dict(self) -> ConfigDict:
        """转换为字典格式"""
        return {
            "search_timeout": self.search_timeout,
            "max_results": self.max_results,
            "cache_enabled": self.cache_enabled,
            "cache_ttl": self.cache_ttl,
            "search_methods": self.search_methods,
        }


@dataclass
class ParallelConfig:
    """并行化配置"""

    enabled: bool = True                # 是否启用并行
    max_workers: int = 3                # 最大并行工作线程数
    chapter_max_workers: int = 2        # 章节生成最大并行数（避免API限流）
    source_max_workers: int = 4         # 源文件加载最大并行数
    evaluate_max_workers: int = 4       # 维度评估最大并行数

    def __post_init__(self) -> None:
        """初始化后验证"""
        if self.max_workers <= 0:
            raise ValueError(f"无效的max_workers: {self.max_workers}")
        if self.chapter_max_workers <= 0:
            raise ValueError(f"无效的chapter_max_workers: {self.chapter_max_workers}")
        if self.source_max_workers <= 0:
            raise ValueError(f"无效的source_max_workers: {self.source_max_workers}")
        if self.evaluate_max_workers <= 0:
            raise ValueError(f"无效的evaluate_max_workers: {self.evaluate_max_workers}")

    def to_dict(self) -> ConfigDict:
        """转换为字典格式"""
        return {
            "enabled": self.enabled,
            "max_workers": self.max_workers,
            "chapter_max_workers": self.chapter_max_workers,
            "source_max_workers": self.source_max_workers,
            "evaluate_max_workers": self.evaluate_max_workers,
        }


@dataclass
class SystemConfig:
    """系统配置"""

    working_dir: Path = field(default_factory=lambda: Path.cwd())
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    enable_memory: bool = True   # 是否启用记忆系统
    memory_provider: Literal["honcho", "builtin"] = "honcho"
    auto_save: bool = True
    auto_save_interval: int = 300  # 自动保存间隔（秒）

    def __post_init__(self) -> None:
        """初始化后验证"""
        if not self.working_dir.exists():
            raise ValueError(f"工作目录不存在: {self.working_dir}")
        if self.auto_save_interval <= 0:
            raise ValueError(f"无效的auto_save_interval: {self.auto_save_interval}")

    def to_dict(self) -> ConfigDict:
        """转换为字典格式"""
        return {
            "working_dir": str(self.working_dir),
            "log_level": self.log_level,
            "enable_memory": self.enable_memory,
            "memory_provider": self.memory_provider,
            "auto_save": self.auto_save,
            "auto_save_interval": self.auto_save_interval,
        }


class ConfigManager:
    """配置管理器"""

    _instance: ClassVar[ConfigManager | None] = None

    def __new__(cls) -> ConfigManager:
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """初始化配置管理器"""
        if hasattr(self, "_initialized"):
            return

        self._config_file: Path | None = None
        self.report_config = ReportConfig()
        self.search_config = SearchConfig()
        self.system_config = SystemConfig()
        self.parallel_config = ParallelConfig()

        # 寻找配置文件
        self._find_config_file()
        self._load_config()

        self._initialized = True

    def _find_config_file(self) -> None:
        """查找配置文件"""
        possible_paths = [
            Path.cwd() / "config.json",
            Path(__file__).parent.parent.parent / "config.json",
            Path(__file__).parent.parent.parent / "config" / "default.json",
            Path.home() / ".ai_report_system" / "config.json",
        ]

        for config_path in possible_paths:
            if config_path.exists():
                self._config_file = config_path
                return

        # 默认使用项目根目录
        self._config_file = Path.cwd() / "config.json"

    def _load_config(self) -> None:
        """从配置文件加载配置"""
        if self._config_file is None or not self._config_file.exists():
            return

        try:
            with self._config_file.open("r", encoding="utf-8") as f:
                config_data = json.load(f)

            if "report" in config_data:
                self._update_from_dict(self.report_config, config_data["report"])

            if "search" in config_data:
                self._update_from_dict(self.search_config, config_data["search"])

            if "system" in config_data:
                self._update_from_dict(self.system_config, config_data["system"])
                if "working_dir" in config_data["system"]:
                    self.system_config.working_dir = Path(config_data["system"]["working_dir"])

            if "parallel" in config_data:
                self._update_from_dict(self.parallel_config, config_data["parallel"])

        except (json.JSONDecodeError, OSError) as e:
            print(f"配置文件加载失败，使用默认配置: {e}")

    def save(self, file_path: Path | None = None) -> None:
        """保存配置到文件"""
        if file_path is None:
            if self._config_file is None:
                self._config_file = Path.cwd() / "config.json"
            file_path = self._config_file

        try:
            config_data = {
                "version": "1.0.0",
                "report": self.report_config.to_dict(),
                "search": self.search_config.to_dict(),
                "system": {
                    **self.system_config.to_dict(),
                    "working_dir": str(self.system_config.working_dir),
                },
                "parallel": self.parallel_config.to_dict(),
            }

            file_path.parent.mkdir(parents=True, exist_ok=True)

            with file_path.open("w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)

            self._config_file = file_path
        except (OSError, TypeError) as e:
            raise RuntimeError(f"保存配置失败: {e}") from e

    def to_dict(self) -> dict[str, Any]:
        """转换为完整配置字典"""
        return {
            "report": self.report_config.to_dict(),
            "search": self.search_config.to_dict(),
            "system": {
                **self.system_config.to_dict(),
                "working_dir": str(self.system_config.working_dir),
            },
            "parallel": self.parallel_config.to_dict(),
        }

    @staticmethod
    def _update_from_dict(obj: Any, data: dict[str, Any]) -> None:
        """从字典更新对象属性"""
        for key, value in data.items():
            if hasattr(obj, key):
                setattr(obj, key, value)


def get_config() -> ConfigManager:
    """获取全局配置实例"""
    return ConfigManager()


# ── 环境变量配置 ──────────────────────────────────────────
# 统一 AI_REPORT_* 前缀环境变量管理，替换散落在各模块中的 os.getenv 调用。
# 优先级：AI_REPORT_* 环境变量 > 旧环境变量名（向后兼容） > 默认值


@dataclass
class EnvConfig:
    """AI报告系统环境变量配置

    所有环境变量统一使用 AI_REPORT_ 前缀。
    优先级：环境变量 > 配置文件 > 默认值
    """

    # ── 系统级 ──
    work_dir: str = field(
        default_factory=lambda: os.getenv("AI_REPORT_WORK_DIR", "./reports")
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("AI_REPORT_LOG_LEVEL", "INFO")
    )
    cache_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_CACHE_ENABLED", "true"
        ).lower() == "true"
    )

    # ── LLM 相关 ──
    llm_api_key: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")
        )
    )
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("AI_REPORT_LLM_BASE_URL", "")
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv("AI_REPORT_LLM_MODEL", "gpt-4")
    )

    # ── LLM Provider 密钥（降级路径用） ──
    litellm_api_key: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_LITELLM_API_KEY", os.getenv("LITELLM_MASTER_KEY", "")
        )
    )
    deepseek_api_key: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")
        )
    )
    siliconflow_api_key: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_SILICONFLOW_API_KEY", os.getenv("SILICONFLOW_API_KEY", "")
        )
    )
    glm_api_key: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_GLM_API_KEY", os.getenv("GLM_API_KEY", "")
        )
    )
    shangtang_api_key: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_SHANGTANG_API_KEY", os.getenv("SHANGTANG_API_KEY", "")
        )
    )

    # ── Hermes 集成 ──
    hermes_url: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_HERMES_URL", "http://localhost:8080"
        )
    )
    hermes_api_key: str = field(
        default_factory=lambda: os.getenv("AI_REPORT_HERMES_API_KEY", "")
    )

    # ── 搜索相关 ──
    tavily_api_key: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", "")
        )
    )
    search_timeout: int = field(
        default_factory=lambda: int(
            os.getenv("AI_REPORT_SEARCH_TIMEOUT", "30")
        )
    )
    search_max_results: int = field(
        default_factory=lambda: int(
            os.getenv("AI_REPORT_SEARCH_MAX_RESULTS", "10")
        )
    )

    # ── 源文档 ──
    source_doc_path: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_SOURCE_DOC_PATH", os.getenv("SOURCE_DOC_PATH", "")
        )
    )

    # ── 并行化 ──
    parallel_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_PARALLEL_ENABLED", "true"
        ).lower() == "true"
    )
    parallel_max_workers: int = field(
        default_factory=lambda: int(
            os.getenv("AI_REPORT_PARALLEL_MAX_WORKERS", "3")
        )
    )
    parallel_chapter_max_workers: int = field(
        default_factory=lambda: int(
            os.getenv("AI_REPORT_PARALLEL_CHAPTER_MAX_WORKERS", "2")
        )
    )
    parallel_source_max_workers: int = field(
        default_factory=lambda: int(
            os.getenv("AI_REPORT_PARALLEL_SOURCE_MAX_WORKERS", "4")
        )
    )
    parallel_evaluate_max_workers: int = field(
        default_factory=lambda: int(
            os.getenv("AI_REPORT_PARALLEL_EVALUATE_MAX_WORKERS", "4")
        )
    )

    # ── Dify KB ──
    dify_compose: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_DIFY_COMPOSE", os.getenv(
                "DIFY_COMPOSE", "/app/dify/docker/docker-compose.yml"
            )
        )
    )
    dify_api: str = field(
        default_factory=lambda: os.getenv(
            "AI_REPORT_DIFY_API", os.getenv("DIFY_API", "http://api:5001")
        )
    )

    def to_dict(self) -> ConfigDict:
        """转换为字典格式（隐藏敏感信息）"""
        return {
            "work_dir": self.work_dir,
            "log_level": self.log_level,
            "cache_enabled": self.cache_enabled,
            "llm_api_key": "***" if self.llm_api_key else "",
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "litellm_api_key": "***" if self.litellm_api_key else "",
            "deepseek_api_key": "***" if self.deepseek_api_key else "",
            "siliconflow_api_key": "***" if self.siliconflow_api_key else "",
            "glm_api_key": "***" if self.glm_api_key else "",
            "shangtang_api_key": "***" if self.shangtang_api_key else "",
            "hermes_url": self.hermes_url,
            "hermes_api_key": "***" if self.hermes_api_key else "",
            "tavily_api_key": "***" if self.tavily_api_key else "",
            "search_timeout": self.search_timeout,
            "search_max_results": self.search_max_results,
            "source_doc_path": self.source_doc_path,
            "parallel_enabled": self.parallel_enabled,
            "parallel_max_workers": self.parallel_max_workers,
            "parallel_chapter_max_workers": self.parallel_chapter_max_workers,
            "parallel_source_max_workers": self.parallel_source_max_workers,
            "parallel_evaluate_max_workers": self.parallel_evaluate_max_workers,
            "dify_compose": self.dify_compose,
            "dify_api": self.dify_api,
        }


_env_config: EnvConfig | None = None


def get_env_config() -> EnvConfig:
    """获取环境变量配置单例"""
    global _env_config
    if _env_config is None:
        _env_config = EnvConfig()
    return _env_config


def reset_env_config() -> None:
    """重置环境变量配置单例（测试用）"""
    global _env_config
    _env_config = None


# ── 报告主题级配置加载器 ──────────────────────────────────
# 项目级 reports/report_config.yaml 按报告类型分节。

DEFAULT_SOURCE_EXTENSIONS: list[str] = [".md", ".txt"]
DEFAULT_DESKTOP_FALLBACK: str = "/mnt/c/Users/1/Desktop"
DEFAULT_LANGUAGE: str = "zh"
DEFAULT_REPORT_TYPE: str = "tech"

DEFAULT_CREDIBILITY_HIGH: list[str] = [
    "gov.cn", "sasac.gov.cn", "cnki.net",
    "mof.gov.cn", "miit.gov.cn", "ndrc.gov.cn",
]

DEFAULT_CREDIBILITY_MEDIUM: list[str] = [
    "csdn.net", "infoq.cn", "oschina.net",
    "36kr.com", "tech.qq.com", "solidot.org",
    "arxiv.org", "ieee.org", "acm.org",
]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归深度合并两个字典。"""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_report_config(
    topic: str | None = None,
    report_type: str | None = None,
) -> dict[str, Any]:
    """加载项目级报告配置，按报告类型自动选择配置段。"""
    raw = _load_project_yaml()
    if not raw:
        return {}

    merged = dict(raw.get("defaults", {}))

    if report_type and report_type in raw:
        type_section = raw[report_type]
        if isinstance(type_section, dict):
            merged = _deep_merge(merged, type_section)
            logger.info(
                "  config: 类型 '%s' 配置已合并 (%d 个覆盖字段)",
                report_type, len(type_section),
            )
    else:
        logger.debug("  config: 使用 defaults（无类型覆盖）")

    return merged


def _load_project_yaml() -> dict[str, Any]:
    """搜索并加载项目级 report_config.yaml"""
    config_path = _find_config_file()
    if config_path and config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                logger.warning("  config: %s 不是 dict，忽略", config_path)
                return {}
            logger.info("  config: 已加载 %s", config_path)
            return data
        except Exception as e:
            logger.warning("  config: 加载 %s 失败: %s", config_path, e)
            return {}

    logger.debug("  config: 无配置文件，使用代码默认值")
    return {}


def _find_config_file() -> Path | None:
    """搜索 report_config.yaml 位置。"""
    suggested = Path("reports") / "report_config.yaml"
    if suggested.exists():
        return suggested
    here = Path(__file__).resolve().parent.parent.parent
    fallback = here / "reports" / "report_config.yaml"
    if fallback.exists():
        return fallback
    return None


def get_source_extensions(config: dict[str, Any]) -> list[str]:
    """从配置中取源文档扩展名列表。"""
    exts = config.get("source_extensions")
    if isinstance(exts, list) and exts:
        return [str(e).lstrip("*") for e in exts]
    return DEFAULT_SOURCE_EXTENSIONS


def get_credibility_high(config: dict[str, Any]) -> list[str]:
    """从配置中取高可信域名列表。"""
    cred = config.get("credibility", {})
    high = cred.get("high") if isinstance(cred, dict) else None
    if isinstance(high, list) and high:
        return high
    return DEFAULT_CREDIBILITY_HIGH


def get_credibility_medium(config: dict[str, Any]) -> list[str]:
    """从配置中取中可信域名列表。"""
    cred = config.get("credibility", {})
    medium = cred.get("medium") if isinstance(cred, dict) else None
    if isinstance(medium, list) and medium:
        return medium
    return DEFAULT_CREDIBILITY_MEDIUM


def get_desktop_fallback(config: dict[str, Any]) -> str:
    """从配置中取桌面回退路径。"""
    path = config.get("desktop_fallback")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return DEFAULT_DESKTOP_FALLBACK


def get_default_language(config: dict[str, Any]) -> str:
    """从配置中取默认语言。"""
    lang = config.get("language")
    if isinstance(lang, str) and lang.strip():
        return lang.strip()
    return DEFAULT_LANGUAGE


def get_default_report_type(config: dict[str, Any]) -> str:
    """从配置中取默认报告类型。"""
    rtype = config.get("report_type")
    if isinstance(rtype, str) and rtype.strip():
        return rtype.strip()
    return DEFAULT_REPORT_TYPE


def get_source_doc_kb_name(config: dict[str, Any]) -> str | None:
    """从配置中取 KB 源文档名。"""
    return config.get("source_doc_kb_name")


def get_parallel_config() -> ParallelConfig:
    """从环境变量构建并行化配置。

    优先级：环境变量 > 配置文件 > 默认值
    """
    env = get_env_config()
    return ParallelConfig(
        enabled=env.parallel_enabled,
        max_workers=env.parallel_max_workers,
        chapter_max_workers=env.parallel_chapter_max_workers,
        source_max_workers=env.parallel_source_max_workers,
        evaluate_max_workers=env.parallel_evaluate_max_workers,
    )
