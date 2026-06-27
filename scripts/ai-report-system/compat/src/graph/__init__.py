"""向后兼容 shim：src.graph → ai_report.graph

将旧的 `from src.graph.xxx` 导入路径映射到 `ai_report.graph.xxx`。
新代码应直接使用 `from ai_report.graph.xxx import ...`。

旧模块名到新模块名的映射：
  report_graph     → ai_report.graph.report_graph
  material_service → ai_report.graph.material_service
  types            → ai_report.graph.types
  nodes            → ai_report.graph.nodes

此模块在未来版本中将被移除。
"""
import importlib
import warnings

_MODULE_MAP = {
    "report_graph": "ai_report.graph.report_graph",
    "material_service": "ai_report.graph.material_service",
    "types": "ai_report.graph.types",
    "nodes": "ai_report.graph.nodes",
}


def __getattr__(name):
    if name in _MODULE_MAP:
        target = _MODULE_MAP[name]
        warnings.warn(
            f"'src.graph.{name}' is deprecated, use '{target}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return importlib.import_module(target)
    raise AttributeError(f"module 'src.graph' has no attribute '{name}'")
