"""向后兼容 shim：src.evaluation → ai_report.core

将旧的 `from src.evaluation.xxx` 导入路径映射到 `ai_report.core.xxx`。
新代码应直接使用 `from ai_report.core.xxx import ...`。

旧模块名到新模块名的映射：
  report_evaluator → ai_report.core.evaluator
  state_manager    → ai_report.core.state_manager

此模块在未来版本中将被移除。
"""
import importlib
import warnings

_MODULE_MAP = {
    "report_evaluator": "ai_report.core.evaluator",
    "state_manager": "ai_report.core.state_manager",
}


def __getattr__(name):
    if name in _MODULE_MAP:
        target = _MODULE_MAP[name]
        warnings.warn(
            f"'src.evaluation.{name}' is deprecated, use '{target}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return importlib.import_module(target)
    raise AttributeError(f"module 'src.evaluation' has no attribute '{name}'")
