"""向后兼容 shim：src.graph.material_service → ai_report.graph.material_service

此模块在未来版本中将被移除。请使用 ai_report.graph.material_service 代替。
"""
import warnings
warnings.warn(
    "src.graph.material_service is deprecated, use ai_report.graph.material_service instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.graph.material_service import *  # noqa: F401,F403