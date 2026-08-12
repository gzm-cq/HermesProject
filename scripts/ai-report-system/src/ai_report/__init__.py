"""AI 报告导出工具 — Markdown 转精美 DOCX。

提供高质量的 Markdown 到 DOCX 转换，支持图表渲染、自定义样式、封面目录等。
"""

from __future__ import annotations

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
    if name in ("render_chart", "render_all_charts"):
        from ai_report.export.chart_renderer import render_all_charts, render_chart
        if name == "render_chart":
            return render_chart
        return render_all_charts
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def register(ctx) -> None:
    ctx.logger.info("ai-report plugin registered v%s", __version__)
