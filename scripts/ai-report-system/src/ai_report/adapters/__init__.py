"""AI报告生成系统外部适配层

封装所有外部系统集成，包括Hermes工具、LLM调用、搜索、文档解析等。

核心原则：
  - core/ 只导入 protocols.py 中的 Protocol，不导入具体实现
  - adapters/ 的具体类实现 Protocol 但不导入 core/
  - 生产环境使用 create_orchestrator() 工厂函数创建编排器
"""

from .protocols import (
    AIClientProtocol,
    QualityAssessorProtocol,
    WebSearchProtocol,
    HermesToolProtocol,
    MemoryProtocol,
)

__all__ = [
    "AIClientProtocol",
    "QualityAssessorProtocol",
    "WebSearchProtocol",
    "HermesToolProtocol",
    "MemoryProtocol",
]
