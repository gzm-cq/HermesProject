"""向后兼容 shim：src.export → ai_report.export

将旧的 `from src.export.xxx` 导入路径映射到 `ai_report.export.xxx`。
新代码应直接使用 `from ai_report.export.xxx import ...`。

旧模块名到新模块名的映射：
  docx_exporter   → ai_report.export.docx_exporter
  chart_renderer  → ai_report.export.chart_renderer

此模块在未来版本中将被移除。
"""
import importlib
import warnings

_MODULE_MAP = {
    "docx_exporter": "ai_report.export.docx_exporter",
    "chart_renderer": "ai_report.export.chart_renderer",
}


def __getattr__(name):
    if name in _MODULE_MAP:
        target = _MODULE_MAP[name]
        warnings.warn(
            f"'src.export.{name}' is deprecated, use '{target}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return importlib.import_module(target)
    raise AttributeError(f"module 'src.export' has no attribute '{name}'")
