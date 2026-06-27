"""AI报告生成系统 - 基于Hermes工具集

提供AI驱动的自动报告生成功能，集成Hermes工具进行搜索、文档解析、图表生成和内容生成。
支持多种报告类型和技术内容分析。

主要模块:
- core: 核心数据结构和配置（base, exceptions, planner, generator, evaluator, orchestrator）
- adapters: 外部集成适配层（hermes, ai_client, web_search, document）
- export: 报告导出模块（docx, chart_renderer）
- graph: StateGraph报告生成管线
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "AI报告团队"
__license__ = "MIT"

from ai_report.config import (
    ConfigManager,
    ReportConfig,
    SearchConfig,
    SystemConfig,
    get_config,
)
from ai_report.core.base import (
    BaseComponent,
    HermesToolComponent,
    StatefulComponent,
)
from ai_report.core.exceptions import (
    ConfigError,
    DiagramError,
    DocumentError,
    MemoryError,
    QualityError,
    ReportAgentError,
    SearchError,
    ValidationError,
    handle_error,
)

__all__ = [
    "__version__",
    "__author__",
    "__license__",
    # config
    "ConfigManager",
    "ReportConfig",
    "SearchConfig",
    "SystemConfig",
    "get_config",
    # core.base
    "BaseComponent",
    "HermesToolComponent",
    "StatefulComponent",
    # core.exceptions
    "ReportAgentError",
    "ConfigError",
    "SearchError",
    "DocumentError",
    "DiagramError",
    "QualityError",
    "MemoryError",
    "ValidationError",
    "handle_error",
]


def register(ctx) -> None:
    """Hermes 插件注册入口。

    Args:
        ctx: Hermes 插件上下文对象，提供以下方法：
            - register_hook(hook_name: str, callback: Callable)
            - logger: 插件日志器
    """
    ctx.logger.info("ai-report plugin registered v%s", __version__)
