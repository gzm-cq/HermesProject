"""兼容层：将旧的 hermes_tools 导入路径映射到新的 adapters 模块。

这是一个向后兼容 shim，确保旧的 `from ai_report.hermes_tools.xxx`
导入路径继续工作。新代码应直接使用 `from ai_report.adapters.xxx`。

旧模块名到新模块名的映射：
  ai_client        → ai_report.adapters.ai_client
  web_searcher     → ai_report.adapters.web_search
  document_parser  → ai_report.adapters.document
  hermes_wrapper   → ai_report.adapters.hermes
  memory_system    → ai_report.adapters.hermes
  quality_assessor → ai_report.adapters.quality_assessor
  chart_generator  → ai_report.adapters.chart_generator
  chart_advisor    → ai_report.adapters.chart_advisor
  business_writer  → ai_report.adapters.business_writer
  workflow_state   → ai_report.core.workflow_state
  full_report_loop → ai_report.core.full_report_loop
  quality_loop     → ai_report.core.quality_loop

注意：此模块将在未来版本中移除。
"""
import importlib
import sys
import warnings

_MODULE_MAP = {
    "ai_client": "ai_report.adapters.ai_client",
    "web_searcher": "ai_report.adapters.web_search",
    "document_parser": "ai_report.adapters.document",
    "hermes_wrapper": "ai_report.adapters.hermes",
    "memory_system": "ai_report.adapters.hermes",
    "quality_assessor": "ai_report.adapters.quality_assessor",
    "chart_generator": "ai_report.adapters.chart_generator",
    "chart_advisor": "ai_report.adapters.chart_advisor",
    "business_writer": "ai_report.adapters.business_writer",
    "workflow_state": "ai_report.core.workflow_state",
    "full_report_loop": "ai_report.core.full_report_loop",
    "quality_loop": "ai_report.core.quality_loop",
}


def __getattr__(name):
    if name in _MODULE_MAP:
        target = _MODULE_MAP[name]
        warnings.warn(
            f"'ai_report.hermes_tools.{name}' is deprecated, "
            f"use '{target}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        mod = importlib.import_module(target)
        # 注册到 sys.modules 使得 from ai_report.hermes_tools.xxx import ... 工作
        sys.modules[f"ai_report.hermes_tools.{name}"] = mod
        return mod
    raise AttributeError(f"module 'ai_report.hermes_tools' has no attribute '{name}'")
