"""LLMClient 单元测试 — JSON 解析三路径、重试机制、token 收集、截断函数。"""

import json
from unittest.mock import MagicMock, patch

import pytest

from memory_cleanup.adapters.llm_client import LLMClient, _truncate
from memory_cleanup.config import AppConfig
from memory_cleanup.core.prompts import build_system_prompt


class TestTruncate:
    """测试 _truncate 截断函数。"""

    def test_short_text_stays_unchanged(self) -> None:
        assert _truncate("Hello World") == "Hello World"

    def test_exactly_max_len_stays_unchanged(self) -> None:
        text = "x" * 400
        assert _truncate(text) == text

    def test_long_text_no_newline_hard_cut(self) -> None:
        text = "x" * 500
        result = _truncate(text)
        assert result.endswith("…（截断）")
        assert len(result) <= 400
        assert "x" * 394 in result

    def test_long_text_with_newline_cuts_at_boundary(self) -> None:
        """有换行符且在 max_len 的一半之后，应在换行处截断。"""
        text = "a" * 210 + "\n" + "b" * 300
        result = _truncate(text)
        assert result.endswith("…（截断）")
        assert "a" * 210 in result
        assert "b" not in result

    def test_long_text_newline_too_early_hard_cut(self) -> None:
        """换行符在 max_len 的一半之前，应硬截断。"""
        text = "a\n" + "b" * 450
        result = _truncate(text)
        assert result.endswith("…（截断）")

    def test_empty_string(self) -> None:
        assert _truncate("") == ""


class TestLLMClientTokenTracking:
    """测试 LLMClient token 收集功能。"""

    def _make_response(self, content: str, usage: dict | None = None) -> MagicMock:
        resp = MagicMock()
        data = {"choices": [{"message": {"content": content}}]}
        if usage:
            data["usage"] = usage
        resp.json.return_value = data
        return resp

    def test_token_counts_accumulate(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)
        system_prompt = build_system_prompt("MEMORY", 0)

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [
                self._make_response('{"merge":[],"remove":[],"compress":[]}', {"prompt_tokens": 50, "completion_tokens": 30}),
                self._make_response('{"merge":[],"remove":[],"compress":[]}', {"prompt_tokens": 40, "completion_tokens": 20}),
            ]
            client.classify_batch(["条目A", "条目B"], 0, "MEMORY", system_prompt)
            client.classify_batch(["条目C"], 2, "MEMORY", system_prompt)

        assert client.total_prompt_tokens == 90
        assert client.total_completion_tokens == 50

    def test_no_usage_in_response_does_not_crash(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)
        system_prompt = build_system_prompt("MEMORY", 0)

        with patch("requests.post") as mock_post:
            mock_post.return_value = self._make_response('{"merge":[],"remove":[],"compress":[]}')
            client.classify_batch(["条目A"], 0, "MEMORY", system_prompt)

        assert client.total_prompt_tokens == 0
        assert client.total_completion_tokens == 0


class TestLLMClientInitWarning:
    """测试 LLMClient 初始化时的 HTTP 警告。"""

    def test_http_url_with_key_logs_warning(self, app_config: AppConfig, caplog: pytest.LogCaptureFixture) -> None:
        cfg = AppConfig(llm_key="sk-test", llm_url="http://example.com")
        with caplog.at_level("WARNING"):
            LLMClient(cfg)
        assert len(caplog.records) >= 1
        warn_msgs = [r.message for r in caplog.records if "HTTP" in r.message]
        assert len(warn_msgs) >= 1

    def test_https_url_no_warning(self, app_config: AppConfig, caplog: pytest.LogCaptureFixture) -> None:
        cfg = AppConfig(llm_key="sk-test", llm_url="https://example.com")
        with caplog.at_level("WARNING"):
            LLMClient(cfg)
        warn_msgs = [r.message for r in caplog.records if "HTTP" in r.message]
        assert len(warn_msgs) == 0

    def test_no_key_no_warning(self, app_config: AppConfig, caplog: pytest.LogCaptureFixture) -> None:
        cfg = AppConfig(llm_key="", llm_url="http://example.com")
        with caplog.at_level("WARNING"):
            LLMClient(cfg)
        warn_msgs = [r.message for r in caplog.records if "HTTP" in r.message]
        assert len(warn_msgs) == 0


class TestLLMClientExponentialBackoff:
    """测试 LLMClient 指数退避重试。"""

    def _make_response(self, content: str) -> MagicMock:
        resp = MagicMock()
        resp.json.return_value = {"choices": [{"message": {"content": content}}]}
        return resp

    def test_exponential_backoff_uses_time_sleep(self, app_config: AppConfig) -> None:
        """验证失败重试调用了 time.sleep（表明退避生效）。

        _call 内层 3 次，前 2 次各触发 1 次 sleep，共 2 次。
        """
        client = LLMClient(app_config)
        system_prompt = build_system_prompt("MEMORY", 0)

        with patch("requests.post", side_effect=Exception("fail")) as mock_post, \
             patch("memory_cleanup.adapters.llm_client.time.sleep") as mock_sleep:
            result = client.classify_batch(["条目A"], 0, "MEMORY", system_prompt)

        assert "error" in result
        # _call 内层 3 次：attempt 0→sleep, attempt 1→sleep, attempt 2→exhausted
        assert mock_sleep.call_count == 2

    def test_no_sleep_on_success(self, app_config: AppConfig) -> None:
        """第 1 次成功不应调用 time.sleep。"""
        client = LLMClient(app_config)
        system_prompt = build_system_prompt("MEMORY", 0)

        with patch("requests.post") as mock_post, \
             patch("memory_cleanup.adapters.llm_client.time.sleep") as mock_sleep:
            mock_post.return_value = self._make_response('{"merge":[],"remove":[],"compress":[]}')
            client.classify_batch(["条目A"], 0, "MEMORY", system_prompt)

        assert mock_sleep.call_count == 0


