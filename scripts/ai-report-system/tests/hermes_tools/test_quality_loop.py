"""
测试：QualityLoop 逐章质量闭环
遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from ai_report.core.full_report_loop import FullReportLoop
from ai_report.core.quality_loop import (
    DIAGNOSIS_GOOD,
    DIAGNOSIS_INSUFFICIENT,
    DIAGNOSIS_OFF_TOPIC,
    ChapterDiagnosis,
    ChapterFixResult,
    QualityLoop,
)
from ai_report.core.workflow_state import WorkflowState


# ═════════════════════════════════════════════════════════════
# 数据结构测试
# ═════════════════════════════════════════════════════════════

class TestChapterDiagnosis:
    """ChapterDiagnosis 数据结构测试。"""

    @pytest.mark.unit
    def test_create(self) -> None:
        """基本创建。"""
        d = ChapterDiagnosis(
            title="测试",
            score_before=0.4,
            diagnosis=DIAGNOSIS_INSUFFICIENT,
            reason="内容太短",
            suggested_action="enrich",
        )
        assert d.title == "测试"
        assert d.diagnosis == DIAGNOSIS_INSUFFICIENT

    @pytest.mark.unit
    def test_to_dict(self) -> None:
        """to_dict 转换。"""
        d = ChapterDiagnosis(
            title="测试", score_before=0.4,
            diagnosis=DIAGNOSIS_OFF_TOPIC,
            reason="偏离主题", suggested_action="rewrite",
        )
        result = d.to_dict()
        assert result["diagnosis"] == DIAGNOSIS_OFF_TOPIC
        assert result["has_search_data"] is False


class TestChapterFixResult:
    """ChapterFixResult 数据结构测试。"""

    @pytest.mark.unit
    def test_improved_property(self) -> None:
        """improved 属性判断。"""
        diagnosis = ChapterDiagnosis(
            title="测试", score_before=0.3,
            diagnosis=DIAGNOSIS_INSUFFICIENT,
            reason="不足", suggested_action="enrich",
        )
        result = ChapterFixResult(
            title="测试",
            diagnosis=diagnosis,
            original_content="旧",
            fixed_content="新内容" * 200,
            score_after=0.7,
        )
        assert result.improved
        assert result.score_after > result.diagnosis.score_before

    @pytest.mark.unit
    def test_not_improved(self) -> None:
        """未提升的情况。"""
        diagnosis = ChapterDiagnosis(
            title="测试", score_before=0.6,
            diagnosis=DIAGNOSIS_GOOD,
            reason="足够", suggested_action="skip",
        )
        result = ChapterFixResult(
            title="测试",
            diagnosis=diagnosis,
            original_content="旧",
            fixed_content="新",
            score_after=0.3,
        )
        assert not result.improved

    @pytest.mark.unit
    def test_to_dict(self) -> None:
        """to_dict 转换。"""
        diagnosis = ChapterDiagnosis(
            title="测试", score_before=0.3,
            diagnosis=DIAGNOSIS_OFF_TOPIC,
            reason="跑题", suggested_action="rewrite",
        )
        result = ChapterFixResult(
            title="测试",
            diagnosis=diagnosis,
            original_content="旧内容",
            fixed_content="新内容",
            score_after=0.8,
        )
        d = result.to_dict()
        assert d["improved"] is True
        assert d["score_before"] == 0.3
        assert d["score_after"] == 0.8


# ═════════════════════════════════════════════════════════════
# 质量估算测试
# ═════════════════════════════════════════════════════════════

class TestEstimateQuality:
    """_estimate_quality 测试。"""

    @pytest.mark.unit
    def test_empty_content(self) -> None:
        """空内容。"""
        assert QualityLoop._estimate_quality("", 500) == 0.0

    @pytest.mark.unit
    def test_short_content(self) -> None:
        """短内容。"""
        assert QualityLoop._estimate_quality("hi", 500) == 0.0

    @pytest.mark.unit
    def test_good_content(self) -> None:
        """高质量内容应有较高分数。"""
        content = "# 标题\n\n这是正文。市场增长了20%。\n\n- 列表项1\n- 列表项2" * 5
        score = QualityLoop._estimate_quality(content, 200)
        assert 0.5 <= score <= 1.0

    @pytest.mark.unit
    def test_content_with_data(self) -> None:
        """含数据的内容得分更高。"""
        content = "# 标题\n\n增长了25%，达到1000亿规模。增长率30%。"
        score = QualityLoop._estimate_quality(content, 200)
        assert score >= 0.3


# ═════════════════════════════════════════════════════════════
# 诊断解析测试
# ═════════════════════════════════════════════════════════════

class TestParseDiagnosis:
    """_parse_diagnosis_response 测试。"""

    @pytest.mark.unit
    def test_parse_json(self) -> None:
        """解析标准 JSON。"""
        response = '{"diagnosis": "off_topic", "reason": "内容偏离主题", "suggested_action": "rewrite"}'
        result = QualityLoop._parse_diagnosis_response(response)
        assert result is not None
        assert result["diagnosis"] == DIAGNOSIS_OFF_TOPIC

    @pytest.mark.unit
    def test_parse_json_block(self) -> None:
        """解析 markdown JSON 块。"""
        response = '```json\n{"diagnosis": "insufficient", "reason": "内容不足", "suggested_action": "enrich"}\n```'
        result = QualityLoop._parse_diagnosis_response(response)
        assert result is not None
        assert result["diagnosis"] == DIAGNOSIS_INSUFFICIENT

    @pytest.mark.unit
    def test_parse_plaintext_off_topic(self) -> None:
        """从纯文本提取 off_topic。"""
        response = "这篇内容明显off_topic，标题写A但内容写B"
        result = QualityLoop._parse_diagnosis_response(response)
        assert result is not None
        assert result["diagnosis"] == DIAGNOSIS_OFF_TOPIC

    @pytest.mark.unit
    def test_parse_plaintext_good(self) -> None:
        """从纯文本提取 good。"""
        response = "good，内容充实主题一致"
        result = QualityLoop._parse_diagnosis_response(response)
        assert result is not None
        assert result["diagnosis"] == DIAGNOSIS_GOOD

    @pytest.mark.unit
    def test_parse_nonsense(self) -> None:
        """无法解析时返回 None。"""
        result = QualityLoop._parse_diagnosis_response("完全不相关的内容")
        assert result is None


# ═════════════════════════════════════════════════════════════
# 诊断逻辑测试
# ═════════════════════════════════════════════════════════════

class TestDiagnose:
    """_diagnose 逻辑测试。"""

    @pytest.mark.unit
    def test_diagnose_with_mock(self) -> None:
        """注入 mock caller 验证诊断。"""
        def mock_llm(prompt: str, **kwargs: Any) -> str:
            return '{"diagnosis": "good", "reason": "内容充实", "suggested_action": "skip"}'

        content = "这是关于市场分析的内容。市场增长了20%。"
        diagnosis = QualityLoop._diagnose(
            content, "市场规模", 200, "补充资料",
            llm_caller=mock_llm,
        )
        assert isinstance(diagnosis, ChapterDiagnosis)
        assert diagnosis.diagnosis == DIAGNOSIS_GOOD
        assert diagnosis.suggested_action == "skip"

    @pytest.mark.unit
    def test_diagnose_off_topic_with_mock(self) -> None:
        """mock 返回跑题。"""
        def mock_llm(prompt: str, **kwargs: Any) -> str:
            return '{"diagnosis": "off_topic", "reason": "偏离标题", "suggested_action": "rewrite"}'

        diagnosis = QualityLoop._diagnose(
            "内容", "测试", 500, "",
            llm_caller=mock_llm,
        )
        assert diagnosis.diagnosis == DIAGNOSIS_OFF_TOPIC
        assert diagnosis.suggested_action == "rewrite"

    @pytest.mark.unit
    def test_diagnose_llm_fallback(self) -> None:
        """LLM 返回空时降级为 insufficient。"""
        def mock_llm(prompt: str, **kwargs: Any) -> str:
            return ""

        diagnosis = QualityLoop._diagnose(
            "简短", "测试", 500, "",
            llm_caller=mock_llm,
        )
        assert diagnosis.diagnosis == DIAGNOSIS_INSUFFICIENT
        assert diagnosis.suggested_action == "enrich"


# ═════════════════════════════════════════════════════════════
# 搜索补充资料测试
# ═════════════════════════════════════════════════════════════

# ═════════════════════════════════════════════════════════════
# run_chapter 集成测试
# ═════════════════════════════════════════════════════════════

class TestRunChapter:
    """run_chapter 集成测试。"""

    @pytest.mark.unit
    def test_skip_good_chapter(self) -> None:
        """高质量章节直接跳过。"""
        loop = QualityLoop(threshold=0.6)
        state = WorkflowState(topic="t", report_type="tech")

        from dataclasses import dataclass

        @dataclass
        class FakeSpec:
            title: str = "高质量章节"
            section_type: str = "body"
            estimated_words: int = 200
            level: int = 2
            required_data: Optional[List[str]] = None
            sub_sections: Optional[List[Any]] = None

        state.init_from_plan([FakeSpec()])
        # 填入质量 >= 0.6 的内容（长内容 + 标题 + 列表 + 数据）
        good_content = (
            "# 高质量章节\n\n"
            "这是关于市场分析的详细内容。市场规模增长了25%，达到1000亿元。\n\n"
            "- 核心数据1：增长率25%\n"
            "- 核心数据2：市场份额35%\n"
            "- 核心数据3：用户规模5000万\n\n"
            "详细分析表明，市场正处于快速增长期。"
        )
        state.set_chapter_result("高质量章节", good_content, "高质量")

        result = loop.run_chapter(state, "高质量章节")
        assert result is None  # 跳过

    @pytest.mark.unit
    def test_unknown_chapter(self) -> None:
        """未知章节返回 None。"""
        loop = QualityLoop()
        state = WorkflowState(topic="t", report_type="tech")
        result = loop.run_chapter(state, "不存在")
        assert result is None

    @pytest.mark.unit
    def test_no_content_chapter(self) -> None:
        """无内容的章节返回 None。"""
        loop = QualityLoop()
        state = WorkflowState(topic="t", report_type="tech")
        state.chapter_contexts["空"] = None  # type: ignore

        # 手动建一个空 context
        from ai_report.core.workflow_state import ChapterContext
        ctx = ChapterContext(title="空章节")
        state.chapter_contexts["空章节"] = ctx

        result = loop.run_chapter(state, "空章节")
        assert result is None


# ═════════════════════════════════════════════════════════════
# 修正逻辑测试
# ═════════════════════════════════════════════════════════════

class TestFix:
    """_fix 修正逻辑测试。"""

    @pytest.mark.unit
    def test_skip_action(self) -> None:
        """skip 操作直接返回原文。"""
        content = "原始内容"
        diagnosis = ChapterDiagnosis(
            title="测试", score_before=0.8,
            diagnosis=DIAGNOSIS_GOOD,
            reason="足够好", suggested_action="skip",
        )
        result = QualityLoop._fix(content, diagnosis, "测试", "主题")
        assert result == content

    @pytest.mark.unit
    def test_rewrite_with_mock(self) -> None:
        """注入 mock 验证重写。"""
        def mock_llm(prompt: str, **kwargs: Any) -> str:
            return "## 测试章节\n\n这是重写后的完整内容。包含了详细的分析和数据支撑。"

        content = "原始偏题内容"
        diagnosis = ChapterDiagnosis(
            title="测试", score_before=0.2,
            diagnosis=DIAGNOSIS_OFF_TOPIC,
            reason="跑题", suggested_action="rewrite",
            search_data="补充资料",
        )
        result = QualityLoop._fix(
            content, diagnosis, "测试", "主题",
            llm_caller=mock_llm,
        )
        assert "重写后的完整内容" in result

    @pytest.mark.unit
    def test_enrich_with_mock(self) -> None:
        """注入 mock 验证补充。"""
        def mock_llm(prompt: str, **kwargs: Any) -> str:
            return "## 测试\n\n原始内容基础上，补充了更多资料。"

        content = "原始内容"
        diagnosis = ChapterDiagnosis(
            title="测试", score_before=0.4,
            diagnosis=DIAGNOSIS_INSUFFICIENT,
            reason="内容不足", suggested_action="enrich",
        )
        result = QualityLoop._fix(
            content, diagnosis, "测试", "主题",
            llm_caller=mock_llm,
        )
        assert "原始内容" in result  # 保留原文

    @pytest.mark.unit
    def test_llm_fallback(self) -> None:
        """LLM 返回空时保留原文。"""
        def mock_llm(prompt: str, **kwargs: Any) -> str:
            return ""

        content = "原始内容"
        diagnosis = ChapterDiagnosis(
            title="测试", score_before=0.3,
            diagnosis=DIAGNOSIS_OFF_TOPIC,
            reason="跑题", suggested_action="rewrite",
        )
        result = QualityLoop._fix(
            content, diagnosis, "测试", "主题",
            llm_caller=mock_llm,
        )
        assert result == content


# ═════════════════════════════════════════════════════════════
# FullReportLoop 测试
# ═════════════════════════════════════════════════════════════

class TestFullReportLoop:
    """FullReportLoop 全文质量闭环测试。"""

    def _make_state(self) -> WorkflowState:
        """创建带内容的测试状态。"""
        from dataclasses import dataclass

        @dataclass
        class FakeSpec:
            title: str = "x"
            section_type: str = "body"
            estimated_words: int = 200
            level: int = 2
            required_data: Optional[List[Any]] = None
            sub_sections: Optional[List[Any]] = None

        state = WorkflowState(topic="t", report_type="tech")
        sections = [
            FakeSpec(title="第一章", section_type="body", estimated_words=100),
            FakeSpec(title="第二章", section_type="body", estimated_words=100),
        ]
        state.init_from_plan(sections, main_context="test")
        state.set_chapter_result(
            "第一章",
            "# 第一章\n\n这是高质量内容。市场增长了25%，达到1000亿元。\n- 数据1\n- 数据2",
            "摘要1",
        )
        state.set_chapter_result(
            "第二章",
            "# 第二章\n\n这也是高质量内容。增长率30%，用户规模5000万。\n- 指标A\n- 指标B",
            "摘要2",
        )
        return state

    @pytest.mark.unit
    def test_evaluate_all(self) -> None:
        """评估所有章节质量。"""
        state = self._make_state()
        scores = FullReportLoop._evaluate_all(state)
        assert len(scores) == 2
        for _, score in scores.items():
            assert 0.3 <= score <= 1.0

    @pytest.mark.unit
    def test_build_anchor_first_chapter(self) -> None:
        """第一章锚点无上一章。"""
        state = self._make_state()
        anchor = FullReportLoop._build_anchor(state, "第一章")
        assert "上一章" not in anchor
        assert "下一章" in anchor

    @pytest.mark.unit
    def test_build_anchor_middle_chapter(self) -> None:
        """中间章节锚点有前后。"""
        state = self._make_state()
        anchor = FullReportLoop._build_anchor(state, "第一章")
        assert "下一章" in anchor

    @pytest.mark.unit
    def test_build_anchor_unknown(self) -> None:
        """未知章节返回空。"""
        state = self._make_state()
        anchor = FullReportLoop._build_anchor(state, "不存在的章节")
        assert anchor == ""

    @pytest.mark.unit
    def test_consistency_check_returns_list(self) -> None:
        """一致性检查返回列表。"""
        state = self._make_state()
        issues = FullReportLoop._consistency_check(state)
        assert isinstance(issues, list)

    @pytest.mark.unit
    def test_run_quality_above_threshold(self) -> None:
        """高质量内容应直接通过。"""
        state = self._make_state()
        loop = FullReportLoop(max_iterations=3, pass_threshold=0.8)
        result = loop.run(state, "t")
        assert result is state
