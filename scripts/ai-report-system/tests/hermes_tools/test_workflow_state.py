"""
测试：WorkflowState 串行写作上下文管理器
遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from ai_report.core.workflow_state import (
    SECTION_TYPE_BUSINESS,
    SECTION_TYPE_REGULAR,
    SECTION_TYPE_SUMMARY,
    ChapterContext,
    WorkflowState,
)


# ── 模拟 SectionSpec ────────────────────────────────────────

@dataclass
class FakeSectionSpec:
    """模拟 ReportPlan 章节。"""
    title: str
    section_type: str = "body"
    estimated_words: int = 500
    level: int = 2
    required_data: List[str] = None
    diagram_types: List[str] = None

    def __post_init__(self) -> None:
        if self.required_data is None:
            self.required_data = []
        if self.diagram_types is None:
            self.diagram_types = []


# ═════════════════════════════════════════════════════════════
# 初始化测试
# ═════════════════════════════════════════════════════════════

class TestInit:
    """WorkflowState 初始化测试。"""

    @pytest.mark.unit
    def test_empty_state(self) -> None:
        """空状态初始化。"""
        state = WorkflowState(topic="test", report_type="tech")
        assert state.topic == "test"
        assert state.report_type == "tech"
        assert len(state.chapter_contexts) == 0

    @pytest.mark.unit
    def test_init_from_plan(self) -> None:
        """从计划初始化。"""
        state = WorkflowState(topic="AI芯片", report_type="market")
        sections = [
            FakeSectionSpec(title="市场规模", section_type="body"),
            FakeSectionSpec(title="竞争格局", section_type="analysis"),
        ]
        state.init_from_plan(sections, main_context="分析2025年AI芯片市场")

        assert len(state.chapter_contexts) == 2
        assert state.main_context == "分析2025年AI芯片市场"
        assert state._chapter_order == ["市场规模", "竞争格局"]

    @pytest.mark.unit
    def test_init_from_plan_without_main_context(self) -> None:
        """无 main_context 时自动生成。"""
        state = WorkflowState(topic="GPU市场", report_type="market")
        sections = [FakeSectionSpec(title="概述")]
        state.init_from_plan(sections)

        assert "GPU市场" in state.main_context
        assert "market" in state.main_context

    @pytest.mark.unit
    def test_chapter_order_preserved(self) -> None:
        """章节顺序与传入顺序一致。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [
            FakeSectionSpec(title="C"),
            FakeSectionSpec(title="A"),
            FakeSectionSpec(title="B"),
        ]
        state.init_from_plan(sections)
        assert state._chapter_order == ["C", "A", "B"]


# ═════════════════════════════════════════════════════════════
# 章节类型检测测试
# ═════════════════════════════════════════════════════════════

class TestSectionTypeDetection:
    """_detect_section_type 测试。"""

    @pytest.mark.unit
    def test_regular_section(self) -> None:
        """普通章节。"""
        assert WorkflowState._detect_section_type("技术背景", "body") == SECTION_TYPE_REGULAR

    @pytest.mark.unit
    def test_business_by_title_keyword(self) -> None:
        """标题含业务关键词。"""
        assert WorkflowState._detect_section_type("成本效益分析", "body") == SECTION_TYPE_BUSINESS
        assert WorkflowState._detect_section_type("市场竞争分析", "body") == SECTION_TYPE_BUSINESS
        assert WorkflowState._detect_section_type("ROI分析", "body") == SECTION_TYPE_BUSINESS

    @pytest.mark.unit
    def test_business_by_type(self) -> None:
        """原始类型为 business。"""
        assert WorkflowState._detect_section_type("随便什么", "business") == SECTION_TYPE_BUSINESS

    @pytest.mark.unit
    def test_summary_section(self) -> None:
        """总结章节。"""
        assert WorkflowState._detect_section_type("总结与展望", "conclusion") == SECTION_TYPE_SUMMARY
        assert WorkflowState._detect_section_type("结论", "conclusion") == SECTION_TYPE_SUMMARY

    @pytest.mark.unit
    def test_analysis_is_business(self) -> None:
        """analysis 类型被视为 business。"""
        assert WorkflowState._detect_section_type("详细分析", "analysis") == SECTION_TYPE_BUSINESS


