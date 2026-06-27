"""phase/analyze.py 单元测试"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from knowledge_tree_builder.config import AppConfig
from knowledge_tree_builder.phase.analyze import (
    _MAX_ARTICLE_CHARS,
    _parse_analysis_response,
    _validate_candidate,
    analyze_article,
)


class TestAnalyzeArticle:
    """analyze_article 主函数测试"""

    @patch("knowledge_tree_builder.phase.analyze.call_llm_json")
    def test_normal_extraction(self, mock_llm: Any, default_config: AppConfig) -> None:
        mock_llm.return_value = {
            "analysis": {"content_summary": "SE-Agent 综述", "empty_article": False},
            "candidates": [
                {"text": "Revision 算子通过分析失败轨迹来修改代码实现", "type": "principle", "claims_count": 1, "claim_list": ["Revision 算子通过分析失败轨迹来修改代码实现"]},
                {"text": "自进化Agent分为三大范式：Revision、Recombination、Refinement", "type": "key_point", "claims_count": 1, "claim_list": ["自进化Agent分为三大范式：Revision、Recombination、Refinement"]},
            ],
        }
        result = analyze_article("文章内容", "测试文章", config=default_config)
        assert len(result["candidates"]) == 2
        assert result["candidates"][0]["type"] == "principle"
        assert result["candidates"][0]["claim_list"] == ["Revision 算子通过分析失败轨迹来修改代码实现"]

    @patch("knowledge_tree_builder.phase.analyze.call_llm_json")
    def test_llm_returns_error(self, mock_llm: Any, default_config: AppConfig) -> None:
        mock_llm.return_value = {"error": "timeout"}
        result = analyze_article("文章内容", "测试文章", config=default_config)
        assert result["candidates"] == []

    @patch("knowledge_tree_builder.phase.analyze.call_llm_json")
    def test_empty_article_flag(self, mock_llm: Any, default_config: AppConfig) -> None:
        mock_llm.return_value = {
            "analysis": {"content_summary": "", "empty_article": True},
            "candidates": [],
        }
        result = analyze_article("", "空文章", config=default_config)
        assert result["analysis"]["empty_article"] is True
        assert result["candidates"] == []

    @patch("knowledge_tree_builder.phase.analyze.call_llm_json")
    def test_max_candidates_truncation(self, mock_llm: Any, default_config: AppConfig) -> None:
        default_config.max_candidates_per_article = 3
        mock_llm.return_value = {
            "candidates": [
                {"text": f"知识点 {i} 这是一条足够长的知识点文本", "type": "key_point", "claims_count": 1}
                for i in range(10)
            ],
        }
        result = analyze_article("内容", "标题", config=default_config)
        assert len(result["candidates"]) == 3

    @patch("knowledge_tree_builder.phase.analyze.call_llm_json")
    def test_invalid_type_discarded(self, mock_llm: Any, default_config: AppConfig) -> None:
        mock_llm.return_value = {
            "candidates": [
                {"text": "这是一条有效知识点文本内容足够长", "type": "invalid_type", "claims_count": 1},
                {"text": "这是另一条有效知识点文本", "type": "principle", "claims_count": 1},
            ],
        }
        result = analyze_article("内容", "标题", config=default_config)
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["type"] == "principle"

    @patch("knowledge_tree_builder.phase.analyze.call_llm_json")
    def test_missing_text_discarded(self, mock_llm: Any, default_config: AppConfig) -> None:
        mock_llm.return_value = {
            "candidates": [
                {"type": "principle", "claims_count": 1},
                {"text": "这条有文本所以会通过质量检查", "type": "key_point", "claims_count": 1},
            ],
        }
        result = analyze_article("内容", "标题", config=default_config)
        assert len(result["candidates"]) == 1

    @patch("knowledge_tree_builder.phase.analyze.call_llm_json")
    def test_missing_claims_count_defaults_to_1(self, mock_llm: Any, default_config: AppConfig) -> None:
        mock_llm.return_value = {
            "candidates": [
                {"text": "这是一条没有 claims_count 的知识点", "type": "key_point"},
            ],
        }
        result = analyze_article("内容", "标题", config=default_config)
        assert result["candidates"][0]["claims_count"] == 1

    @patch("knowledge_tree_builder.phase.analyze.call_llm_json")
    def test_article_text_truncation(self, mock_llm: Any, default_config: AppConfig) -> None:
        long_text = "x" * (_MAX_ARTICLE_CHARS + 5000)
        mock_llm.return_value = {"candidates": []}
        analyze_article(long_text, "超长文章", config=default_config)
        # 验证传给 LLM 的 prompt 被截断
        call_args = mock_llm.call_args
        assert len(call_args.kwargs.get("prompt", call_args.args[0] if call_args.args else "")) < len(long_text)

    @patch("knowledge_tree_builder.phase.analyze.call_llm_json")
    def test_config_params_passed_to_llm(self, mock_llm: Any, default_config: AppConfig) -> None:
        mock_llm.return_value = {"candidates": []}
        analyze_article("内容", "标题", config=default_config)
        call_kwargs = mock_llm.call_args.kwargs
        assert call_kwargs["api_url"] == default_config.llm_api_url
        assert call_kwargs["api_key"] == default_config.llm_api_key
        assert call_kwargs["model"] == default_config.llm_model


class TestValidateCandidate:
    """_validate_candidate 单条校验测试"""

    def test_valid_candidate_passes(self) -> None:
        raw = {"text": "这是一条有效的知识点文本内容", "type": "key_point", "claims_count": 1}
        result = _validate_candidate(raw)
        assert result is not None
        assert result["type"] == "key_point"

    def test_short_text_rejected(self) -> None:
        raw = {"text": "太短了", "type": "key_point", "claims_count": 1}
        assert _validate_candidate(raw) is None

    def test_empty_text_rejected(self) -> None:
        raw = {"text": "", "type": "key_point", "claims_count": 1}
        assert _validate_candidate(raw) is None

    def test_invalid_type_rejected(self) -> None:
        raw = {"text": "这是一条有效的知识点文本内容足够长", "type": "unknown", "claims_count": 1}
        assert _validate_candidate(raw) is None

    def test_non_dict_rejected(self) -> None:
        assert _validate_candidate("not a dict") is None
        assert _validate_candidate(None) is None


class TestParseAnalysisResponse:
    """_parse_analysis_response 容错测试"""

    def test_malformed_json_graceful(self) -> None:
        response = {"candidates": "not_a_list", "analysis": "not_a_dict"}
        result = _parse_analysis_response(response, 15, "标题")
        assert result["candidates"] == []
        assert result["analysis"]["empty_article"] is False

    def test_missing_analysis_key(self) -> None:
        response = {"candidates": []}
        result = _parse_analysis_response(response, 15, "标题")
        assert result["analysis"]["content_summary"] == ""

    def test_error_response(self) -> None:
        response = {"error": "parse_failed"}
        result = _parse_analysis_response(response, 15, "标题")
        assert result["candidates"] == []
