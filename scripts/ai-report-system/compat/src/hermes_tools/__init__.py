"""向后兼容 shim：src.hermes_tools → ai_report.adapters

将旧的 `from src.hermes_tools.xxx` 导入路径映射到 `ai_report.adapters.xxx`。
新代码应直接使用 `from ai_report.adapters.xxx import ...`。

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

此模块在未来版本中将被移除。
"""
import importlib
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
            f"'src.hermes_tools.{name}' is deprecated, use '{target}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return importlib.import_module(target)
    raise AttributeError(f"module 'src.hermes_tools' has no attribute '{name}'")
