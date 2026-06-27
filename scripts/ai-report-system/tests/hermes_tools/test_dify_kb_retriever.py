"""
测试：Dify KB Retriever 数据结构和格式化逻辑
遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from ai_report.adapters.dify_kb import (
    DifyKBRetriever,
    KBSegment,
    KBResult,
)


class TestKBSegment:
    """KBSegment 数据结构测试。"""

    @pytest.mark.unit
    def test_create_minimal(self) -> None:
        """最简创建。"""
        seg = KBSegment(content="test content", score=0.85)
        assert seg.content == "test content"
        assert seg.score == 0.85
        assert seg.document_name == ""
        assert seg.document_id == ""

    @pytest.mark.unit
    def test_create_full(self) -> None:
        """完整创建。"""
        seg = KBSegment(
            content="deep content",
            score=0.92,
            document_name="doc1",
            document_id="id-123",
        )
        assert seg.document_name == "doc1"
        assert seg.document_id == "id-123"

    @pytest.mark.unit
    def test_to_formatted_with_name(self) -> None:
        """带文档名的格式化输出。"""
        seg = KBSegment(content="该技术具有较高应用价值", score=0.85, document_name="技术报告")
        formatted = seg.to_formatted()
        assert "[技术报告]" in formatted
        assert "(0.85)" in formatted
        assert "该技术具有较高应用价值" in formatted

    @pytest.mark.unit
    def test_to_formatted_without_name(self) -> None:
        """无文档名的格式化输出。"""
        seg = KBSegment(content="plain content", score=0.5)
        formatted = seg.to_formatted()
        assert "(0.50)" in formatted
        assert "plain content" in formatted

    @pytest.mark.unit
    def test_to_formatted_zero_score(self) -> None:
        """得分为0时省略分数显示。"""
        seg = KBSegment(content="no score data", score=0.0)
        formatted = seg.to_formatted()
        assert "(0.00)" not in formatted
        assert "no score data" in formatted

    @pytest.mark.unit
    def test_to_formatted_truncates_long_content(self) -> None:
        """长内容截断到500字。"""
        content = "x" * 1000
        seg = KBSegment(content=content, score=0.9)
        formatted = seg.to_formatted()
        assert len(formatted) <= 510  # prefix (~15) + 500 chars
        assert formatted.endswith("x" * 485)  # "x"*485 vs exact depends on prefix length


class TestKBResult:
    """KBResult 数据结构测试。"""

    @pytest.mark.unit
    def test_empty_result(self) -> None:
        """空结果。"""
        result = KBResult(query="test")
        assert result.success is False
        assert len(result.segments) == 0
        assert result.error is None

    @pytest.mark.unit
    def test_success_with_segments(self) -> None:
        """有片段时成功。"""
        result = KBResult(query="test")
        result.segments.append(KBSegment(content="c1", score=0.9))
        assert result.success is True

    @pytest.mark.unit
    def test_success_with_error(self) -> None:
        """有错误时不成功。"""
        result = KBResult(query="test", error="unavailable")
        result.segments.append(KBSegment(content="c1", score=0.9))
        assert result.success is False

    @pytest.mark.unit
    def test_format_text_empty(self) -> None:
        """空结果返回空字符串。"""
        result = KBResult(query="test")
        assert result.format_text() == ""

    @pytest.mark.unit
    def test_format_text_with_error(self) -> None:
        """有错误时返回空字符串。"""
        result = KBResult(query="test", error="unavailable")
        result.segments.append(KBSegment(content="c1", score=0.9))
        assert result.format_text() == ""

    @pytest.mark.unit
    def test_format_text_with_segments(self) -> None:
        """有片段时格式化输出。"""
        result = KBResult(query="test")
        result.segments.append(KBSegment(content="data1", score=0.9, document_name="doc1"))
        formatted = result.format_text()
        assert "【知识库参考】" in formatted
        assert "data1" in formatted
        assert "[doc1]" in formatted

    @pytest.mark.unit
    def test_format_text_respects_max_chars(self) -> None:
        """格式化文本不超过max_chars。"""
        result = KBResult(query="test")
        result.segments.append(KBSegment(content="a" * 800, score=0.9, document_name="doc1"))
        result.segments.append(KBSegment(content="b" * 800, score=0.8, document_name="doc2"))
        formatted = result.format_text(max_chars=500)
        assert len(formatted) <= 500

    @pytest.mark.unit
    def test_format_text_multiple_segments(self) -> None:
        """多个片段按顺序排列。"""
        result = KBResult(query="test")
        result.segments.append(KBSegment(content="first", score=0.9))
        result.segments.append(KBSegment(content="second", score=0.8))
        formatted = result.format_text()
        assert "first" in formatted
        assert "second" in formatted


class TestRetrieveForChapter:
    """retrieve_for_chapter 的查询构造测试。"""

    @pytest.mark.unit
    def test_retriever_init_attrs(self) -> None:
        """初始化属性正确。"""
        retriever = DifyKBRetriever()
        assert hasattr(retriever, "_dataset_id")
        assert hasattr(retriever, "_api_key")
        assert hasattr(retriever, "_compose")
        assert retriever._availability_checked is False

    @pytest.mark.unit
    def test_retrieve_for_chapter_returns_string(self) -> None:
        """retrieve_for_chapter 总是返回字符串。"""
        retriever = DifyKBRetriever()
        # 测试"无可用"路径 — 不调真实 Docker
        result = retriever.retrieve_for_chapter(
            chapter_title="市场规模",
            topic="AI 2025",
            report_type="market",
        )
        assert isinstance(result, str)

    @pytest.mark.unit
    def test_retrieve_query_construction(self) -> None:
        """验证查询字符串构建逻辑。"""
        retriever = DifyKBRetriever()
        # 测试正常路径的查询组合 — 用 retrieve 方法但不调真实 docker
        result = retriever.retrieve("AI 2025 市场规模 market")
        assert result.query == "AI 2025 市场规模 market"

    @pytest.mark.unit
    def test_retrieve_query_no_report_type(self) -> None:
        """无 report_type 时查询不变。"""
        retriever = DifyKBRetriever()
        # 手动构建查询字符串（retrieve_for_chapter 内部逻辑）
        parts = ["AI 2025", "市场规模"]
        query = " ".join(parts)
        result = retriever.retrieve(query)
        assert result.query == "AI 2025 市场规模"


class TestKBSegmentEdgeCases:
    """KBSegment 边界情况测试。"""

    @pytest.mark.unit
    def test_empty_content(self) -> None:
        """空内容。"""
        seg = KBSegment(content="", score=0.0)
        formatted = seg.to_formatted()
        # 空内容在 retrieve 中会被过滤，但数据结构本身应能处理
        assert formatted.startswith("-")
        assert len(formatted) >= 2

    @pytest.mark.unit
    def test_high_score(self) -> None:
        """高分。"""
        seg = KBSegment(content="perfect match", score=1.0)
        assert "(1.00)" in seg.to_formatted()

    @pytest.mark.unit
    def test_no_document_name_edge(self) -> None:
        """无文档名仅显示分数。"""
        seg = KBSegment(content="data", score=0.75)
        formatted = seg.to_formatted()
        assert "(0.75)" in formatted
        assert "data" in formatted
