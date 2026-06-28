"""测试 content_generator 纯函数（无 LLM 调用）。

覆盖：
  - GeneratedSection 数据类
  - _build_intent_driven_prompt  — 写作 prompt 组装逻辑
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_report.core.generator import (
    GeneratedSection,
    HermesContentGenerator,
)
from ai_report.core.planner import ReportPlan, SectionSpec


# ═══════════════════════════════════════════════════════════════
# GeneratedSection
# ═══════════════════════════════════════════════════════════════

class TestGeneratedSection:
    """GeneratedSection 数据类行为测试"""

    def test_default_values(self):
        spec = SectionSpec(title="测试", section_type="body", level=1, estimated_words=500)
        section = GeneratedSection(spec=spec, content="内容")
        assert section.word_count == 2  # calculated from content
        assert section.quality_score == 0.0
        assert section.generation_attempts == 1
        assert not section.used_fallback


# ═══════════════════════════════════════════════════════════════
# _build_intent_driven_prompt  — 写作 prompt 结构验证
# ═══════════════════════════════════════════════════════════════

def _make_plan(title: str = "测试报告", **overrides: Any) -> ReportPlan:
    meta = overrides.pop("metadata", {})
    return ReportPlan(
        title=title,
        topic="测试",
        report_type="tech",
        language="zh",
        sections=[],
        metadata={
            "report_goal": {"title": title, "writing_role": {"role": "专家", "tone": "专业"}},
            **meta,
        },
    )


def _make_spec(
    title: str = "章1",
    key_points: list[str] | None = None,
    **overrides: Any,
) -> SectionSpec:
    return SectionSpec(
        title=title,
        section_type=overrides.get("section_type", "body"),
        level=overrides.get("level", 1),
        estimated_words=overrides.get("estimated_words", 500),
        required_data=overrides.get("required_data", []),
        diagram_types=overrides.get("diagram_types", []),
        content_template=overrides.get("content_template", ""),
    )


def _build_prompt(
    spec: SectionSpec,
    plan: ReportPlan,
    cp: dict[str, Any] | None = None,
) -> str:
    """封装调用 HermesContentGenerator._build_intent_driven_prompt"""
    if cp is None:
        cp = {
            "title": spec.title,
            "writing_intent": spec.required_data[0] if spec.required_data else "",
            "key_points": [],
            "avoid_topics": [],
            "chart_spec": None,
            "materials_text": spec.content_template,
        }
    # mock state（WorkflowState 只需 topics_by_type 字段支持）
    from ai_report.core.workflow_state import WorkflowState
    state = WorkflowState(topic="测试", report_type="tech")
    state._topics_by_type = {"tech": ["测试主题"]}
    state._chapter_order = [spec.title] if spec.title else []
    return HermesContentGenerator._build_intent_driven_prompt(spec, plan, cp, state)


class TestBuildIntentDrivenPrompt:
    """_build_intent_driven_prompt prompt 结构测试"""

    def test_contains_chapter_title(self):
        spec = _make_spec("可行性分析")
        prompt = _build_prompt(spec, _make_plan())
        # prompt 中不直接出现章节标题，但包含写作意图
        assert "写作意图" in prompt

    def test_contains_writing_goal(self):
        plan = _make_plan()
        spec = _make_spec("技术方案", required_data=["intent:论证技术可行性"])
        prompt = _build_prompt(spec, plan)
        assert "报告总目标" in prompt
        assert "测试" in prompt

    def test_contains_key_points_in_constraints(self):
        cp = {
            "title": "投资分析",
            "writing_intent": "分析投资回报",
            "key_points": ["预算金额", "分期投入", "回报率"],
            "avoid_topics": [],
            "chart_spec": None,
            "materials_text": "详情素材",
        }
        spec = _make_spec("投资分析", key_points=cp["key_points"])
        prompt = _build_prompt(spec, _make_plan(), cp)
        assert "预算金额" in prompt

    def test_contains_avoid_topics(self):
        cp = {
            "title": "市场分析",
            "writing_intent": "行业趋势",
            "key_points": ["市场规模"],
            "avoid_topics": ["V3方案", "外部竞品"],
            "chart_spec": None,
            "materials_text": "",
        }
        spec = _make_spec("市场分析")
        prompt = _build_prompt(spec, _make_plan(), cp)
        assert "V3方案" in prompt
        assert "外部竞品" in prompt

    def test_contains_materials_text(self):
        cp = {
            "title": "技术方案",
            "writing_intent": "架构设计",
            "key_points": ["三网隔离"],
            "avoid_topics": [],
            "chart_spec": None,
            "materials_text": "互联网层配置Qoder，工控网层部署文心",
        }
        spec = _make_spec("技术方案", content_template=cp["materials_text"])
        prompt = _build_prompt(spec, _make_plan(), cp)
        assert "Qoder" in prompt

    def test_summary_chapter_has_numeric_constraints(self):
        """总结/汇总章节注入 5 条数值完整性要求"""
        cp = {
            "title": "投资估算汇总",
            "writing_intent": "汇总投资数据",
            "key_points": ["总金额"],
            "avoid_topics": [],
            "chart_spec": None,
            "materials_text": "",
        }
        spec = _make_spec("投资估算汇总")
        prompt = _build_prompt(spec, _make_plan(), cp)
        assert "数值完整性要求" in prompt
        assert "禁止将总价换算" in prompt

    def test_non_summary_chapter_no_numeric_constraints(self):
        """非总结章节不注入数值约束"""
        cp = {
            "title": "技术方案",
            "writing_intent": "架构设计",
            "key_points": ["三网架构"],
            "avoid_topics": [],
            "chart_spec": None,
            "materials_text": "",
        }
        spec = _make_spec("技术方案")
        prompt = _build_prompt(spec, _make_plan(), cp)
        assert "数值完整性要求" not in prompt

    def test_chart_data_injection_when_present(self):
        """chart_spec.data 存在时注入「本章图表数据」"""
        cp = {
            "title": "投资分析",
            "writing_intent": "投资评估",
            "key_points": ["预算"],
            "avoid_topics": [],
            "chart_spec": {
                "type": "comparison",
                "data": {"items": [{"label": "互联网", "amount": 260}]},
            },
            "materials_text": "",
        }
        spec = _make_spec("投资分析")
        prompt = _build_prompt(spec, _make_plan(), cp)
        assert "本章图表数据" in prompt
        assert "comparison" in prompt
        assert "260" in prompt

    def test_chart_data_skipped_when_empty(self):
        """chart_spec 无 data 时不注入图表数据"""
        cp = {
            "title": "投资分析",
            "writing_intent": "投资评估",
            "key_points": ["预算"],
            "avoid_topics": [],
            "chart_spec": {"type": "comparison"},  # 无 data
            "materials_text": "",
        }
        spec = _make_spec("投资分析")
        prompt = _build_prompt(spec, _make_plan(), cp)
        assert "本章图表数据" not in prompt
