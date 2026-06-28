"""
测试：ChartGenerator Markdown 图表生成+校验
遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from ai_report.adapters.chart_generator import ChartGenerator, ChartValidationResult
from ai_report.adapters.chart_advisor import ChartSpec


# ═════════════════════════════════════════════════════════════
# 校验测试
# ═════════════════════════════════════════════════════════════

class TestValidation:
    """_validate 规则校验测试。"""

    @pytest.mark.unit
    def test_table_passes(self) -> None:
        """标准表格应通过校验。"""
        spec = ChartSpec(chart_type="table", title="数据表", purpose="展示数据")
        chart = "## 图表: 数据表\n\n| 类别 | 数值 |\n|------|------|\n| A | 100 |\n| B | 200 |"
        result = ChartGenerator._validate(chart, spec)
        assert result.passed

    @pytest.mark.unit
    def test_table_missing_syntax(self) -> None:
        """表格缺少 --- 分隔线。"""
        spec = ChartSpec(chart_type="table", title="表", purpose="展示")
        chart = "## 图表: 表\n\n| A | B |\n| 1 | 2 |"
        result = ChartGenerator._validate(chart, spec)
        assert not result.passed
        assert any("---" in issue for issue in result.issues)

    @pytest.mark.unit
    def test_bar_with_ascii(self) -> None:
        """柱状图含 ASCII 条应通过。"""
        spec = ChartSpec(chart_type="bar", title="柱状图", purpose="对比")
        chart = "## 图表: 柱状图\n\nNVIDIA ██████████████████████ 80%\nAMD    ██████████ 40%"
        result = ChartGenerator._validate(chart, spec)
        assert result.passed

    @pytest.mark.unit
    def test_bar_missing_ascii(self) -> None:
        """柱状图缺 ASCII 条。"""
        spec = ChartSpec(chart_type="bar", title="柱状", purpose="对比")
        chart = "## 图表: 柱状\n数值：80%"
        result = ChartGenerator._validate(chart, spec)
        assert not result.passed
        assert any("柱状条" in issue for issue in result.issues)

    @pytest.mark.unit
    def test_missing_title(self) -> None:
        """缺少标题。"""
        spec = ChartSpec(chart_type="table", title="重要数据表", purpose="展示")
        chart = "| 列 | 值 |\n|---|-----|\n| A | 1 |"
        result = ChartGenerator._validate(chart, spec)
        assert not result.passed

    @pytest.mark.unit
    def test_missing_data_numbers(self) -> None:
        """缺少数据数值。"""
        spec = ChartSpec(chart_type="table", title="空表", purpose="展示")
        chart = "## 图表: 空表\n\n无数据"
        result = ChartGenerator._validate(chart, spec)
        assert not result.passed

    @pytest.mark.unit
    def test_html_blocked(self) -> None:
        """HTML 标签被标记。"""
        spec = ChartSpec(chart_type="table", title="测试", purpose="测试")
        chart = '<svg width="100"><text>test</text></svg>'
        result = ChartGenerator._validate(chart, spec)
        assert not result.passed
        assert any("SVG" in issue or "非 Markdown" in issue for issue in result.issues)

    @pytest.mark.unit
    def test_pie_requires_data(self) -> None:
        """饼图需要数据或 ASCII 块。"""
        spec = ChartSpec(chart_type="pie", title="占比", purpose="展示占比")
        chart = "## 图表: 占比\n只有文字说明"
        result = ChartGenerator._validate(chart, spec)
        # 无数据 → 不通过
        assert not result.passed


# ═════════════════════════════════════════════════════════════
# 生成测试
# ═════════════════════════════════════════════════════════════

class TestGenerate:
    """generate 方法测试（使用 mock llm_caller，不依赖真实 API）。"""

    @pytest.mark.unit
    def test_generate_table(self) -> None:
        """生成表格类型。"""
        gen = ChartGenerator(llm_caller=lambda p, **kw: (
            "## 图表: 数据对比\n\n"
            "| 类别 | 数值 |\n"
            "|------|------|\n"
            "| A | 100 |\n"
            "| B | 200 |"
        ))
        spec = ChartSpec(chart_type="table", title="数据对比", purpose="对比数据")
        result = gen.generate(spec)
        assert isinstance(result, ChartValidationResult)
        assert result.chart_markdown is not None
        assert "数据对比" in result.chart_markdown
        assert result.passed

    @pytest.mark.unit
    def test_generate_multiple(self) -> None:
        """批量生成。"""
        gen = ChartGenerator(llm_caller=lambda p, **kw: (
            "## 图表: 表1\n\n"
            "| 指标 | 数值 |\n"
            "|------|------|\n"
            "| X | 50 |\n"
            "| Y | 100 |"
        ))
        specs = [
            ChartSpec(chart_type="table", title="表1", purpose="p1"),
            ChartSpec(chart_type="bar", title="柱状1", purpose="对比"),
        ]
        results = gen.generate_all(specs)
        assert len(results) == 2
        for r in results:
            assert isinstance(r, ChartValidationResult)

    @pytest.mark.unit
    def test_to_dict(self) -> None:
        """ChartValidationResult to_dict。"""
        spec = ChartSpec(chart_type="table", title="t", purpose="p")
        result = ChartValidationResult(
            spec=spec,
            chart_markdown="## 图表: t\n\ndata",
            passed=True,
            issues=[],
        )
        d = result.to_dict()
        assert d["chart_type"] == "table"
        assert d["passed"]
