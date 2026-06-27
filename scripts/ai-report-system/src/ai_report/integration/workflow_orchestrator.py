"""向后兼容 shim：ai_report.integration.workflow_orchestrator → ai_report.core.orchestrator

此模块在未来版本中将被移除。请使用 ai_report.core.orchestrator 代替。
"""
import warnings
warnings.warn(
    "ai_report.integration.workflow_orchestrator is deprecated, use ai_report.core.orchestrator instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.core.orchestrator import *  # noqa: F401,F403