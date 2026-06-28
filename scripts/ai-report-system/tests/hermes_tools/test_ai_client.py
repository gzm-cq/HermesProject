"""
测试：ai_client 委托式 LLM 调用
================================
遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import json

import pytest

from ai_report.adapters.ai_client import call_llm, reload_config, set_task_executor


# ═════════════════════════════════════════════════════════════
# set_task_executor 测试
# ═════════════════════════════════════════════════════════════

class TestSetTaskExecutor:
    """set_task_executor 测试。"""

    @pytest.mark.unit
    def test_set_and_clear(self) -> None:
        """设置和清除 task_executor。"""
        set_task_executor(lambda goal="", context="": "mock response")
        # 设置后 call_llm 不应崩溃
        result = call_llm("test")
        assert isinstance(result, str)
        set_task_executor(None)

    @pytest.mark.unit
    def test_set_none_then_direct_fallback(self) -> None:
        """清除后 call_llm 应走降级路径（不崩溃）。"""
        set_task_executor(None)
        # 降级路径需要真实的 API key，这里只验证不崩溃
        result = call_llm("test", max_tokens=10)
        assert isinstance(result, str)  # 要么是 LLM 响应，要么是空字符串


# ═════════════════════════════════════════════════════════════
# 委托路径测试
# ═════════════════════════════════════════════════════════════

class TestAgentDelegation:
    """通过 task_executor 委托给 Agent 的路径。"""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        """设置 mock task_executor（测试完恢复）。"""
        set_task_executor(self._mock_executor)
        yield
        set_task_executor(None)

    def _mock_executor(self, goal: str = "", context: str = "") -> str:
        """模拟 Agent 返回结构化响应。"""
        ctx = json.loads(context) if context else {}
        prompt = ctx.get("prompt", "")
        if "hello" in prompt.lower():
            return "Hello! How can I help you today?\n---END---"
        return f"Mock response to: {prompt[:50]}\n---END---"

    @pytest.mark.unit
    def test_call_llm_returns_string(self) -> None:
        """call_llm 返回字符串。"""
        result = call_llm("Say hello", max_tokens=20)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.unit
    def test_call_llm_with_system_prompt(self) -> None:
        """含 system prompt 的调用。"""
        result = call_llm("Hello", system_prompt="Be concise", max_tokens=10)
        assert isinstance(result, str)
        assert "Hello" in result

    @pytest.mark.unit
    def test_call_llm_empty_prompt(self) -> None:
        """空 prompt 不崩溃。"""
        result = call_llm("")
        assert isinstance(result, str)

    @pytest.mark.unit
    def test_parses_end_marker(self) -> None:
        """---END--- 标记后的内容被过滤。"""
        result = call_llm("hello")
        assert "---END---" not in result

    @pytest.mark.unit
    def test_reload_config(self) -> None:
        """reload_config 不抛异常。"""
        reload_config()
        assert True


# ═════════════════════════════════════════════════════════════
# 降级路径测试（直接 HTTP 调用）
# ═════════════════════════════════════════════════════════════

class TestDirectFallback:
    """无 task_executor 时的降级路径。"""

    @pytest.mark.unit
    def test_direct_fallback_returns_string(self) -> None:
        """降级路径返回字符串（无论有无真实配置）。"""
        set_task_executor(None)
        result = call_llm("Say 'ok'", max_tokens=10)
        assert isinstance(result, str)
        # 有真实配置时会调通 LLM；无配置时返回空字符串

    @pytest.mark.integration
    @pytest.mark.skip(reason="requires API key and direct HTTP access")
    def test_direct_with_real_config(self) -> None:
        """有真实配置时降级路径可用。"""
        set_task_executor(None)
        result = call_llm("Say 'ok'", max_tokens=10)
        assert isinstance(result, str)
        assert len(result) > 0
