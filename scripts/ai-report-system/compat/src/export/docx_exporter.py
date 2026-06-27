"""向后兼容 shim：src.export.docx_exporter → ai_report.export.docx_exporter

此模块在未来版本中将被移除。请使用 ai_report.export.docx_exporter 代替。
"""
import warnings
warnings.warn(
    "src.export.docx_exporter is deprecated, use ai_report.export.docx_exporter instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.export.docx_exporter import *  # noqa: F401,F403