"""嵌入模块测试 — JSON 解析兜底、LLM 因果调用（mock）"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from clustering_analysis.core.embeddings import (
    _parse_llm_json_response,
    call_llm_for_entity_with_causal,
)


class TestParseLLMJsonResponse:
    """测试 _parse_llm_json_response 的三层 JSON 解析兜底"""

    def test_direct_json(self) -> None:
        """第 1 层：完整 JSON 直接解析"""
        content = '{"entity_name": "测试", "causal_pairs": [{"cause_idx": 0, "effect_idx": 1}]}'
        result = _parse_llm_json_response(content)
        assert result is not None
        assert result["entity_name"] == "测试"
        assert len(result["causal_pairs"]) == 1

    def test_markdown_code_block(self) -> None:
        """第 2 层：markdown 代码块中提取 JSON"""
        content = '```json\n{"entity_name": "测试", "causal_pairs": []}\n```'
        result = _parse_llm_json_response(content)
        assert result is not None
        assert result["entity_name"] == "测试"

    def test_markdown_without_lang_tag(self) -> None:
        """第 2 层：代码块无语言标签"""
        content = '```\n{"entity_name": "测试"}\n```'
        result = _parse_llm_json_response(content)
        assert result is not None
        assert result["entity_name"] == "测试"

    def test_raw_json_extract(self) -> None:
        """第 3 层：从文本中提取 JSON 对象"""
        content = '以下是分析结果：\n{"entity_name": "测试", "causal_pairs": [{"cause_idx": 0, "effect_idx": 2}]}\n请确认。'
        result = _parse_llm_json_response(content)
        assert result is not None
        assert result["entity_name"] == "测试"
        assert len(result["causal_pairs"]) == 1

    def test_empty_content(self) -> None:
        """空内容返回 None"""
        assert _parse_llm_json_response("") is None
        assert _parse_llm_json_response("  ") is None

    def test_invalid_content(self) -> None:
        """无 JSON 的文本返回 None"""
        assert _parse_llm_json_response("这是一段普通文本") is None


class TestCallLlmForEntityWithCausal:
    """测试 call_llm_for_entity_with_causal (mock requests)"""

    def _make_mock_response(self, content: str, status: int = 200) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return mock_resp

    @patch("clustering_analysis.core.embeddings.requests")
    def test_successful_extraction(self, mock_requests: MagicMock) -> None:
        """正常场景：LLM 返回完整 JSON"""
        mock_resp = self._make_mock_response(
            '{"entity_name": "数据库连接池异常", "causal_pairs": [{"cause_idx": 0, "effect_idx": 1, "reason": "连接池耗尽导致超时"}]}'
        )
        mock_requests.post.return_value = mock_resp

        texts = ["连接池耗尽", "大量请求超时"]
        entity_name, causal_pairs = call_llm_for_entity_with_causal(texts)

        assert entity_name == "数据库连接池异常"
        assert len(causal_pairs) == 1
        assert causal_pairs[0]["cause_idx"] == 0
        assert causal_pairs[0]["effect_idx"] == 1

    @patch("clustering_analysis.core.embeddings.requests")
    def test_no_causal_pairs(self, mock_requests: MagicMock) -> None:
        """无因果时返回空列表"""
        mock_resp = self._make_mock_response(
            '{"entity_name": "普通事件", "causal_pairs": []}'
        )
        mock_requests.post.return_value = mock_resp

        texts = ["事件A", "事件B"]
        entity_name, causal_pairs = call_llm_for_entity_with_causal(texts)
        assert causal_pairs == []

    @patch("clustering_analysis.core.embeddings.requests")
    def test_invalid_indices_filtered(self, mock_requests: MagicMock) -> None:
        """越界或无效的因果索引被过滤"""
        mock_resp = self._make_mock_response(
            '{"entity_name": "测试", "causal_pairs": ['
            '{"cause_idx": 0, "effect_idx": 5, "reason": "越界"},'
            '{"cause_idx": -1, "effect_idx": 1, "reason": "负数"},'
            '{"cause_idx": 0, "effect_idx": 0, "reason": "自环"},'
            '{"cause_idx": 0, "effect_idx": 1, "reason": "有效"}'
            "]}"
        )
        mock_requests.post.return_value = mock_resp

        texts = ["A", "B"]
        _, causal_pairs = call_llm_for_entity_with_causal(texts)
        assert len(causal_pairs) == 1
        assert causal_pairs[0]["reason"] == "有效"

    @patch("clustering_analysis.core.embeddings.requests")
    def test_markdown_json_response(self, mock_requests: MagicMock) -> None:
        """LLM 返回 markdown 包裹的 JSON"""
        mock_resp = self._make_mock_response(
            "```json\n{\"entity_name\": \"测试实体\", \"causal_pairs\": []}\n```"
        )
        mock_requests.post.return_value = mock_resp

        entity_name, causal_pairs = call_llm_for_entity_with_causal(["文本"])
        assert entity_name == "测试实体"

    @patch("clustering_analysis.core.embeddings.requests")
    def test_retry_on_failure(self, mock_requests: MagicMock) -> None:
        """请求失败后重试，最终返回兜底结果"""
        mock_requests.post.side_effect = Exception("connection failed")

        texts = ["测试"]
        entity_name, causal_pairs = call_llm_for_entity_with_causal(texts, retries=2)
        assert entity_name == "提取失败"
        assert causal_pairs == []
        # 确保重试了 2 次
        assert mock_requests.post.call_count == 2

    @patch("clustering_analysis.core.embeddings.requests")
    def test_requests_not_installed(self, mock_requests: Any) -> None:
        """requests 未安装时返回 fallback"""
        with patch("clustering_analysis.core.embeddings.requests", None):
            entity_name, causal_pairs = call_llm_for_entity_with_causal(["文本"])
            assert "requests 未安装" in entity_name
            assert causal_pairs == []
