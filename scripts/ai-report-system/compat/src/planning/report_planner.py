"""向后兼容 shim：src.planning.report_planner → ai_report.core.planner

此模块在未来版本中将被移除。请使用 ai_report.core.planner 代替。
"""
import warnings
warnings.warn(
    "src.planning.report_planner is deprecated, use ai_report.core.planner instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.core.planner import *  # noqa: F401,F403
# 旧名别名映射
from ai_report.core.planner import HermesReportPlanner as ReportPlanner  # noqa: F401

__all__ = ["ReportPlanner"]