class TestLLMClientParseJson:
    """测试 JSON 三路径解析逻辑。"""

    def test_clean_json(self) -> None:
        raw = '{"merge": [], "remove": [], "compress": []}'
        result = LLMClient._parse_json(raw)
        assert result == {"merge": [], "remove": [], "compress": []}

    def test_json_with_markdown_fences(self) -> None:
        raw = "```json\n{\"merge\": [], \"remove\": [], \"compress\": []}\n```"
        result = LLMClient._parse_json(raw)
        assert result is not None
        assert "merge" in result

    def test_json_with_leading_text(self) -> None:
        """正则回退路径：原始文本包含多余前缀，且 JSON 本身无嵌套对象。"""
        # 注：正则 \{[^{}]*\} 不支持嵌套，仅匹配无嵌套结构
        raw = '以下是分类结果：{"index": 1, "原因": "test"}'
        result = LLMClient._parse_json(raw)
        assert result is not None
        assert result.get("index") == 1

    def test_invalid_json_returns_none(self) -> None:
        raw = "这不是 JSON"
        result = LLMClient._parse_json(raw)
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = LLMClient._parse_json("")
        assert result is None


class TestLLMClientClassifyBatch:
    """测试 classify_batch 方法。"""

    def _make_response(self, content: str) -> MagicMock:
        resp = MagicMock()
        resp.json.return_value = {"choices": [{"message": {"content": content}}]}
        return resp

    def test_successful_classify(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)
        expected = {"merge": [], "remove": [{"index": 0, "原因": "test"}], "compress": []}
        system_prompt = build_system_prompt("MEMORY", 0)

        with patch("requests.post") as mock_post:
            mock_post.return_value = self._make_response(json.dumps(expected))
            result = client.classify_batch(["条目A"], 0, "MEMORY", system_prompt)

        assert result["remove"][0]["index"] == 0
        assert "error" not in result

    def test_retry_on_failure(self, app_config: AppConfig) -> None:
        """HTTP 失败后应重试，第3次成功则返回正确结果。"""
        client = LLMClient(app_config)
        success_resp = self._make_response('{"merge":[],"remove":[],"compress":[]}')
        system_prompt = build_system_prompt("MEMORY", 0)

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [
                Exception("连接失败"),
                Exception("超时"),
                success_resp,
            ]
            result = client.classify_batch(["条目A"], 0, "MEMORY", system_prompt)

        assert "error" not in result
        assert mock_post.call_count == 3

    def test_all_retries_exhausted(self, app_config: AppConfig) -> None:
        """3次全部失败应返回 error 字典。"""
        client = LLMClient(app_config)
        system_prompt = build_system_prompt("MEMORY", 0)

        with patch("requests.post", side_effect=Exception("网络错误")):
            result = client.classify_batch(["条目A"], 0, "MEMORY", system_prompt)

        assert "error" in result

    def test_batch_offset_in_prompt(self, app_config: AppConfig) -> None:
        """批次偏移量应传入 user prompt 的条目索引。"""
        client = LLMClient(app_config)
        system_prompt = build_system_prompt("MEMORY", 20)
        success_resp = self._make_response('{"merge":[],"remove":[],"compress":[]}')

        with patch("requests.post") as mock_post:
            mock_post.return_value = success_resp
            client.classify_batch(["条目A", "条目B"], 20, "MEMORY", system_prompt)

        call_kwargs = mock_post.call_args
        user_msg = call_kwargs[1]["json"]["messages"][1]["content"]
        assert "[20]" in user_msg
        assert "[21]" in user_msg


class TestBuildSystemPrompt:
    """测试 prompts.py 的 system prompt 生成。"""

    def test_memory_prompt_contains_offset(self) -> None:
        prompt = build_system_prompt("MEMORY", 40)
        assert "40" in prompt

    def test_user_prompt_contains_offset(self) -> None:
        prompt = build_system_prompt("USER", 0)
        assert "0" in prompt

    def test_memory_prompt_mentions_remove_rules(self) -> None:
        prompt = build_system_prompt("MEMORY", 0)
        assert "remove" in prompt
        assert "business" in prompt.lower() or "业务数据" in prompt

    def test_user_prompt_is_conservative(self) -> None:
        """USER prompt 应比 MEMORY 更保守。"""
        user_prompt = build_system_prompt("USER", 0)
        assert "极少" in user_prompt or "谨慎" in user_prompt

    def test_output_format_in_prompt(self) -> None:
        """两种 prompt 都应包含 JSON 输出格式说明。"""
        for source in ["MEMORY", "USER"]:
            prompt = build_system_prompt(source, 0)
            assert '"merge"' in prompt
            assert '"remove"' in prompt
            assert '"compress"' in prompt
