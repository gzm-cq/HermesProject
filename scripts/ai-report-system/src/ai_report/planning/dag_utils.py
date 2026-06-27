"""向后兼容 shim：ai_report.planning.dag_utils → ai_report.core.dag_utils

此模块在未来版本中将被移除。请使用 ai_report.core.dag_utils 代替。
"""
import warnings
warnings.warn(
    "ai_report.planning.dag_utils is deprecated, use ai_report.core.dag_utils instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.core.dag_utils import *  # noqa: F401,F403