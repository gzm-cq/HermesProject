"""向后兼容 shim：src.config → ai_report.config

将旧的 `from src.config.xxx` 导入路径映射到 `ai_report.config`。
新代码应直接使用 `from ai_report.config import ...`。

此模块在未来版本中将被移除。
"""
import importlib
import warnings

_TARGET = "ai_report.config"


def __getattr__(name):
    try:
        mod = importlib.import_module(_TARGET)
        attr = getattr(mod, name, None)
        if attr is not None:
            warnings.warn(
                f"'src.config.{name}' is deprecated, use '{_TARGET}.{name}' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            return attr
    except ImportError:
        pass
    raise AttributeError(f"module 'src.config' has no attribute '{name}'")
