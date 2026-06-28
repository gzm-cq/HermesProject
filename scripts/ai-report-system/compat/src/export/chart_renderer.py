"""向后兼容 shim：src.export.chart_renderer → ai_report.export.chart_renderer

此模块在未来版本中将被移除。请使用 ai_report.export.chart_renderer 代替。
"""
import warnings
warnings.warn(
    "src.export.chart_renderer is deprecated, use ai_report.export.chart_renderer instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.export.chart_renderer import *  # noqa: F401,F403