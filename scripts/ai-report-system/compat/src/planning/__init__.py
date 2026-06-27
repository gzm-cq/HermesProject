"""向后兼容 shim：src.planning → ai_report.core

将旧的 `from src.planning.xxx` 导入路径映射到 `ai_report.core.xxx`。
新代码应直接使用 `from ai_report.core.xxx import ...`。

旧模块名到新模块名的映射：
  report_planner    → ai_report.core.planner
  content_generator → ai_report.core.generator
  dag_utils         → ai_report.core.dag_utils

此模块在未来版本中将被移除。
"""
import importlib
import warnings

_MODULE_MAP = {
    "report_planner": "ai_report.core.planner",
    "content_generator": "ai_report.core.generator",
    "dag_utils": "ai_report.core.dag_utils",
}


def __getattr__(name):
    if name in _MODULE_MAP:
        target = _MODULE_MAP[name]
        warnings.warn(
            f"'src.planning.{name}' is deprecated, use '{target}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return importlib.import_module(target)
    raise AttributeError(f"module 'src.planning' has no attribute '{name}'")
