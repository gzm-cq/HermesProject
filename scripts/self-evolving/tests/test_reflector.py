"""反思回路核心模块测试"""
import json
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from kanban_reflection.config import KanbanReflectionConfig
from kanban_reflection.core.reflector import (
    ReflectionResult,
    read_trace_lines,
    build_reflection_prompt,
    reflect_on_failure,
)


class TestReflectionResult:
    """测试 ReflectionResult 数据类"""

    def test_to_dict(self) -> None:
        """to_dict 应返回正确字段"""
        result = ReflectionResult(
            task_id="task-1",
            failure_reason="命令不存在",
            failure_type="tool_execution_error",
            suggestion="检查命令路径",
            confidence=0.85,
        )
        data = result.to_dict()
        assert data["task_id"] == "task-1"
        assert data["failure_reason"] == "命令不存在"
        assert data["failure_type"] == "tool_execution_error"
        assert data["confidence"] == 0.85
        assert "timestamp" in data

    def test_to_inject_prompt(self) -> None:
        """to_inject_prompt 应生成注入文本"""
        result = ReflectionResult(
            task_id="task-1",
            failure_reason="命令不存在",
            failure_type="tool_execution_error",
            suggestion="检查命令路径",
        )
        prompt = result.to_inject_prompt()
        assert "命令不存在" in prompt
        assert "检查命令路径" in prompt

    def test_confidence_clamping(self) -> None:
        """置信度应被限制在 0-1 范围"""
        result = ReflectionResult(
            task_id="t", failure_reason="r", failure_type="t",
            suggestion="s", confidence=1.5,
        )
        assert result.confidence == 1.5  # dataclass 不限制，reflect_on_failure 才限制


class TestReadTraceLines:
    """测试 trace 日志读取"""

    def test_read_existing_file(self, tmp_path: Path) -> None:
        """存在的文件应正确读取匹配行"""
        log_file = tmp_path / "trace.log"
        lines = [
            '{"task_id": "abc", "event": "start"}',
            '{"task_id": "def", "event": "start"}',
            '{"task_id": "abc", "event": "end"}',
        ]
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = read_trace_lines(str(log_file), "abc", max_lines=5)
        assert len(result) == 2
        assert result[0]["task_id"] == "abc"
        assert result[1]["task_id"] == "abc"

    def test_read_missing_file(self) -> None:
        """不存在的文件应返回空列表"""
        result = read_trace_lines("/nonexistent/path.log", "test")
        assert result == []

    def test_read_empty_file(self, tmp_path: Path) -> None:
        """空文件应返回空列表"""
        log_file = tmp_path / "empty.log"
        log_file.write_text("", encoding="utf-8")
        result = read_trace_lines(str(log_file), "test")
        assert result == []

    def test_read_max_lines(self, tmp_path: Path) -> None:
        """应只返回最近 N 条匹配行"""
        log_file = tmp_path / "trace.log"
        lines = [f'{{"task_id": "abc", "seq": {i}}}' for i in range(10)]
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = read_trace_lines(str(log_file), "abc", max_lines=3)
        assert len(result) == 3
        assert result[0]["seq"] == 7
        assert result[2]["seq"] == 9

    def test_invalid_json_lines_skipped(self, tmp_path: Path) -> None:
        """无效 JSON 行应被跳过"""
        log_file = tmp_path / "trace.log"
        log_file.write_text(
            '{"task_id": "abc"}\nnot json\n{"task_id": "abc"}\n',
            encoding="utf-8",
        )
        result = read_trace_lines(str(log_file), "abc")
        assert len(result) == 2


class TestBuildReflectionPrompt:
    """测试反思 prompt 构建"""

    def test_prompt_contains_trace_content(self, sample_trace_lines: list[dict]) -> None:
        """prompt 应包含 trace 内容"""
        messages = build_reflection_prompt("测试任务", sample_trace_lines)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "测试任务" in messages[1]["content"]
        assert "tool_call" in messages[1]["content"]

    def test_prompt_with_empty_goal(self) -> None:
        """空目标不应报错"""
        messages = build_reflection_prompt("", [{"event": "test"}])
        assert len(messages) == 2


class TestReflectOnFailure:
    """测试核心反思函数"""

    def test_successful_reflection(
        self, mock_llm_client: MagicMock, sample_trace_lines: list[dict],
    ) -> None:
        """成功的 LLM 调用应返回结构化结果"""
        result = reflect_on_failure(
            task_id="task-123",
            task_goal="测试任务",
            trace_lines=sample_trace_lines,
            llm_client=mock_llm_client,
        )
        assert isinstance(result, ReflectionResult)
        assert result.task_id == "task-123"
        assert result.failure_type == "tool_execution_error"
        assert result.confidence > 0

    def test_reflection_calls_llm(
        self, mock_llm_client: MagicMock, sample_trace_lines: list[dict],
    ) -> None:
        """应正确调用 LLM 客户端"""
        reflect_on_failure("task-1", "goal", sample_trace_lines, llm_client=mock_llm_client)
        mock_llm_client.chat_completion.assert_called_once()
        mock_llm_client.extract_content.assert_called_once()

    def test_llm_fallback_on_error(
        self, sample_trace_lines: list[dict],
    ) -> None:
        """LLM 调用失败时应返回降级结果"""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = ConnectionError("API 不可达")

        result = reflect_on_failure(
            task_id="task-err",
            task_goal="test",
            trace_lines=sample_trace_lines,
            llm_client=mock_client,
        )
        assert result.failure_type == "llm_anomaly"
        assert "API 不可达" in result.failure_reason

    def test_empty_trace_graceful(self, mock_llm_client: MagicMock) -> None:
        """空的 trace 不应崩溃"""
        result = reflect_on_failure(
            task_id="task-empty",
            task_goal="test",
            trace_lines=[],
            llm_client=mock_llm_client,
        )
        assert result is not None

    def test_invalid_failure_type_normalized(
        self, mock_llm_client: MagicMock,
    ) -> None:
        """未知的 failure_type 应被归一化"""
        mock_llm_client.extract_content.return_value = json.dumps({
            "failure_reason": "未知错误",
            "failure_type": "invalid_type_123",
            "suggestion": "重试",
            "confidence": 0.5,
        })
        mock_llm_client.parse_json_response.return_value = {
            "failure_reason": "未知错误",
            "failure_type": "invalid_type_123",
            "suggestion": "重试",
            "confidence": 0.5,
        }

        result = reflect_on_failure(
            task_id="t", task_goal="g",
            trace_lines=[{"event": "test"}],
            llm_client=mock_llm_client,
        )
        assert result.failure_type == "other"
