"""向后兼容 shim：使 src 成为可导入的顶层包。

此包将旧的 `from src.xxx import ...` 导入路径映射到新的 `ai_report.xxx`。
新代码应直接使用 `from ai_report.xxx import ...`。

注意：此兼容层将在未来版本中移除。
"""
