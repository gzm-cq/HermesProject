"""报告导出模块 — chart 渲染 + docx 输出"""
from .docx_exporter import export_to_docx

__all__ = [
    "export_to_docx",
    "render_chart",
    "render_all_charts",
]


def __getattr__(name):
    if name == "render_chart":
        from .chart_renderer import render_chart
        return render_chart
    if name == "render_all_charts":
        from .chart_renderer import render_all_charts
        return render_all_charts
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
