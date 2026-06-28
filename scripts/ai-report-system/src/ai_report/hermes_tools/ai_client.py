"""向后兼容 shim：ai_report.hermes_tools.ai_client → ai_report.adapters.ai_client

此模块在未来版本中将被移除。请使用 ai_report.adapters.ai_client 代替。
"""
import warnings
warnings.warn(
    "ai_report.hermes_tools.ai_client is deprecated, use ai_report.adapters.ai_client instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.adapters.ai_client import *  # noqa: F401,F403