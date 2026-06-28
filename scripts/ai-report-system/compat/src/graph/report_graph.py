"""向后兼容 shim：src.graph.report_graph → ai_report.graph.report_graph

此模块在未来版本中将被移除。请使用 ai_report.graph.report_graph 代替。
"""
import warnings
warnings.warn(
    "src.graph.report_graph is deprecated, use ai_report.graph.report_graph instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.graph.report_graph import *  # noqa: F401,F403