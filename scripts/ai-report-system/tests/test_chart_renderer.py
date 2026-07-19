"""测试 chart_renderer 模块。

注意：渲染依赖 matplotlib，若环境未安装会跳过相关测试。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

matplotlib = pytest.importorskip("matplotlib")  # 跳过整个模块若不可用

from ai_report.export.chart_renderer import (
    _data_hash,
    _find_zh_font,
    _reset_dedup,
    render_all_charts,
    render_chart,
)


class TestDataHash:
    def test_same_data_same_hash(self):
        d1 = {"layers": [{"name": "a"}]}
        d2 = {"layers": [{"name": "a"}]}
        assert _data_hash(d1) == _data_hash(d2)

    def test_different_data_different_hash(self):
        d1 = {"layers": [{"name": "a"}]}
        d2 = {"layers": [{"name": "b"}]}
        assert _data_hash(d1) != _data_hash(d2)

    def test_key_order_independent(self):
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 2, "a": 1}
        assert _data_hash(d1) == _data_hash(d2)


class TestRenderChart:
    def setup_method(self):
        _reset_dedup()

    def test_none_spec_returns_none(self, tmp_path):
        assert render_chart(None, tmp_path, 1, "标题") is None

    def test_empty_data_returns_none(self, tmp_path):
        spec = {"type": "comparison", "data": {}}
        assert render_chart(spec, tmp_path, 1, "标题") is None

    def test_unknown_type_returns_none(self, tmp_path):
        spec = {"type": "unknown_type", "data": {"items": [{"name": "a", "value": 1}, {"name": "b", "value": 2}]}}
        assert render_chart(spec, tmp_path, 1, "标题") is None

    def test_insufficient_data_skipped(self, tmp_path):
        """comparison 类型要求数据点 >= 2。"""
        spec = {"type": "comparison", "data": {"items": [{"name": "a", "value": 1}]}}
        assert render_chart(spec, tmp_path, 1, "标题") is None

    def test_comparison_renders_png(self, tmp_path):
        spec = {
            "type": "comparison",
            "data": {"items": [
                {"name": "A", "value": 100},
                {"name": "B", "value": 200},
            ]},
        }
        path = render_chart(spec, tmp_path, 1, "测试对比")
        assert path is not None
        assert path.exists()
        assert path.suffix == ".png"

    def test_timeline_renders_png(self, tmp_path):
        spec = {
            "type": "timeline",
            "data": {"phases": [
                {"year": "2025", "label": "P1", "value": 1},
                {"year": "2026", "label": "P2", "value": 2},
            ]},
        }
        path = render_chart(spec, tmp_path, 2, "时间线")
        assert path is not None
        assert path.exists()

    def test_architecture_renders_png(self, tmp_path):
        spec = {
            "type": "architecture_diagram",
            "data": {"layers": [
                {"name": "应用层", "定位": "业务", "职能": "提供服务", "策略": "微服务"},
                {"name": "平台层", "定位": "中台", "职能": "AI能力", "策略": "组件化"},
            ]},
        }
        path = render_chart(spec, tmp_path, 3, "架构图")
        assert path is not None
        assert path.exists()

    def test_dedup_same_content_same_chapter(self, tmp_path):
        """同一章节同内容只渲染一次。"""
        spec = {
            "type": "comparison",
            "data": {"items": [{"name": "A", "value": 1}, {"name": "B", "value": 2}]},
        }
        path1 = render_chart(spec, tmp_path, 1, "标题")
        path2 = render_chart(spec, tmp_path, 1, "标题")
        assert path1 is not None
        assert path2 is None  # 第二次被去重

    def test_dedup_different_chapter_same_data_still_rendered(self, tmp_path):
        """不同章节同内容仍会渲染（因为 hash 包含 chapter_index）。"""
        spec = {
            "type": "comparison",
            "data": {"items": [{"name": "A", "value": 1}, {"name": "B", "value": 2}]},
        }
        path1 = render_chart(spec, tmp_path, 1, "章节1")
        path2 = render_chart(spec, tmp_path, 2, "章节2")
        assert path1 is not None
        assert path2 is not None
        assert path1 != path2


class TestRenderAllCharts:
    def test_empty_input(self, tmp_path):
        result = render_all_charts([], tmp_path)
        assert result == []

    def test_multiple_charts(self, tmp_path):
        chapter_prompts = [
            {
                "title": "对比",
                "chart_spec": {
                    "type": "comparison",
                    "data": {"items": [{"name": "A", "value": 1}, {"name": "B", "value": 2}]},
                },
            },
            {
                "title": "时间线",
                "chart_spec": {
                    "type": "timeline",
                    "data": {"phases": [
                        {"year": "2025", "label": "P1", "value": 1},
                        {"year": "2026", "label": "P2", "value": 2},
                    ]},
                },
            },
            {
                "title": "无图表",
                "chart_spec": None,
            },
        ]
        result = render_all_charts(chapter_prompts, tmp_path)
        assert len(result) == 2  # 第三个 spec=None 跳过
        for idx, path in result:
            assert path.exists()


class TestFindZhFont:
    def test_returns_str_or_none(self):
        """_find_zh_font 应返回 str 或 None（取决于系统字体是否安装）。"""
        result = _find_zh_font()
        assert result is None or isinstance(result, str)
