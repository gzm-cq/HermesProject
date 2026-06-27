"""测试共享 Fixtures"""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kanban_reflection.config import KanbanReflectionConfig


@pytest.fixture
def default_config() -> KanbanReflectionConfig:
    """返回默认测试配置"""
    return KanbanReflectionConfig()


@pytest.fixture
def sample_trace_lines() -> list[dict]:
    """返回模拟的 trace 日志行"""
    return [
        {
            "timestamp": "2026-06-05T10:00:00Z",
            "session_id": "test-session",
            "event": "tool_call",
            "tool": "bash",
            "input": "ls /tmp",
            "output": "file1.txt file2.txt",
        },
        {
            "timestamp": "2026-06-05T10:00:05Z",
            "session_id": "test-session",
            "event": "tool_result",
            "tool": "bash",
            "status": "error",
            "error": "command not found",
        },
        {
            "timestamp": "2026-06-05T10:00:10Z",
            "session_id": "test-session",
            "event": "task_status",
            "task_id": "task-123",
            "status": "failed",
            "reason": "tool_execution_error",
        },
    ]


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """返回 Mock 的 LLM 客户端"""
    client = MagicMock()
    client.extract_content.return_value = json.dumps({
        "failure_reason": "bash 命令不存在",
        "failure_type": "tool_execution_error",
        "suggestion": "检查命令是否安装，或使用 which 确认路径",
        "confidence": 0.85,
    })
    client.parse_json_response.return_value = {
        "failure_reason": "bash 命令不存在",
        "failure_type": "tool_execution_error",
        "suggestion": "检查命令是否安装，或使用 which 确认路径",
        "confidence": 0.85,
    }
    return client


@pytest.fixture
def tmp_trace_file(tmp_path: Path, sample_trace_lines: list[dict]) -> Path:
    """创建临时 trace.log 文件"""
    trace_file = tmp_path / "trace.log"
    with open(trace_file, "w", encoding="utf-8") as f:
        for line in sample_trace_lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return trace_file


@pytest.fixture
def env_config() -> KanbanReflectionConfig:
    """通过环境变量加载配置"""
    saved = {}
    env_vars = {
        "KN_REFLECTION_API_URL": "http://test:8080/v1/chat",
        "KN_REFLECTION_MODEL": "test-model",
        "KN_REFLECTION_TIMEOUT": "30",
        "KN_REFLECTION_MAX_TRACE_LINES": "10",
        "KN_REFLECTION_CONFIDENCE": "0.5",
        "KN_REFLECTION_MAX_RETRIES": "5",
        "KN_REFLECTION_LOG_LEVEL": "DEBUG",
    }
    for key, val in env_vars.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = val

    config = KanbanReflectionConfig.from_env()

    # 恢复原始环境变量
    for key, val in saved.items():
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
    return config
