"""向后兼容 shim：src.evaluation.report_evaluator → ai_report.core.evaluator

此模块在未来版本中将被移除。请使用 ai_report.core.evaluator 代替。
"""
import warnings
warnings.warn(
    "src.evaluation.report_evaluator is deprecated, use ai_report.core.evaluator instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.core.evaluator import *  # noqa: F401,F403
# 旧名别名映射
from ai_report.core.evaluator import HermesReportEvaluator as ReportEvaluator  # noqa: F401

__all__ = ["ReportEvaluator"]