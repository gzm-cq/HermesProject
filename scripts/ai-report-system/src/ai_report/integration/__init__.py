"""兼容层：将旧的 integration 导入路径映射到新的 core 模块。

这是一个向后兼容 shim，确保旧的 `from ai_report.integration.xxx`
导入路径继续工作。新代码应直接使用 `from ai_report.core.xxx`。

旧模块名到新模块名的映射：
  workflow_orchestrator → ai_report.core.orchestrator
  report_goal_helpers   → ai_report.core.report_goal_helpers

注意：此模块将在未来版本中移除。
"""
import importlib
import sys
import warnings

_MODULE_MAP = {
    "workflow_orchestrator": "ai_report.core.orchestrator",
    "report_goal_helpers": "ai_report.core.report_goal_helpers",
}


def __getattr__(name):
    if name in _MODULE_MAP:
        target = _MODULE_MAP[name]
        warnings.warn(
            f"'ai_report.integration.{name}' is deprecated, use '{target}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        mod = importlib.import_module(target)
        # 注册到 sys.modules 使得 from ai_report.integration.xxx import ... 工作
        sys.modules[f"ai_report.integration.{name}"] = mod
        return mod
    raise AttributeError(f"module 'ai_report.integration' has no attribute '{name}'")