# ═════════════════════════════════════════════════════════════
# 搜索阶段测试
# ═════════════════════════════════════════════════════════════

class TestSearchPhase:
    """搜索阶段功能测试。"""

    @pytest.mark.unit
    def test_set_chapter_search(self) -> None:
        """设置搜索资料。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [FakeSectionSpec(title="市场规模")]
        state.init_from_plan(sections)
        state.set_chapter_search("市场规模", "2025年市场规模达1000亿")

        assert state.has_search_data("市场规模")
        assert not state.has_search_data("不存在")

    @pytest.mark.unit
    def test_search_data_stored(self) -> None:
        """搜索资料正确存储。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [FakeSectionSpec(title="趋势")]
        state.init_from_plan(sections)
        data = "增长趋势加速，年复合增长率20%"
        state.set_chapter_search("趋势", data)
        assert state.chapter_contexts["趋势"].search_data == data

    @pytest.mark.unit
    def test_search_unknown_chapter(self) -> None:
        """未知章节不崩溃。"""
        state = WorkflowState(topic="t", report_type="tech")
        state.set_chapter_search("不存在", "data")  # 不应抛异常


# ═════════════════════════════════════════════════════════════
# 写作阶段测试
# ═════════════════════════════════════════════════════════════

class TestWritingPhase:
    """写作阶段核心测试。"""

    @pytest.mark.unit
    def test_first_chapter_prompt_no_prev(self) -> None:
        """第一章 prompt 不含上章概要。"""
        state = WorkflowState(topic="AI芯片", report_type="market")
        sections = [FakeSectionSpec(title="概述")]
        state.init_from_plan(sections, main_context="分析AI芯片市场")
        state.set_chapter_search("概述", "芯片市场快速增长")

        prompt = state.get_chapter_prompt("概述")

        assert "分析AI芯片市场" in prompt  # 含整体目标
        assert "芯片市场快速增长" in prompt  # 含搜索资料
        assert "上一章" not in prompt       # 不含上章概要

    @pytest.mark.unit
    def test_second_chapter_prompt_has_prev(self) -> None:
        """第二章 prompt 包含第一章摘要。"""
        state = WorkflowState(topic="AI芯片", report_type="market")
        sections = [
            FakeSectionSpec(title="概述"),
            FakeSectionSpec(title="市场规模"),
        ]
        state.init_from_plan(sections, main_context="分析市场")

        # 写第一章
        state.set_chapter_result("概述", "# 概述\nAI芯片市场正在快速增长", summary="AI芯片市场概述完成")

        # 第二章 prompt
        prompt = state.get_chapter_prompt("市场规模")
        assert "AI芯片市场概述完成" in prompt  # 含上章摘要
        assert "分析市场" in prompt             # 含整体目标

    @pytest.mark.unit
    def test_second_chapter_prompt_no_prev_if_not_written(self) -> None:
        """如果第一章没写，第二章也不含上章摘要。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [
            FakeSectionSpec(title="A"),
            FakeSectionSpec(title="B"),
        ]
        state.init_from_plan(sections)
        # 不写第一章，直接取第二章
        prompt = state.get_chapter_prompt("B")
        assert "上一章" not in prompt

    @pytest.mark.unit
    def test_unknown_chapter_raises(self) -> None:
        """未知章节抛异常。"""
        state = WorkflowState(topic="t", report_type="tech")
        with pytest.raises(ValueError):
            state.get_chapter_prompt("不存在")

    @pytest.mark.unit
    def test_business_chapter_has_guidance(self) -> None:
        """业务章节 prompt 含业务视角指引。"""
        state = WorkflowState(topic="t", report_type="market")
        sections = [FakeSectionSpec(title="成本效益分析")]
        state.init_from_plan(sections)
        prompt = state.get_chapter_prompt("成本效益分析")
        assert "业务视角要求" in prompt
        assert "成本" in prompt


# ═════════════════════════════════════════════════════════════
# 结果存储测试
# ═════════════════════════════════════════════════════════════

class TestResultStorage:
    """结果存储和摘要提取测试。"""

    @pytest.mark.unit
    def test_set_chapter_result(self) -> None:
        """设置章节结果。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [FakeSectionSpec(title="概述")]
        state.init_from_plan(sections)

        state.set_chapter_result("概述", "# 概述\n这是第一章内容")
        assert state.chapter_contexts["概述"].generated_content == "# 概述\n这是第一章内容"

    @pytest.mark.unit
    def test_auto_extract_summary(self) -> None:
        """自动提取摘要。"""
        content = "# 概述\n\n这是第一章的正文内容。讨论了市场现状和趋势。\n\n第二段内容。"
        summary = WorkflowState._auto_extract_summary(content)
        assert "第一章的正文内容" in summary
        assert len(summary) <= 100

    @pytest.mark.unit
    def test_summary_empty_content(self) -> None:
        """空内容返回空摘要。"""
        assert WorkflowState._auto_extract_summary("") == ""

    @pytest.mark.unit
    def test_prev_summary_updates(self) -> None:
        """每写一章，上章摘要更新。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [
            FakeSectionSpec(title="第一章"),
            FakeSectionSpec(title="第二章"),
        ]
        state.init_from_plan(sections)

        state.set_chapter_result("第一章", "内容", summary="第一章摘要")
        state.set_chapter_result("第二章", "内容", summary="第二章摘要")

        # 写第二章后，_prev_summary 应为第二章的
        assert "第二章摘要" in state._prev_summary


# ═════════════════════════════════════════════════════════════
# 状态查询测试
# ═════════════════════════════════════════════════════════════

class TestStateQuery:
    """状态查询功能测试。"""

    @pytest.mark.unit
    def test_current_section(self) -> None:
        """当前章节检测。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [
            FakeSectionSpec(title="A"),
            FakeSectionSpec(title="B"),
        ]
        state.init_from_plan(sections)
        assert state.current_section == "A"

    @pytest.mark.unit
    def test_completed_sections(self) -> None:
        """已完成章节。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [
            FakeSectionSpec(title="A"),
            FakeSectionSpec(title="B"),
        ]
        state.init_from_plan(sections)
        state.set_chapter_result("A", "content")
        assert state.completed_sections == ["A"]
        assert state.pending_sections == ["B"]

    @pytest.mark.unit
    def test_is_complete(self) -> None:
        """全部完成判断。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [
            FakeSectionSpec(title="A"),
            FakeSectionSpec(title="B"),
        ]
        state.init_from_plan(sections)
        assert not state.is_complete
        state.set_chapter_result("A", "c")
        state.set_chapter_result("B", "c")
        assert state.is_complete

    @pytest.mark.unit
    def test_get_full_text(self) -> None:
        """拼接完整文字。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [
            FakeSectionSpec(title="A"),
            FakeSectionSpec(title="B"),
        ]
        state.init_from_plan(sections)
        state.set_chapter_result("A", "# A\n内容a")
        state.set_chapter_result("B", "# B\n内容b")
        text = state.get_full_text()
        assert "内容a" in text
        assert "内容b" in text
        assert text.index("内容a") < text.index("内容b")  # 顺序正确

    @pytest.mark.unit
    def test_to_dict(self) -> None:
        """字典序列化。"""
        state = WorkflowState(topic="t", report_type="tech")
        sections = [FakeSectionSpec(title="A")]
        state.init_from_plan(sections)
        d = state.to_dict()
        assert d["topic"] == "t"
        assert d["report_type"] == "tech"
        assert d["total_sections"] == 1
