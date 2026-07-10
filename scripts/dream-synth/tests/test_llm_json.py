"""call_llm_json 单元测试 — JSON 解析、重试、边界。"""
import json
from unittest.mock import patch

import pytest


class TestCallLlmJson:
    def test_valid_json_returns_dict(self, module):
        """标准 JSON 输出应正确解析。"""
        expected = {"score": 5, "reason": "好"}
        with patch.object(module, "call_llm", return_value=json.dumps(expected, ensure_ascii=False)):
            result = module.call_llm_json("prompt", "test-model")
        assert result == expected

    def test_json_with_markdown_fence_stripped(self, module):
        """带 ```json 标记的输出也能解析。"""
        raw = "```json\n{\"score\": 3, \"reason\": \"一般\"}\n```"
        with patch.object(module, "call_llm", return_value=raw):
            result = module.call_llm_json("prompt", "test-model")
        assert result["score"] == 3
        assert result["reason"] == "一般"

    def test_json_surrounded_by_text(self, module):
        """JSON 前后有说明文字时也能提取。"""
        raw = "好的，这是分析结果：\n{\"score\": 4, \"reason\": \"不错\"}\n希望对你有帮助。"
        with patch.object(module, "call_llm", return_value=raw):
            result = module.call_llm_json("prompt", "test-model")
        assert result["score"] == 4

    def test_invalid_json_retries_then_returns_empty(self, module):
        """JSON 解析失败时会重试，最终返回空字典。"""
        call_count = 0

        def fake_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return "not json at all"

        with patch.object(module, "call_llm", side_effect=fake_call):
            result = module.call_llm_json("prompt", "test-model", max_retries=2)

        assert result == {}
        assert call_count == 3  # 1 次初始 + 2 次重试

    def test_second_try_succeeds(self, module):
        """第一次失败，第二次成功解析。"""
        responses = ["bad output", "{\"promote\": true, \"category\": \"concepts\"}"]
        call_count = 0

        def fake_call(*args, **kwargs):
            nonlocal call_count
            r = responses[call_count]
            call_count += 1
            return r

        with patch.object(module, "call_llm", side_effect=fake_call):
            result = module.call_llm_json("prompt", "test-model", max_retries=2)

        assert result["promote"] is True
        assert result["category"] == "concepts"
        assert call_count == 2

    def test_empty_string_returns_empty(self, module):
        """空输出返回空字典。"""
        with patch.object(module, "call_llm", return_value=""):
            result = module.call_llm_json("prompt", "test-model", max_retries=0)
        assert result == {}

    def test_nested_json_parsed_correctly(self, module):
        """嵌套 JSON 结构应完整解析。"""
        expected = {
            "patterns": [
                {"topic": "缓存", "evidence_count": 3, "evidence_ids": ["a", "b"]}
            ]
        }
        with patch.object(module, "call_llm", return_value=json.dumps(expected, ensure_ascii=False)):
            result = module.call_llm_json("prompt", "test-model")
        assert len(result["patterns"]) == 1
        assert result["patterns"][0]["topic"] == "缓存"
