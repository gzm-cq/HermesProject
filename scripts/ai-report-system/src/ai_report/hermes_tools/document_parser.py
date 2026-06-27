"""向后兼容 shim：ai_report.hermes_tools.document_parser → ai_report.adapters.document

此模块在未来版本中将被移除。请使用 ai_report.adapters.document 代替。
"""
import warnings
warnings.warn(
    "ai_report.hermes_tools.document_parser is deprecated, use ai_report.adapters.document instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.adapters.document import *  # noqa: F401,F403