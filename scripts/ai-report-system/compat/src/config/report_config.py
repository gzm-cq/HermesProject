"""兼容 shim：src.config.report_config → ai_report.config

将旧的 `from src.config.report_config import ...` 导入路径映射到 `ai_report.config`。
新代码应直接使用 `from ai_report.config import ...`。

此模块在未来版本中将被移除。
"""
import warnings

warnings.warn(
    "src.config.report_config is deprecated, use ai_report.config instead.",
    DeprecationWarning,
    stacklevel=2,
)
from ai_report.config import *  # noqa: F401,F403
