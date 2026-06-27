"""兼容层：将旧的 evaluation 导入路径映射到新的 core 模块。

这是一个向后兼容 shim，确保旧的 `from ai_report.evaluation.xxx`
导入路径继续工作。新代码应直接使用 `from ai_report.core.xxx`。

旧模块名到新模块名的映射：
  report_evaluator → ai_report.core.evaluator
  state_manager    → ai_report.core.state_manager

注意：此模块将在未来版本中移除。
"""
import importlib
import sys
import warnings

_MODULE_MAP = {
    "report_evaluator": "ai_report.core.evaluator",
    "state_manager": "ai_report.core.state_manager",
}


def __getattr__(name):
    if name in _MODULE_MAP:
        target = _MODULE_MAP[name]
        warnings.warn(
            f"'ai_report.evaluation.{name}' is deprecated, use '{target}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        mod = importlib.import_module(target)
        # 注册到 sys.modules 使得 from ai_report.evaluation.xxx import ... 工作
        sys.modules[f"ai_report.evaluation.{name}"] = mod
        return mod
    raise AttributeError(f"module 'ai_report.evaluation' has no attribute '{name}'")
