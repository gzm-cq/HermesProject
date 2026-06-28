"""llm_client.py 单元测试 — LLM 客户端适配器。"""

import json
from unittest.mock import MagicMock, patch

import pytest

from recall_eval.adapters.llm_client import LLMClient
from recall_eval.config import AppConfig


class TestLLMClientInit:
    """LLMClient 初始化测试。"""

    def test_default_config(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)
        assert client._url == app_config.eval_api_url
        assert client._model == app_config.eval_model
        assert client.total_prompt_tokens == 0
        assert client.total_completion_tokens == 0

    def test_with_api_key(self, app_config: AppConfig) -> None:
        app_config.eval_api_key = "sk-test-key"
        client = LLMClient(app_config)
        assert client._key == "sk-test-key"


class TestParseJson:
    """_parse_json 静态方法测试。"""

    def test_normal_json(self) -> None:
        raw = '{"score": 0.8, "reason": "good"}'
        result = LLMClient._parse_json(raw)
        assert result is not None
        assert result["score"] == 0.8
        assert result["reason"] == "good"

    def test_json_with_markdown(self) -> None:
        raw = "```json\n{\"score\": 0.9}\n```"
        result = LLMClient._parse_json(raw)
        assert result is not None
        assert result["score"] == 0.9

    def test_json_with_whitespace(self) -> None:
        raw = '  \n  {"score": 0.7}  \n  '
        result = LLMClient._parse_json(raw)
        assert result is not None
        assert result["score"] == 0.7

    def test_invalid_json(self) -> None:
        raw = "this is not json"
        result = LLMClient._parse_json(raw)
        assert result is None

    def test_nested_json_extraction(self) -> None:
        raw = 'Some text before {"score": 0.85, "reason": "test"} some text after'
        result = LLMClient._parse_json(raw)
        assert result is not None
        assert result["score"] == 0.85


class TestFaithfulnessEvaluation:
    """忠实度评估测试。"""

    def test_evaluate_faithfulness_success(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)
        mock_response = json.dumps({
            "score": 0.85,
            "reason": "大部分内容基于上下文",
            "supported_claims": ["claim1", "claim2"],
            "unsupported_claims": [],
        })

        with patch.object(client, "_call", return_value=mock_response):
            result = client.evaluate_faithfulness("query", "context", "answer")

        assert result["score"] == 0.85
        assert "reason" in result
        assert "supported_claims" in result

    def test_evaluate_faithfulness_llm_failure(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)

        with patch.object(client, "_call", return_value=None):
            result = client.evaluate_faithfulness("query", "context", "answer")

        assert result["score"] == 0.0
        assert "error" in result

    def test_evaluate_faithfulness_parse_failure(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)

        with patch.object(client, "_call", return_value="invalid json"):
            result = client.evaluate_faithfulness("query", "context", "answer")

        assert result["score"] == 0.0
        assert "error" in result


class TestRelevanceEvaluation:
    """相关性评估测试。"""

    def test_evaluate_relevance_success(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)
        mock_response = json.dumps({
            "score": 0.9,
            "reason": "高度相关",
            "relevant_topics": ["topic1"],
            "irrelevant_topics": [],
        })

        with patch.object(client, "_call", return_value=mock_response):
            result = client.evaluate_relevance("query", "context")

        assert result["score"] == 0.9
        assert "relevant_topics" in result

    def test_evaluate_relevance_failure(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)

        with patch.object(client, "_call", return_value=None):
            result = client.evaluate_relevance("query", "context")

        assert result["score"] == 0.0
        assert "error" in result


class TestCoverageEvaluation:
    """覆盖率评估测试。"""

    def test_evaluate_coverage_success(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)
        mock_response = json.dumps({
            "score": 0.75,
            "reason": "覆盖了大部分要点",
            "query_points": ["p1", "p2"],
            "covered_points": ["p1"],
            "missing_points": ["p2"],
        })

        with patch.object(client, "_call", return_value=mock_response):
            result = client.evaluate_coverage("query", "context")

        assert result["score"] == 0.75
        assert "covered_points" in result
        assert "missing_points" in result

    def test_evaluate_coverage_failure(self, app_config: AppConfig) -> None:
        client = LLMClient(app_config)

        with patch.object(client, "_call", return_value=None):
            result = client.evaluate_coverage("query", "context")

        assert result["score"] == 0.0
        assert "error" in result
