"""向后兼容 shim：src.hermes_tools.business_writer → ai_report.adapters.business_writer

此模块在未来版本中将被移除。请使用 ai_report.adapters.business_writer 代替。
"""
import warnings
warnings.warn(
    "src.hermes_tools.business_writer is deprecated, use ai_report.adapters.business_writer instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.adapters.business_writer import *  # noqa: F401,F403