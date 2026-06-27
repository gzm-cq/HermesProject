"""向后兼容 shim：src.hermes_tools.chart_advisor → ai_report.adapters.chart_advisor

此模块在未来版本中将被移除。请使用 ai_report.adapters.chart_advisor 代替。
"""
import warnings
warnings.warn(
    "src.hermes_tools.chart_advisor is deprecated, use ai_report.adapters.chart_advisor instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.adapters.chart_advisor import *  # noqa: F401,F403