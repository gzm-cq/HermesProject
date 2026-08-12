"""Hermes 统一共享库（hermes_common）。

跨「脚本层（scripts/*）」与「插件层（plugins/*）」复用的纯依赖工具集中地：
  - ledger:     F-1 统一反馈账本（append_ledger_event，零依赖）
  - llm_guard:  所有 LLM 调用的统一护栏（解析 / 重试 / 退避 / 限速，零第三方依赖）
  - text_utils: 关键词提取 / CJK 处理

消费方将本包父目录（开发态 libs/hermes_common 或生产态 /root/.hermes/lib）注入
sys.path 后，以 `from hermes_common.xxx import ...` 使用。
"""

from .ledger import append_ledger_event
from .text_utils import CJK_STOP_CHARS, extract_keywords

__all__ = ["append_ledger_event", "CJK_STOP_CHARS", "extract_keywords"]
