"""
测试：report_planner 样本搜索功能
遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from ai_report.core.planner import HermesReportPlanner, ReportPlan, SectionSpec


# ═════════════════════════════════════════════════════════════
# Plan 创建测试（含 samples 参数）
# ═════════════════════════════════════════════════════════════

class TestCreatePlanWithSamples:
    """create_plan 的 samples 参数测试。"""

    @pytest.mark.unit
    def test_create_without_samples(self) -> None:
        """无 samples 时行为不变。"""
        config = _make_config()
        planner = HermesReportPlanner(config)
        plan = planner.create_plan("AI芯片市场分析", report_type="market")
        assert isinstance(plan, ReportPlan)
        assert plan.report_type == "market"
        assert len(plan.sections) > 0

    @pytest.mark.unit
    def test_create_with_samples(self) -> None:
        """有 samples 时生成正常。"""
        config = _make_config()
        planner = HermesReportPlanner(config)
        samples = [
            "Market analysis report: Executive Summary, Market Overview, ...",
            "Competitive analysis: Market size, Key players, Market share...",
        ]
        plan = planner.create_plan(
            "GPU市场分析",
            report_type="market",
            samples=samples,
        )
        assert isinstance(plan, ReportPlan)
        assert len(plan.sections) > 0

    @pytest.mark.unit
    def test_samples_added_to_content_template(self) -> None:
        """样本信息应出现在第一个章节的 content_template 中。"""
        config = _make_config()
        planner = HermesReportPlanner(config)
        samples = ["Sample: market overview structure"]
        plan = planner.create_plan(
            "测试主题",
            report_type="tech",
            samples=samples,
        )
        first = plan.sections[0]
        assert first.content_template is not None
        assert "参考样本" in first.content_template

    @pytest.mark.unit
    def test_samples_empty_list_fallback(self) -> None:
        """空样本列表应回退到默认模板。"""
        config = _make_config()
        planner = HermesReportPlanner(config)
        plan = planner.create_plan(
            "测试",
            report_type="tech",
            samples=[],
        )
        # 空列表不应导致 _build_sections_with_samples 被调用
        # 实际走的是 _build_template_sections
        assert len(plan.sections) > 0


# ═════════════════════════════════════════════════════════════
# search_samples 测试
# ═════════════════════════════════════════════════════════════

class TestSearchSamples:
    """search_samples 方法测试。"""

    @pytest.mark.unit
    def test_search_samples_returns_list(self) -> None:
        """应返回列表（可能为空）。"""
        config = _make_config()
        planner = HermesReportPlanner(config)
        result = planner.search_samples("AI市场", "market")
        assert isinstance(result, list)

    @pytest.mark.unit
    def test_search_samples_different_topics(self) -> None:
        """不同主题的搜索。"""
        config = _make_config()
        planner = HermesReportPlanner(config)
        result = planner.search_samples("Python开发", "tech")
        assert isinstance(result, list)


# ═════════════════════════════════════════════════════════════
# 向后兼容测试
# ═════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """向后兼容性测试。"""

    @pytest.mark.unit
    def test_original_signature_works(self) -> None:
        """原始参数签名仍可用。"""
        config = _make_config()
        planner = HermesReportPlanner(config)
        plan = planner.create_plan("测试", "tech", "zh")
        assert len(plan.sections) > 0

    @pytest.mark.unit
    def test_custom_structure_still_works(self) -> None:
        """custom_structure 仍可正常使用。"""
        config = _make_config()
        planner = HermesReportPlanner(config)
        custom = [
            {"title": "自定章节1", "section_type": "intro", "estimated_words": 200},
            {"title": "自定章节2", "section_type": "body", "estimated_words": 500},
        ]
        plan = planner.create_plan("测试", "tech", custom_structure=custom)
        assert len(plan.sections) == 2
        assert plan.sections[0].title == "自定章节1"

    @pytest.mark.unit
    def test_detect_type(self) -> None:
        """detect_type 仍正常工作。"""
        config = _make_config()
        planner = HermesReportPlanner(config)
        assert planner.detect_type("市场分析报告") == "market"

    @pytest.mark.unit
    def test_plan_preview(self) -> None:
        """preview 方法仍正常工作。"""
        config = _make_config()
        planner = HermesReportPlanner(config)
        plan = planner.create_plan("测试", "tech")
        preview = plan.preview()
        assert isinstance(preview, str)
        assert len(preview) > 10


# ═════════════════════════════════════════════════════════════
# 辅助函数
# ═════════════════════════════════════════════════════════════

def _make_config() -> Any:
    """创建模拟配置对象。"""
    from ai_report.config import get_config
    return get_config()
