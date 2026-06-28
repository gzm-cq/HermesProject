"""向后兼容 shim：src.hermes_tools.chart_generator → ai_report.adapters.chart_generator

此模块在未来版本中将被移除。请使用 ai_report.adapters.chart_generator 代替。
"""
import warnings
warnings.warn(
    "src.hermes_tools.chart_generator is deprecated, use ai_report.adapters.chart_generator instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.adapters.chart_generator import *  # noqa: F401,F403