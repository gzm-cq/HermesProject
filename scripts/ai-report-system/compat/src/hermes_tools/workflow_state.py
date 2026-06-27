"""向后兼容 shim：src.hermes_tools.workflow_state → ai_report.core.workflow_state

此模块在未来版本中将被移除。请使用 ai_report.core.workflow_state 代替。
"""
import warnings
warnings.warn(
    "src.hermes_tools.workflow_state is deprecated, use ai_report.core.workflow_state instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.core.workflow_state import *  # noqa: F401,F403