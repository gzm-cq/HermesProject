"""向后兼容 shim：ai_report.planning.report_planner → ai_report.core.planner

此模块在未来版本中将被移除。请使用 ai_report.core.planner 代替。
"""
import warnings
warnings.warn(
    "ai_report.planning.report_planner is deprecated, use ai_report.core.planner instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.core.planner import *  # noqa: F401,F403
from ai_report.core.planner import HermesReportPlanner as ReportPlanner  # noqa: F401