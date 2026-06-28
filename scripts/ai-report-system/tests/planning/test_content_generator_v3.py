"""
测试：content_generator 搜索并行+写作串行
遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from ai_report.core.generator import (
    HermesContentGenerator,
    GeneratedReport,
    GeneratedSection,
)
from ai_report.core.workflow_state import WorkflowState


# ── 模拟数据 ────────────────────────────────────────────────

@dataclass
class FakeSectionSpec:
    """模拟 SectionSpec。"""
    title: str
    section_type: str = "body"
    estimated_words: int = 200
    level: int = 2
    required_data: Optional[List[str]] = None
    diagram_types: Optional[List[str]] = None

    def __post_init__(self) -> None:
        if self.required_data is None:
            self.required_data = []
        if self.diagram_types is None:
            self.diagram_types = []


@dataclass
class FakePlan:
    """模拟 ReportPlan。"""
    title: str = "测试报告"
    topic: str = "AI市场调研"
    report_type: str = "market"
    language: str = "zh"
    sections: List[Any] = None
    resource_needs: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.sections is None:
            self.sections = [
                FakeSectionSpec(title="市场概况", section_type="trend", estimated_words=300),
                FakeSectionSpec(title="竞争格局", section_type="analysis", estimated_words=400),
            ]
        if self.resource_needs is None:
            self.resource_needs = {}


# ═════════════════════════════════════════════════════════════
# 初始化测试
# ═════════════════════════════════════════════════════════════

class TestInit:
    """初始化测试。"""

    @pytest.mark.unit
    def test_version(self) -> None:
        """版本号正确。"""
        assert HermesContentGenerator.COMPONENT_VERSION == "3.0.0"

    @pytest.mark.unit
    def test_description_updated(self) -> None:
        # 描述已更新
        assert 'DAG并行化' in HermesContentGenerator.COMPONENT_DESCRIPTION
        assert '并行章节' in HermesContentGenerator.COMPONENT_DESCRIPTION


# ═════════════════════════════════════════════════════════════
# WorkflowState 集成测试
# ═════════════════════════════════════════════════════════════

class TestWorkflowIntegration:
    """与 WorkflowState 的集成测试。"""

    @pytest.mark.unit
    def test_build_main_context(self) -> None:
        """构建整体目标。"""
        plan = FakePlan()
        context = HermesContentGenerator._build_main_context(plan)
        assert "AI市场调研" in context
        assert "市场分析报告" in context

    @pytest.mark.unit
    def test_build_main_context_tech(self) -> None:
        """技术报告的整体目标。"""
        plan = FakePlan(report_type="tech", topic="微服务架构")
        context = HermesContentGenerator._build_main_context(plan)
        assert "技术报告" in context

    @pytest.mark.unit
    def test_add_business_context(self) -> None:
        """业务章节注入。"""
        prompt = "普通内容"
        result = HermesContentGenerator._add_business_context(prompt, "成本分析")
        assert "业务写作要求" in result
        assert "copywriting" in result

    @pytest.mark.unit
    def test_state_init_from_plan_flow(self) -> None:
        """模拟完整 state 流程。"""
        plan = FakePlan()
        config = _make_config()
        generator = HermesContentGenerator(config)
        state = WorkflowState(topic=plan.topic, report_type=plan.report_type)
        main_context = generator._build_main_context(plan)
        state.init_from_plan(plan.sections, main_context=main_context)

        assert state.topic == "AI市场调研"
        assert len(state.chapter_contexts) == 2
        assert state.main_context == main_context


# ═════════════════════════════════════════════════════════════
# 质量检查测试
# ═════════════════════════════════════════════════════════════

class TestQualityCheck:
    """质量检查功能测试。"""

    @pytest.mark.unit
    def test_empty_content(self) -> None:
        """空内容返回 0。"""
        score = HermesContentGenerator._check_content_quality("", FakeSectionSpec(title="x"), "t")
        assert score == 0.0

    @pytest.mark.unit
    def test_short_content(self) -> None:
        """短内容返回 0。"""
        score = HermesContentGenerator._check_content_quality("hi", FakeSectionSpec(title="x"), "t")
        assert score == 0.0

    @pytest.mark.unit
    def test_quality_basic_content(self) -> None:
        """基本内容应有合理分数。"""
        content = "# 标题\n\n这是正文内容。市场增长了20%。\n\n- 列表项1\n- 列表项2"
        score = HermesContentGenerator._check_content_quality(
            content, FakeSectionSpec(title="x", estimated_words=100), "test",
        )
        assert 0.3 <= score <= 1.0

    @pytest.mark.unit
    def test_template_penalty(self) -> None:
        """套话应被扣分。"""
        content = "是当前领域内的重要课题，随着技术的发展和业务的推进"
        score = HermesContentGenerator._check_content_quality(
            content, FakeSectionSpec(title="x", estimated_words=300), "test",
        )
        assert score < 0.8  # 套话扣分


# ═════════════════════════════════════════════════════════════
# 降级测试
# ═════════════════════════════════════════════════════════════

class TestFallback:
    """降级内容测试。"""

    @pytest.mark.unit
    def test_fallback_contains_section_title(self) -> None:
        """降级内容包含章节标题。"""
        spec = FakeSectionSpec(title="市场概况")
        content = HermesContentGenerator._generate_fallback_content(spec, "AI市场")
        assert "市场概况" in content
        assert "AI市场" in content


# ═════════════════════════════════════════════════════════════
# 组装测试
# ═════════════════════════════════════════════════════════════

class TestAssemble:
    """报告组装测试。"""

    @pytest.mark.unit
    def test_assemble_report(self) -> None:
        """组装报告包含完整内容。"""
        plan = FakePlan()
        spec = FakeSectionSpec(title="测试章节")
        sections = [
            GeneratedSection(spec=spec, content="# 测试章节\n内容正文"),
        ]
        state = WorkflowState(topic=plan.topic, report_type=plan.report_type)
        state.init_from_plan(plan.sections)
        state.set_chapter_result("市场概况", "# 市场概况\n内容", "摘要")

        report = HermesContentGenerator._assemble_report(
            plan, state, sections, start_time=0,
        )
        assert isinstance(report, GeneratedReport)
        assert report.total_words > 0
        assert "测试报告" in report.full_content


# ═════════════════════════════════════════════════════════════
# 辅助函数
# ═════════════════════════════════════════════════════════════

def _make_config() -> Any:
    """创建模拟配置对象。"""
    from ai_report.config import get_config
    return get_config()
