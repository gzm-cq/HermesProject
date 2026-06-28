"""metrics.py 单元测试 — 评估指标计算。"""

from unittest.mock import MagicMock

import pytest

from recall_eval.core.metrics import (
    _extract_keywords,
    _heuristic_coverage,
    _heuristic_faithfulness,
    _heuristic_relevance,
    _tokenize,
    coverage_score,
    faithfulness_score,
    relevance_score,
)


class TestTokenize:
    """_tokenize 分词测试。"""

    def test_english_words(self) -> None:
        tokens = _tokenize("hello world python")
        assert "hello" in tokens
        assert "world" in tokens
        assert "python" in tokens

    def test_chinese_words(self) -> None:
        tokens = _tokenize("你好 世界")
        assert "你好" in tokens or "世界" in tokens

    def test_mixed_language(self) -> None:
        tokens = _tokenize("LiteLLM 配置 问题")
        assert "litellm" in tokens

    def test_empty_string(self) -> None:
        tokens = _tokenize("")
        assert tokens == set()


class TestExtractKeywords:
    """_extract_keywords 关键词提取测试。"""

    def test_extracts_keywords(self) -> None:
        text = "LiteLLM 网关配置 数据库连接 端口设置"
        keywords = _extract_keywords(text, top_n=3)
        assert len(keywords) <= 3

    def test_empty_text(self) -> None:
        keywords = _extract_keywords("", top_n=5)
        assert keywords == []

    def test_top_n_limit(self) -> None:
        text = "a b c d e f g h i j k l m n o p"
        keywords = _extract_keywords(text, top_n=5)
        assert len(keywords) <= 5


class TestHeuristicFaithfulness:
    """启发式忠实度评估测试。"""

    def test_perfect_match(self) -> None:
        query = "LiteLLM 地址"
        context = "LiteLLM 网关地址是 http://127.0.0.1:4142"
        answer = "LiteLLM 网关地址是 http://127.0.0.1:4142"
        result = _heuristic_faithfulness(query, context, answer)
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0
        assert result["method"] == "heuristic"

    def test_empty_answer(self) -> None:
        result = _heuristic_faithfulness("query", "context", "")
        assert result["score"] == 0.0
        assert "答案为空" in result["reason"]

    def test_no_overlap(self) -> None:
        query = "python 编程"
        context = "java 是一种编程语言"
        answer = "python 是一种编程语言"
        result = _heuristic_faithfulness(query, context, answer)
        assert 0.0 <= result["score"] <= 1.0

    def test_returns_expected_keys(self) -> None:
        result = _heuristic_faithfulness("q", "c", "a")
        assert "score" in result
        assert "reason" in result
        assert "supported_claims" in result
        assert "unsupported_claims" in result


class TestHeuristicRelevance:
    """启发式相关性评估测试。"""

    def test_high_relevance(self) -> None:
        query = "LiteLLM 配置问题"
        context = "LiteLLM 网关配置包括地址、端口、API Key 等设置"
        result = _heuristic_relevance(query, context)
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0
        assert result["method"] == "heuristic"

    def test_empty_context(self) -> None:
        result = _heuristic_relevance("query", "")
        assert result["score"] == 0.0
        assert "上下文为空" in result["reason"]

    def test_returns_expected_keys(self) -> None:
        result = _heuristic_relevance("q", "c")
        assert "score" in result
        assert "reason" in result
        assert "relevant_topics" in result
        assert "irrelevant_topics" in result


class TestHeuristicCoverage:
    """启发式覆盖率评估测试。"""

    def test_high_coverage(self) -> None:
        query = "LiteLLM 配置地址端口"
        context = "LiteLLM 配置包括地址和端口设置"
        result = _heuristic_coverage(query, context)
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0
        assert result["method"] == "heuristic"

    def test_empty_context(self) -> None:
        result = _heuristic_coverage("query", "")
        assert result["score"] == 0.0
        assert "上下文为空" in result["reason"]

    def test_returns_expected_keys(self) -> None:
        result = _heuristic_coverage("q", "c")
        assert "score" in result
        assert "reason" in result
        assert "query_points" in result
        assert "covered_points" in result
        assert "missing_points" in result


class TestFaithfulnessScore:
    """faithfulness_score 函数测试。"""

    def test_heuristic_mode(self, sample_query: str, sample_context: str, sample_answer: str) -> None:
        result = faithfulness_score(sample_query, sample_context, sample_answer)
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0

    def test_llm_mode(self, sample_query: str, sample_context: str, sample_answer: str) -> None:
        mock_llm = MagicMock()
        mock_llm.evaluate_faithfulness.return_value = {"score": 0.9, "reason": "test"}
        result = faithfulness_score(sample_query, sample_context, sample_answer, mock_llm)
        assert result["score"] == 0.9
        mock_llm.evaluate_faithfulness.assert_called_once()


class TestRelevanceScore:
    """relevance_score 函数测试。"""

    def test_heuristic_mode(self, sample_query: str, sample_context: str) -> None:
        result = relevance_score(sample_query, sample_context)
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0

    def test_llm_mode(self, sample_query: str, sample_context: str) -> None:
        mock_llm = MagicMock()
        mock_llm.evaluate_relevance.return_value = {"score": 0.85, "reason": "test"}
        result = relevance_score(sample_query, sample_context, mock_llm)
        assert result["score"] == 0.85
        mock_llm.evaluate_relevance.assert_called_once()


class TestCoverageScore:
    """coverage_score 函数测试。"""

    def test_heuristic_mode(self, sample_query: str, sample_context: str) -> None:
        result = coverage_score(sample_query, sample_context)
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0

    def test_llm_mode(self, sample_query: str, sample_context: str) -> None:
        mock_llm = MagicMock()
        mock_llm.evaluate_coverage.return_value = {"score": 0.8, "reason": "test"}
        result = coverage_score(sample_query, sample_context, mock_llm)
        assert result["score"] == 0.8
        mock_llm.evaluate_coverage.assert_called_once()
