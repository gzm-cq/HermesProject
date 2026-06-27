"""测试 workflow_orchestrator 纯函数（无 LLM / 无外部 I/O）。

覆盖：
  - _validate_report_goal    — report_goal 前置校验逻辑
  - ReportCleaner            — 报告内容清洗（** 去除 + 标题修复）
  - _flatten_sections        — 递归章节展平
  - _graph_to_plan           — StateGraph → ReportPlan 转换
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

import pytest

from ai_report.core.orchestrator import ReportWorkflowOrchestrator
from ai_report.core.planner import SectionSpec
from ai_report.core.report_cleaner import ReportCleaner


# ── 辅助类型：模拟 report 对象 ─────────────────────────────


@dataclass
class MockSection:
    content: str = ""


@dataclass
class MockReport:
    full_content: str = ""
    sections: list[MockSection] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# _validate_report_goal
# ═══════════════════════════════════════════════════════════════

class TestValidateReportGoal:
    """_validate_report_goal 前置校验逻辑测试"""

    def test_all_steps_done_passes(self):
        """Step 4+5 标记 done 且内容完备 → 只报告 fact_bank 缺失"""
        goal = {
            "_execution": {
                "step_4_curate_materials": {"done": True},
                "step_5_coverage_check": {"done": True},
            },
            "chapter_prompts": [
                {"title": "章1", "materials_text": "有内容的素材"},
            ],
        }
        issues = ReportWorkflowOrchestrator._validate_report_goal(goal, "测试主题")
        # fact_bank 不存在是已知问题（不影响 Step 4/5 本身校验）
        non_fb = [i for i in issues if "fact_bank" not in i.lower()]
        assert non_fb == []

    def test_step4_done_materials_empty(self):
        """Step 4 标 done 但 materials_text 为空 → 报错"""
        goal = {
            "_execution": {"step_4_curate_materials": {"done": True}},
            "chapter_prompts": [
                {"title": "章1", "materials_text": ""},
            ],
        }
        issues = ReportWorkflowOrchestrator._validate_report_goal(goal, "t")
        fb_issues = [i for i in issues if "fact_bank" not in i.lower()]
        assert any("materials_text" in i for i in fb_issues)

    def test_step5_not_done_warning(self):
        """Step 5 未标记 done → 发出建议"""
        goal = {
            "_execution": {
                "step_4_curate_materials": {"done": True},
            },
            "chapter_prompts": [
                {"title": "章1", "materials_text": "有内容"},
            ],
        }
        issues = ReportWorkflowOrchestrator._validate_report_goal(goal, "t")
        fb_issues = [i for i in issues if "fact_bank" not in i.lower()]
        assert any("Step 5" in i for i in fb_issues)

    def test_short_key_points_when_step4_not_done(self):
        """Step 4 未 done 且 key_points 过短 → 建议提示"""
        goal = {
            "_execution": {},
            "chapter_prompts": [
                {"title": "技术方案", "key_points": ["短"]},  # len < 15
            ],
        }
        issues = ReportWorkflowOrchestrator._validate_report_goal(goal, "t")
        fb_issues = [i for i in issues if "fact_bank" not in i.lower()]
        assert any("key_points" in i for i in fb_issues)


# ═══════════════════════════════════════════════════════════════
# ReportCleaner (原 _clean_report)
# ═══════════════════════════════════════════════════════════════

@dataclass
class _Section:
    content: str = ""

@dataclass
class _SimpleReport:
    full_content: str = ""
    sections: list[_Section] = field(default_factory=list)


class TestCleanReport:
    """ReportCleaner 内容清洗测试"""

    def setup_method(self):
        self._cleaner = ReportCleaner()

    def test_removes_bold_markers(self):
        """**text** → text"""
        report = _SimpleReport(full_content="这是**重要**内容")
        self._cleaner.clean_report(report)
        assert "**" not in report.full_content

    def test_removes_bold_prefix(self):
        """去除行首的 **结论：** 等前缀"""
        report = _SimpleReport(full_content="**结论：**这是总结")
        self._cleaner.clean_report(report)
        assert report.full_content == "这是总结"

    def test_preserves_heading_levels(self):
        """# 标题行不动"""
        report = _SimpleReport(full_content="# 第一章\n内容\n## 1.1 小节")
        self._cleaner.clean_report(report)
        assert report.full_content.startswith("# 第一章")

    def test_preserves_table_rows(self):
        """表格行中的 ** 保留"""
        content = "| **项目** | **金额** |\n| 互联网 | 100万 |"
        report = _SimpleReport(full_content=content)
        self._cleaner.clean_report(report)
        # 表格行中的 ** 应该被保留（行级表格格式）
        first_line = report.full_content.split("\n")[0]
        assert "**" in first_line

    def test_removes_separator_lines(self):
        """--- *** ___ 行直接删除"""
        report = _SimpleReport(full_content="内容\n---\n更多\n***\n结尾")
        self._cleaner.clean_report(report)
        assert "---" not in report.full_content

    def test_empty_report_does_not_crash(self):
        report = _SimpleReport(full_content="")
        self._cleaner.clean_report(report)  # 不应抛异常

    def test_none_report_does_not_crash(self):
        self._cleaner.clean_report(None)  # 不应抛异常

    def test_updates_section_content(self):
        """同步清理 sections 中的 **"""
        report = _SimpleReport(
            full_content="一些**加粗**内容",
            sections=[_Section(content="子章节**加粗**内容")],
        )
        self._cleaner.clean_report(report)
        assert "**" not in report.sections[0].content


