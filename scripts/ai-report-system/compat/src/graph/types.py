"""向后兼容 shim：src.graph.types → ai_report.graph.types

此模块在未来版本中将被移除。请使用 ai_report.graph.types 代替。
"""
import warnings
warnings.warn(
    "src.graph.types is deprecated, use ai_report.graph.types instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.graph.types import *  # noqa: F401,F403