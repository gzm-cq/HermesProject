"""向后兼容 shim：src.integration → ai_report.core

将旧的 `from src.integration.xxx` 导入路径映射到 `ai_report.core.xxx`。
新代码应直接使用 `from ai_report.core.xxx import ...`。

旧模块名到新模块名的映射：
  workflow_orchestrator → ai_report.core.orchestrator
  report_goal_helpers   → ai_report.core.report_goal_helpers

此模块在未来版本中将被移除。
"""
import importlib
import warnings

_MODULE_MAP = {
    "workflow_orchestrator": "ai_report.core.orchestrator",
    "report_goal_helpers": "ai_report.core.report_goal_helpers",
}


def __getattr__(name):
    if name in _MODULE_MAP:
        target = _MODULE_MAP[name]
        warnings.warn(
            f"'src.integration.{name}' is deprecated, use '{target}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return importlib.import_module(target)
    raise AttributeError(f"module 'src.integration' has no attribute '{name}'")