# ═══════════════════════════════════════════════════════════════
# _flatten_sections
# ═══════════════════════════════════════════════════════════════

class TestFlattenSections:
    """_flatten_sections 递归展平测试"""

    def test_single_level(self):
        cp = {"title": "章1", "level": 1, "estimated_words": 500}
        sections: list[SectionSpec] = []
        ReportWorkflowOrchestrator._flatten_sections(cp, sections)
        assert len(sections) == 1
        assert sections[0].title == "章1"

    def test_with_sub_sections(self):
        cp = {
            "title": "章1",
            "level": 1,
            "sub_sections": [
                {"title": "节1.1", "level": 2},
                {"title": "节1.2", "level": 2},
            ],
        }
        sections: list[SectionSpec] = []
        ReportWorkflowOrchestrator._flatten_sections(cp, sections)
        assert len(sections) == 3  # 章1 + 节1.1 + 节1.2

    def test_nested_sub_sections(self):
        cp = {
            "title": "章1",
            "level": 1,
            "sub_sections": [
                {
                    "title": "节1.1",
                    "level": 2,
                    "sub_sections": [
                        {"title": "点1.1.1", "level": 3},
                    ],
                },
            ],
        }
        sections: list[SectionSpec] = []
        ReportWorkflowOrchestrator._flatten_sections(cp, sections)
        assert len(sections) == 3

    def test_chart_spec_mapping(self):
        """chart_spec.type → diagram_types 转换"""
        cp = {"title": "投资分析", "chart_spec": {"type": "comparison"}}
        sections: list[SectionSpec] = []
        ReportWorkflowOrchestrator._flatten_sections(cp, sections)
        assert "comparison" in sections[0].diagram_types

    def test_writing_intent_and_key_points_in_required_data(self):
        cp = {
            "title": "技术方案",
            "writing_intent": "论证技术可行性",
            "key_points": ["三网架构", "国产化"],
        }
        sections: list[SectionSpec] = []
        ReportWorkflowOrchestrator._flatten_sections(cp, sections)
        rd = sections[0].required_data
        assert any("intent:" in d for d in rd)
        assert any("kp:" in d for d in rd)


# ═══════════════════════════════════════════════════════════════
# _graph_to_plan
# ═══════════════════════════════════════════════════════════════

class TestGraphToPlan:
    """_graph_to_plan StateGraph → ReportPlan 转换测试"""

    def test_converts_chapter_prompts(self):
        prompts = [
            {"title": "章1", "level": 1, "estimated_words": 500},
            {"title": "章2", "level": 1, "estimated_words": 600},
        ]
        plan = ReportWorkflowOrchestrator._graph_to_plan(
            topic="测试",
            report_type="tech",
            language="zh",
            goal={"title": "测试报告"},
            chapter_prompts=prompts,
        )
        assert len(plan.sections) == 2
        assert plan.sections[0].title == "章1"

    def test_empty_prompts_raises(self):
        with pytest.raises(ValueError, match="未产出章节结构"):
            ReportWorkflowOrchestrator._graph_to_plan(
                topic="测试", report_type="tech", language="zh",
                goal={}, chapter_prompts=[],
            )

    def test_metadata_includes_goal_and_chapter_prompts(self):
        goal = {"title": "测试报告"}
        prompts = [{"title": "章1"}]
        plan = ReportWorkflowOrchestrator._graph_to_plan(
            topic="测试", report_type="tech", language="zh",
            goal=goal, chapter_prompts=prompts,
        )
        assert plan.metadata["report_goal"] == goal
        assert len(plan.metadata["chapter_prompts"]) == 1
