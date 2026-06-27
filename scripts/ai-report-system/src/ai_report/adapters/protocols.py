"""适配器协议定义 — 声明 core 层对外部依赖的抽象接口

核心原则：
  - core/ 只导入本文件中的 Protocol，不导入 adapters/ 的具体实现
  - adapters/ 的具体类实现 Protocol 但不导入 core/
  - 运行时可用 isinstance(obj, XxxProtocol) 做结构化类型检查

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


# ── LLM 调用协议 ────────────────────────────────────────────


@runtime_checkable
class AIClientProtocol(Protocol):
    """LLM 调用适配器协议。

    封装 call_llm 行为，使调用方无需关心
    委托模式（Agent）还是直接 HTTP 调用。
    """

    def call_llm(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """调用 LLM 并返回文本响应。

        Args:
            prompt: 用户提示
            system_prompt: 系统提示（可选）
            max_tokens: 最大输出 token 数
            temperature: 温度

        Returns:
            LLM 返回的文本；全部失败返回空字符串
        """
        ...

    def set_task_executor(self, executor: Any | None) -> None:
        """注入 Hermes Agent 委托执行器。

        Args:
            executor: 封装 delegate_task 的可调用对象；None 则清除
        """
        ...


# ── 质量评估协议 ────────────────────────────────────────────


@runtime_checkable
class QualityAssessorProtocol(Protocol):
    """质量评估器协议。

    对报告内容进行多维度质量评估。
    """

    def assess(
        self,
        content: str,
        report_id: str | None = None,
        dimensions: list[str] | None = None,
    ) -> Any:
        """评估内容质量。

        Args:
            content: 待评估内容
            report_id: 报告标识
            dimensions: 要评估的维度列表，None 为全部

        Returns:
            评估结果对象（含 overall_score, quality_grade 等）
        """
        ...


# ── Web 搜索协议 ────────────────────────────────────────────


@runtime_checkable
class WebSearchProtocol(Protocol):
    """Web 搜索适配器协议。

    支持委托搜索（Hermes Agent）和直调搜索（Tavily）两种模式。
    """

    def search(
        self,
        query: str,
        max_results: int = 5,
        **kwargs: Any,
    ) -> Any:
        """执行搜索。

        Args:
            query: 搜索查询
            max_results: 最大结果数

        Returns:
            搜索结果（类型由具体实现决定）
        """
        ...

    def prepare(
        self,
        query: str,
        max_results: int = 5,
        context: str = "",
    ) -> Any:
        """准备搜索委托任务（供 Hermes Agent 执行）。

        Args:
            query: 搜索查询
            max_results: 最大结果数
            context: 额外上下文

        Returns:
            委托任务定义
        """
        ...


# ── Hermes 工具协议 ─────────────────────────────────────────


@runtime_checkable
class HermesToolProtocol(Protocol):
    """Hermes 工具适配器协议。

    封装 Browser、File 等工具操作。
    """

    def execute_tool(
        self,
        wrapper_name: str,
        operation: str,
        **kwargs: Any,
    ) -> Any:
        """执行工具操作。

        Args:
            wrapper_name: 工具名称（'browser', 'file' 等）
            operation: 操作名称
            **kwargs: 操作参数

        Returns:
            操作结果
        """
        ...


# ── 记忆系统协议 ────────────────────────────────────────────


@runtime_checkable
class MemoryProtocol(Protocol):
    """记忆系统适配器协议。

    提供记忆上下文检索和交互日志存储。
    """

    def provide_memory_context(
        self,
        query: str,
        max_contexts: int = 3,
    ) -> list[Any]:
        """提供记忆上下文。

        Args:
            query: 查询文本
            max_contexts: 最大上下文数量

        Returns:
            相关记忆上下文列表
        """
        ...

    def store_interaction_context(
        self,
        peer_id: str,
        context: dict[str, Any],
    ) -> None:
        """存储交互上下文。

        Args:
            peer_id: 对方 ID
            context: 上下文数据
        """
        ...
