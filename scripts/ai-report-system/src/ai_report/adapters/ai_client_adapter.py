"""AIClient 适配器 — 将模块级 ai_client 函数封装为 AIClientProtocol 实现

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .protocols import AIClientProtocol
from . import ai_client as _module

logger = logging.getLogger(__name__)

__all__ = ["AIClientAdapter"]


class AIClientAdapter:
    """将 ai_client 模块级函数封装为 AIClientProtocol 实例。

    用法:
        adapter = AIClientAdapter()
        adapter.set_task_executor(my_executor)
        result = adapter.call_llm("Hello", system_prompt="You are helpful")
    """

    def __init__(self) -> None:
        self._task_executor: Callable[..., str] | None = None

    def call_llm(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """委托给 ai_client.call_llm 模块函数。"""
        return _module.call_llm(
            prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def set_task_executor(self, executor: Any | None) -> None:
        """设置 task_executor，同时同步到模块级全局状态。

        保持与原 ai_client.set_task_executor 行为一致。
        """
        self._task_executor = executor
        _module.set_task_executor(executor)


# 运行时协议验证（开发期自检）
def _verify_protocol() -> None:
    """验证 AIClientAdapter 满足 AIClientProtocol。"""
    assert isinstance(AIClientAdapter(), AIClientProtocol), (
        "AIClientAdapter 未满足 AIClientProtocol"
    )


_verify_protocol()
