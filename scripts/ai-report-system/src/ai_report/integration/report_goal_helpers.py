"""向后兼容 shim：ai_report.integration.report_goal_helpers → ai_report.core.report_goal_helpers

此模块在未来版本中将被移除。请使用 ai_report.core.report_goal_helpers 代替。
"""
import warnings
warnings.warn(
    "ai_report.integration.report_goal_helpers is deprecated, use ai_report.core.report_goal_helpers instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.core.report_goal_helpers import *  # noqa: F401,F403