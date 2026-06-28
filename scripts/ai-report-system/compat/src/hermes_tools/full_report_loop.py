"""向后兼容 shim：src.hermes_tools.full_report_loop → ai_report.core.full_report_loop

此模块在未来版本中将被移除。请使用 ai_report.core.full_report_loop 代替。
"""
import warnings
warnings.warn(
    "src.hermes_tools.full_report_loop is deprecated, use ai_report.core.full_report_loop instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.core.full_report_loop import *  # noqa: F401,F403