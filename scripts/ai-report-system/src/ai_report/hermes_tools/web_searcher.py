"""向后兼容 shim：ai_report.hermes_tools.web_searcher → ai_report.adapters.web_search

此模块在未来版本中将被移除。请使用 ai_report.adapters.web_search 代替。
"""
import warnings
warnings.warn(
    "ai_report.hermes_tools.web_searcher is deprecated, use ai_report.adapters.web_search instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.adapters.web_search import *  # noqa: F401,F403