"""recall 模块测试 — 科目定位、注意力筛选、格式化注入。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from knowledge_tree_plugin.recall import (
    _extract_keywords,
    attention_filter,
    format_context_lines,
    locate_subject,
    log_use,
)


class TestExtractKeywords:
    """_extract_keywords 测试。"""

    def test_extract_english_terms(self) -> None:
        """提取英文标识符。"""
        result = _extract_keywords("HDBSCAN clustering with EOM method")
        assert "hdbscan" in result
        assert "clustering" in result
        assert "eom" in result

    def test_extract_chinese_bigrams(self) -> None:
        """提取中文二字组。"""
        result = _extract_keywords("半导体工程的基础理论分析")
        assert "半导" in result
        assert "基础" in result
        assert "理论" in result

    def test_filter_stop_chars(self) -> None:
        """停用字开头的二字组不提取。"""
        result = _extract_keywords("的了在是有")
        assert "的了" not in result

    def test_empty_text(self) -> None:
        """空文本返回空列表。"""
        assert _extract_keywords("") == []


class TestLocateSubject:
    """locate_subject 测试。"""

    def test_keyword_match_returns_deepest(self, mock_adapter: MagicMock) -> None:
        """关键词匹配返回最深科目。"""
        mock_adapter.search_subjects_by_keywords.return_value = [
            {"id": 1, "name": "半导体", "depth": 0},
            {"id": 2, "name": "基础理论", "depth": 1},
        ]
        mock_adapter.get_child_nodes.return_value = [{"id": 10, "name": "欧姆定律"}]

        result = locate_subject(
            query="基础理论",
            query_embedding=[0.1] * 1024,
            adapter=mock_adapter,
        )
        assert result is not None
        assert result["id"] == 2  # 最深节点
        assert result["child_count"] == 1

    def test_no_keyword_fallback_to_embedding(
        self, mock_adapter: MagicMock,
    ) -> None:
        """无关键词匹配时回退 embedding 余弦定位。"""
        mock_adapter.search_subjects_by_keywords.return_value = []
        mock_adapter.get_domain_nodes.return_value = [
            {"id": 1, "name": "半导体", "k_vector": [0.1] * 1024},
        ]
        mock_adapter.get_child_nodes.return_value = [{"id": 10, "name": "欧姆定律"}]

        result = locate_subject(
            query="test",
            query_embedding=[0.1] * 1024,  # 与领域节点余弦=1.0
            adapter=mock_adapter,
        )
        assert result is not None
        assert result["id"] == 1

    def test_no_domains_returns_none(self, mock_adapter: MagicMock) -> None:
        """无领域节点返回 None。"""
        mock_adapter.search_subjects_by_keywords.return_value = []
        mock_adapter.get_domain_nodes.return_value = []

        result = locate_subject(
            query="test",
            query_embedding=[0.1] * 1024,
            adapter=mock_adapter,
        )
        assert result is None


class TestAttentionFilter:
    """attention_filter 测试。"""

    def test_cold_start_uses_cosine(self) -> None:
        """冷启动期使用余弦相似度。"""
        child_nodes = [
            {"id": 1, "name": "A", "k_vector": [0.1] * 1024},
            {"id": 2, "name": "B", "k_vector": [0.9] * 1024},  # 与 Q 更相似
        ]
        query_emb = [0.9] * 1024

        result = attention_filter(
            query_emb, child_nodes, cold_start=True, min_score=0.0,
        )
        assert len(result) == 2
        # 节点 B（id=2) 应该分数更高
        assert result[0]["id"] == 2

    def test_attention_mode_normal(self) -> None:
        """正常注意力模式返回 top-N。"""
        child_nodes = [
            {"id": 1, "name": "A", "k_vector": [0.1] * 1024},
            {"id": 2, "name": "B", "k_vector": [0.9] * 1024},
        ]
        query_emb = [0.9] * 1024

        result = attention_filter(
            query_emb, child_nodes, cold_start=False, max_results=1,
        )
        assert len(result) == 1
        assert result[0]["id"] == 2

    def test_filter_below_min_score(self) -> None:
        """低于 min_score 的节点被过滤。"""
        child_nodes = [
            {"id": 1, "name": "A", "k_vector": [0.01] * 1024},  # 低分
            {"id": 2, "name": "B", "k_vector": [0.9] * 1024},
        ]
        query_emb = [0.9] * 1024

        result = attention_filter(
            query_emb, child_nodes, cold_start=False, min_score=0.3,
        )
        # 节点 A 的分数应该很低（softmax 后接近 0）
        assert len(result) >= 1


    def test_attention_mode_filters_near_zero_noise(self) -> None:
        """非冷启动不套 0.3 绝对阈值，但应过滤接近 0 的 softmax 噪音。"""
        child_nodes = [
            {"id": 1, "name": "A", "k_vector": [1.0, 0.0]},
            {"id": 2, "name": "B", "k_vector": [-100.0, 0.0]},
        ]
        result = attention_filter(
            [1.0, 0.0], child_nodes, cold_start=False, max_results=2,
        )
        assert [r["id"] for r in result] == [1]

    def test_empty_nodes_returns_empty(self) -> None:
        """空节点列表返回空。"""
        assert attention_filter([0.1] * 1024, []) == []

    def test_no_k_vector_returns_empty(self) -> None:
        """所有节点无 k_vector 时返回空。"""
        child_nodes = [
            {"id": 1, "name": "A", "k_vector": None},
        ]
        assert attention_filter([0.1] * 1024, child_nodes) == []


class TestFormatContextLines:
    """format_context_lines 测试。"""

    def test_basic_format(self) -> None:
        """基本格式化输出。"""
        kps = [
            {"id": 10, "name": "欧姆定律", "text": "V=IR", "score": 0.87},
        ]
        result = format_context_lines(kps)
        assert len(result) == 3  # <memory-context> + <memory> + </memory-context>
        assert result[0] == "<memory-context>"
        assert 'source="knowledge_tree"' in result[1]
        assert 'node_id="10"' in result[1]
        assert result[2] == "</memory-context>"

    def test_empty_returns_empty(self) -> None:
        """空列表返回空。"""
        assert format_context_lines([]) == []

    def test_multiple_points(self) -> None:
        """多个知识点生成多行。"""
        kps = [
            {"id": 10, "name": "A", "text": "a", "score": 0.9},
            {"id": 11, "name": "B", "text": "b", "score": 0.8},
        ]
        result = format_context_lines(kps)
        assert len(result) == 4


class TestLogUse:
    """log_use 测试。"""

    def test_log_use_normal(self, mock_adapter: MagicMock) -> None:
        """正常调用转发到 adapter。"""
        log_use(mock_adapter, "test_session", [10, 11], "欧姆定律")
        mock_adapter.log_use.assert_called_once_with(
            session_id="test_session",
            node_ids=[10, 11],
            query="欧姆定律",
        )

    def test_log_use_exception_handled(self, mock_adapter: MagicMock) -> None:
        """异常不抛出。"""
        mock_adapter.log_use.side_effect = ValueError("DB error")
        # 不应抛出异常
        log_use(mock_adapter, "test", [10], "q")
