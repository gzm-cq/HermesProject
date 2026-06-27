"""向后兼容 shim：src.evaluation.state_manager → ai_report.core.state_manager

此模块在未来版本中将被移除。请使用 ai_report.core.state_manager 代替。
"""
import warnings
warnings.warn(
    "src.evaluation.state_manager is deprecated, use ai_report.core.state_manager instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.core.state_manager import *  # noqa: F401,F403