"""向后兼容 shim：src.planning.content_generator → ai_report.core.generator

此模块在未来版本中将被移除。请使用 ai_report.core.generator 代替。
"""
import warnings
warnings.warn(
    "src.planning.content_generator is deprecated, use ai_report.core.generator instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.core.generator import *  # noqa: F401,F403
# 旧名别名映射
from ai_report.core.generator import HermesContentGenerator as ContentGenerator  # noqa: F401

__all__ = ["ContentGenerator"]