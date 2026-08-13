"""AI 报告导出工具 — Markdown 转精美 DOCX。

提供高质量的 Markdown 到 DOCX 转换，支持图表渲染、自定义样式、封面目录等。
"""

from __future__ import annotations

from typing import Any

__version__ = "2.0.0"
__author__ = "AI报告团队"
__license__ = "MIT"

from ai_report.export.docx_exporter import export_to_docx

__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "export_to_docx",
    "render_chart",
    "render_all_charts",
    "register",
]


def __getattr__(name):
    # 收敛到 ai_report.export 的惰性导入，避免两处重复实现 chart 函数导入逻辑
    if name in ("render_chart", "render_all_charts"):
        from ai_report import export as _export
        return getattr(_export, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def register(_ctx: Any | None = None) -> None:
    """插件注册入口（hermes.plugins 入口点）。

    _ctx 为插件加载器传入的上下文（可选）；当前仅记录注册日志。
    """
    logger = __import__("logging").getLogger(__name__)
    logger.info("ai-report plugin registered v%s", __version__)
