"""
测试：delegator, chart_advisor, web_searcher, business_writer
遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from ai_report.adapters.web_search import (
    MODE_HERMES,
    MODE_SKIP,
    MODE_TAVILY,
    DelegationTask,
    Delegator,
    SkillPrompts,
    TavilySearcher,
)
from ai_report.adapters.chart_advisor import ChartAdvisor, ChartRules
from ai_report.adapters.web_search import HermesWebSearcher, SearchResultItem
from ai_report.adapters.business_writer import (
    BusinessWriter,
    BusinessWritingTask,
)


# ═══════════════════════════════════════════════════════════
# Delegator 测试
# ═══════════════════════════════════════════════════════════

class TestDelegationTask:
    """DelegationTask 数据结构测试。"""

    @pytest.mark.unit
    def test_create_minimal(self) -> None:
        """最简创建。"""
        task = DelegationTask(skill="web-research", goal="test query")
        assert task.skill == "web-research"
        assert task.goal == "test query"
        assert task.mode == MODE_HERMES
        assert task.max_searches == 5

    @pytest.mark.unit
    def test_create_full(self) -> None:
        """完整创建。"""
        task = DelegationTask(
            skill="data-analysis",
            goal="analyze data",
            context="some context",
            max_searches=3,
            mode=MODE_TAVILY,
            expected_output="chart specs",
        )
        assert task.skill == "data-analysis"
        assert task.max_searches == 3
        assert task.mode == MODE_TAVILY


class TestSkillPrompts:
    """Skill prompt 构建器测试。"""

    @pytest.mark.unit
    def test_web_research_prompt(self) -> None:
        """web-research prompt 格式。"""
        task = DelegationTask(
            skill="web-research",
            goal="AI市场调研",
            context="中国市场",
            max_searches=3,
        )
        prompt = SkillPrompts.build("web-research", task)
        assert "AI市场调研" in prompt
        assert "中国市场" in prompt
        assert "web_search" in prompt
        assert "3 searches" in prompt

    @pytest.mark.unit
    def test_data_analysis_prompt(self) -> None:
        """data-analysis prompt 格式。"""
        task = DelegationTask(
            skill="data-analysis",
            goal="推荐图表类型",
            context="市场数据",
        )
        prompt = SkillPrompts.build("data-analysis", task)
        assert "推荐图表类型" in prompt
        assert "metric contract" in prompt
        assert "visuals" in prompt

    @pytest.mark.unit
    def test_copywriting_prompt(self) -> None:
        """copywriting prompt 格式。"""
        task = DelegationTask(
            skill="copywriting",
            goal="写业务价值段落",
            context="ROI 150%",
        )
        prompt = SkillPrompts.build("copywriting", task)
        assert "写业务价值段落" in prompt
        assert "Clarity over cleverness" in prompt
        assert "Benefits over features" in prompt

    @pytest.mark.unit
    def test_unknown_skill(self) -> None:
        """未知 skill 应抛异常。"""
        task = DelegationTask(skill="nonexistent", goal="test")
        with pytest.raises(ValueError, match="Unsupported skill"):
            SkillPrompts.build("nonexistent", task)


class TestDelegator:
    """Delegator 执行器测试。"""

    @pytest.mark.unit
    def test_prepare_returns_prompt(self) -> None:
        """prepare 应返回格式化的 prompt。"""
        delegator = Delegator()
        task = DelegationTask(skill="web-research", goal="test")
        prompt = delegator.prepare(task)
        assert isinstance(prompt, str)
        assert len(prompt) > 20

    @pytest.mark.unit
    def test_execute_skip_mode(self) -> None:
        """skip 模式应返回空结果。"""
        delegator = Delegator()
        task = DelegationTask(skill="web-research", goal="test", mode=MODE_SKIP)
        result = delegator.execute_direct(task)
        assert result.success
        assert result.output == ""

    @pytest.mark.unit
    def test_execute_unsupported_mode(self) -> None:
        """不支持的 direct 模式应返回错误。"""
        delegator = Delegator()
        task = DelegationTask(skill="web-research", goal="test", mode=MODE_HERMES)
        result = delegator.execute_direct(task)
        assert not result.success
        assert "not supported" in (result.error or "").lower()

    @pytest.mark.unit
    def test_tavily_available(self) -> None:
        """tavily 可用性检查（环境配置了 key 则可用）。"""
        delegator = Delegator()
        # 不抛异常即可
        _ = delegator.tavily_available


class TestTavilySearcher:
    """Tavily 搜索器测试。"""

    @pytest.mark.unit
    def test_available(self) -> None:
        """可用性检查（不抛异常即可）。"""
        searcher = TavilySearcher()
        _ = searcher.available

    @pytest.mark.unit
    def test_search_handles_missing_key(self) -> None:
        """无 key 或搜索失败时应优雅处理。"""
        searcher = TavilySearcher()
        if searcher.available:
            # 有真正 key，验证搜索能返回结果
            data = searcher.search("pytest testing framework", max_results=2)
            assert "results" in data
            assert len(data["results"]) > 0
        else:
            with pytest.raises(RuntimeError):
                searcher.search("test")


# ═══════════════════════════════════════════════════════════
# ChartAdvisor 测试
# ═══════════════════════════════════════════════════════════

class TestChartRules:
    """图表选型规则测试。"""

    @pytest.mark.unit
    def test_analysis_type_trend(self) -> None:
        """趋势分析应推荐 line/bar 图。"""
        charts = ChartRules.recommend_for_analysis_type("trend")
        types = [c["type"] for c in charts]
        assert "line" in types
        assert "bar" in types

    @pytest.mark.unit
    def test_analysis_type_comparison(self) -> None:
        """对比分析应推荐 bar/radar 图。"""
        charts = ChartRules.recommend_for_analysis_type("comparison")
        types = [c["type"] for c in charts]
        assert "bar" in types

    @pytest.mark.unit
    def test_analysis_type_unknown_defaults_to_table(self) -> None:
        """未知类型默认回退到 table。"""
        charts = ChartRules.recommend_for_analysis_type("unknown_type")
        assert all(c["type"] == "table" for c in charts)

    @pytest.mark.unit
    def test_recommend_for_section_tech_trend(self) -> None:
        """技术报告的趋势章节。"""
        charts = ChartRules.recommend_for_section("tech", "trend", "技术发展历程")
        assert len(charts) > 0

    @pytest.mark.unit
    def test_recommend_for_section_market_comparison(self) -> None:
        """市场报告的竞争对比章节。"""
        charts = ChartRules.recommend_for_section("market", "comparison", "市场竞争格局")
        assert len(charts) > 0


class TestChartAdvisor:
    """图表顾问集成测试。"""

    @pytest.mark.unit
    def test_advise_returns_advice(self) -> None:
        """advise 应返回 ChartAdvice。"""
        advisor = ChartAdvisor(theme="dark")
        sections = [
            {"title": "市场规模", "type": "trend"},
            {"title": "竞争格局", "type": "comparison"},
        ]
        advice = advisor.advise("market", sections)
        assert advice.report_type == "market"
        assert len(advice.recommended_charts) > 0
        assert "dark" in str(advice.style_guide.get("theme", ""))

    @pytest.mark.unit
    def test_advise_sorts_by_priority(self) -> None:
        """推荐图表应按优先级排序。"""
        advisor = ChartAdvisor()
        sections = [
            {"title": "趋势", "type": "trend"},
            {"title": "对比", "type": "comparison"},
            {"title": "分布", "type": "distribution"},
        ]
        advice = advisor.advise("tech", sections)
        priorities = [c.priority for c in advice.recommended_charts]
        assert priorities == sorted(priorities)

    @pytest.mark.unit
    def test_advise_light_theme(self) -> None:
        """浅色主题配置。"""
        advisor = ChartAdvisor(theme="light")
        advice = advisor.advise("tech", [{"title": "测试", "type": "trend"}])
        assert advice.style_guide["theme"] == "light"

    @pytest.mark.unit
    def test_advise_custom_data(self) -> None:
        """自定义数据不应影响基本功能。"""
        advisor = ChartAdvisor()
        advice = advisor.advise(
            "research",
            [{"title": "文献统计", "type": "distribution"}],
            custom_data={"source": "arxiv"},
        )
        assert len(advice.recommended_charts) > 0
        assert len(advice.notes) > 0

    @pytest.mark.unit
    def test_to_dict(self) -> None:
        """ChartAdvice 的 to_dict 转换。"""
        advisor = ChartAdvisor()
        advice = advisor.advise("tech", [{"title": "测试", "type": "trend"}])
        d = advice.to_dict()
        assert "recommended_charts" in d
        assert "style_guide" in d
        assert "notes" in d


# ═══════════════════════════════════════════════════════════
# WebSearcher 测试
# ═══════════════════════════════════════════════════════════

class TestSearchResultItem:
    """搜索结果项测试。"""

    @pytest.mark.unit
    def test_create_with_defaults(self) -> None:
        """最简创建应自动设置时间戳。"""
        item = SearchResultItem(title="Test", content="Content")
        assert item.title == "Test"
        assert item.content == "Content"
        assert item.timestamp > 0

    @pytest.mark.unit
    def test_invalid_relevance(self) -> None:
        """无效 relevance 应抛异常。"""
        with pytest.raises(ValueError):
            SearchResultItem(title="T", content="C", relevance=1.5)

    @pytest.mark.unit
    def test_to_dict(self) -> None:
        """to_dict 转换。"""
        item = SearchResultItem(
            title="T", content="C", url="https://example.com", source="web",
        )
        d = item.to_dict()
        assert d["title"] == "T"
        assert d["url"] == "https://example.com"


class TestHermesWebSearcher:
    """委托式搜索器测试。"""

    @pytest.mark.unit
    def test_prepare_returns_task(self) -> None:
        """prepare 应返回 DelegationTask。"""
        searcher = HermesWebSearcher()
        task = searcher.prepare("AI芯片市场")
        assert isinstance(task, DelegationTask)
        assert task.skill == "web-research"
        assert task.goal == "AI芯片市场"
        assert task.mode == MODE_HERMES

    @pytest.mark.unit
    def test_prepare_with_context(self) -> None:
        """prepare 带上下文。"""
        searcher = HermesWebSearcher()
        task = searcher.prepare("测试", max_results=3, context="2025年数据")
        assert task.max_searches == 3
        assert task.context == "2025年数据"

    @pytest.mark.unit
    def test_search_default_returns_task(self) -> None:
        """默认 search 应返回 DelegationTask。"""
        searcher = HermesWebSearcher()
        result = searcher.search("test", force_tavily=False)
        assert isinstance(result, DelegationTask)

    @pytest.mark.unit
    def test_search_tavily_handles_errors(self) -> None:
        """Tavily 搜索错误处理。"""
        searcher = HermesWebSearcher()
        if not searcher._delegator.tavily_available:
            result = searcher.search_tavily("test")
            assert result.error is not None
        else:
            # 有真正 key，验证搜索能返回结果
            result = searcher.search_tavily("python programming", max_results=2)
            assert result.success or result.error is not None


# ═══════════════════════════════════════════════════════════
# BusinessWriter 测试
# ═══════════════════════════════════════════════════════════

class TestBusinessWriter:
    """业务文案撰写器测试。"""

    @pytest.mark.unit
    def test_prepare_returns_task(self) -> None:
        """prepare 应返回 DelegationTask。"""
        writer = BusinessWriter()
        task_in = BusinessWritingTask(
            scenario="cost_reduction",
            section_title="成本效益分析",
            project_context="AI报告系统",
            key_findings=["降低运营成本30%"],
        )
        task_out = writer.prepare(task_in)
        assert isinstance(task_out, DelegationTask)
        assert task_out.skill == "copywriting"
        assert "成本效益分析" in task_out.goal

    @pytest.mark.unit
    def test_generate_returns_result(self) -> None:
        """generate 应返回 BusinessWritingResult。"""
        writer = BusinessWriter()
        task = BusinessWritingTask(
            scenario="revenue_growth",
            section_title="收入增长分析",
            project_context="新项目上线",
            key_findings=["预期收入增长50%", "ROI达200%"],
        )
        result = writer.generate(task)
        assert isinstance(result.content, str)
        assert result.section_title == "收入增长分析"
        assert result.scenario == "revenue_growth"
        assert len(result.content) > 50

    @pytest.mark.unit
    def test_generate_all_scenarios(self) -> None:
        """所有场景类型均应能生成内容。"""
        writer = BusinessWriter()
        scenarios = [
            "cost_reduction", "revenue_growth", "efficiency",
            "risk_management", "competitive_advantage", "customer_experience",
            "innovation", "scalability",
        ]
        for scenario in scenarios:
            task = BusinessWritingTask(
                scenario=scenario,
                section_title=f"Test {scenario}",
                project_context="测试项目",
                key_findings=[f"效果提升{i*10}%" for i in range(1, 4)],
            )
            result = writer.generate(task)
            assert len(result.content) > 30, f"Failed for scenario: {scenario}"

    @pytest.mark.unit
    def test_generate_without_findings(self) -> None:
        """无关键数据时仍能生成。"""
        writer = BusinessWriter()
        task = BusinessWritingTask(
            scenario="innovation",
            section_title="创新分析",
            project_context="新技术探索",
        )
        result = writer.generate(task)
        assert len(result.content) > 20


# ═══════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════

class TestModuleIntegration:
    """模块间集成测试。"""

    @pytest.mark.unit
    def test_delegator_and_web_searcher(self) -> None:
        """web_searcher 通过 delegator 构建 task。"""
        searcher = HermesWebSearcher()
        task = searcher.prepare("集成测试")
        prompt = searcher._delegator.prepare(task)
        assert isinstance(prompt, str)
        assert len(prompt) > 30

    @pytest.mark.unit
    def test_chart_advisor_to_dict(self) -> None:
        """ChartAdvice to_dict 包含完整信息。"""
        advisor = ChartAdvisor()
        advice = advisor.advise(
            "market",
            [{"title": "市场规模", "type": "trend"}],
        )
        d = advice.to_dict()
        assert d["report_type"] == "market"
        assert len(d["recommended_charts"]) > 0

    @pytest.mark.unit
    def test_search_result_serialization(self) -> None:
        """SearchResultItem 序列化和反序列化。"""
        original = SearchResultItem(
            title="Test Title",
            content="Test Content",
            url="https://example.com",
        )
        data = original.to_dict()
        restored = SearchResultItem(**data)
        assert restored.title == original.title
        assert restored.url == original.url
