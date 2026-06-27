"""向后兼容 shim：ai_report.hermes_tools.memory_system → ai_report.adapters.hermes

此模块在未来版本中将被移除。请使用 ai_report.adapters.hermes 代替。
"""
import warnings
warnings.warn(
    "ai_report.hermes_tools.memory_system is deprecated, use ai_report.adapters.hermes instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.adapters.hermes import *  # noqa: F401,F403