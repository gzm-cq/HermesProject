"""legend.py 单元测试 — 自动图例生成"""

import pytest

from drawio_generator.legend import build_legend


# ===== fixture =====

@pytest.fixture
def palette():
    """模拟 palettes.py 中的配色方案结构"""
    return {
        "node_blue":   {"fill": "#dae8fc", "stroke": "#6c8ebf"},
        "node_green":  {"fill": "#d5e8d4", "stroke": "#82b366"},
        "node_orange": {"fill": "#ffe6cc", "stroke": "#d79b00"},
        "node_yellow": {"fill": "#fff2cc", "stroke": "#d6b656"},
        "node_purple": {"fill": "#e1d5e7", "stroke": "#9673a6"},
        "node_red":    {"fill": "#f8cecc", "stroke": "#b85450"},
        "layer_bg": "#F4F6F8",
        "layer_stroke": "#D5DCE4",
        "text_color": "#333333",
    }


@pytest.fixture
def plan():
    """模拟 plan 结构"""
    return {"width": 1000, "height": 800}


# ===== 颜色少于 3 个 =====

class TestFewerThanThreeColors:
    """颜色少于 3 个时不画图例"""

    def test_two_colors_returns_empty(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
        ]
        result = build_legend(plan, nodes, [], palette)
        assert result["layers"] == []
        assert result["nodes"] == []
        assert result["width"] == 0
        assert result["height"] == 0

    def test_one_color_returns_empty(self, plan, palette):
        nodes = [{"id": "a", "color": "node_blue"}]
        result = build_legend(plan, nodes, [], palette)
        assert result["layers"] == []
        assert result["nodes"] == []
        assert result["width"] == 0
        assert result["height"] == 0

    def test_zero_colors_returns_empty(self, plan, palette):
        result = build_legend(plan, [], [], palette)
        assert result["layers"] == []
        assert result["nodes"] == []
        assert result["width"] == 0
        assert result["height"] == 0

    def test_duplicate_colors_count_once(self, plan, palette):
        # 多个节点用同一颜色，去重后不足 3 个
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_blue"},
            {"id": "c", "color": "node_green"},
        ]
        result = build_legend(plan, nodes, [], palette)
        assert result["layers"] == []
        assert result["nodes"] == []


# ===== 颜色 >= 3 个 =====

class TestThreeOrMoreColors:
    """颜色 >= 3 个时生成图例"""

    def test_three_colors_generates_legend(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
            {"id": "c", "color": "node_orange"},
        ]
        result = build_legend(plan, nodes, [], palette)
        assert len(result["layers"]) >= 1
        assert len(result["nodes"]) >= 1
        # 应有 1 个背景层
        assert len(result["layers"]) == 1
        # 应有标题 + 3 个颜色 (swatch + text 各 1) = 1 + 6 = 7 个节点
        assert len(result["nodes"]) == 7

    def test_legend_layer_has_bg_and_stroke(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
            {"id": "c", "color": "node_orange"},
        ]
        result = build_legend(plan, nodes, [], palette)
        layer = result["layers"][0]
        assert layer["_legend"] is True
        assert layer["_bg"] == "#F4F6F8"
        assert layer["_stroke"] == "#D5DCE4"

    def test_legend_has_title_node(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
            {"id": "c", "color": "node_orange"},
        ]
        result = build_legend(plan, nodes, [], palette)
        title = result["nodes"][0]
        assert title["id"] == "__legend_title__"
        assert "图例" in title["label"]
        assert title["bold"] is True

    def test_swatch_and_text_nodes_per_color(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
            {"id": "c", "color": "node_orange"},
        ]
        result = build_legend(plan, nodes, [], palette)
        swatches = [n for n in result["nodes"] if n["id"].startswith("__legend_swatch_")]
        texts = [n for n in result["nodes"] if n["id"].startswith("__legend_text_")]
        assert len(swatches) == 3
        assert len(texts) == 3
        for sw in swatches:
            assert "_fill_override" in sw
            assert sw["_legend"] is True

    def test_does_not_modify_original_plan(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
            {"id": "c", "color": "node_orange"},
        ]
        original_w = plan["width"]
        original_h = plan["height"]
        build_legend(plan, nodes, [], palette)
        assert plan["width"] == original_w
        assert plan["height"] == original_h


# ===== 位置参数 =====

class TestPositions:
    """测试不同位置参数"""

    def _make_three_color_nodes(self):
        return [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
            {"id": "c", "color": "node_orange"},
        ]

    def test_top_left_position(self, plan, palette):
        result = build_legend(plan, self._make_three_color_nodes(), [], palette, position="top_left")
        layer = result["layers"][0]
        margin = 24
        assert layer["x"] == margin
        assert layer["y"] == margin

    def test_top_right_position(self, plan, palette):
        result = build_legend(plan, self._make_three_color_nodes(), [], palette, position="top_right")
        layer = result["layers"][0]
        margin = 24
        assert layer["y"] == margin
        # x = plan_w - box_w - margin
        assert layer["x"] == plan["width"] - layer["w"] - margin

    def test_bottom_left_position(self, plan, palette):
        result = build_legend(plan, self._make_three_color_nodes(), [], palette, position="bottom_left")
        layer = result["layers"][0]
        margin = 24
        assert layer["x"] == margin
        assert layer["y"] == plan["height"] - layer["h"] - margin

    def test_bottom_right_default(self, plan, palette):
        # 不传 position 默认 bottom_right
        result_default = build_legend(plan, self._make_three_color_nodes(), [], palette)
        result_explicit = build_legend(plan, self._make_three_color_nodes(), [], palette, position="bottom_right")
        layer_default = result_default["layers"][0]
        layer_explicit = result_explicit["layers"][0]
        assert layer_default["x"] == layer_explicit["x"]
        assert layer_default["y"] == layer_explicit["y"]
        margin = 24
        assert layer_explicit["x"] == plan["width"] - layer_explicit["w"] - margin
        assert layer_explicit["y"] == plan["height"] - layer_explicit["h"] - margin

    def test_invalid_position_falls_back_to_bottom_right(self, plan, palette):
        result = build_legend(plan, self._make_three_color_nodes(), [], palette, position="invalid_pos")
        result_br = build_legend(plan, self._make_three_color_nodes(), [], palette, position="bottom_right")
        assert result["layers"][0]["x"] == result_br["layers"][0]["x"]
        assert result["layers"][0]["y"] == result_br["layers"][0]["y"]


# ===== 边颜色收集 =====

class TestEdgeColors:
    """测试边颜色纳入图例"""

    def test_edge_color_collected(self, plan, palette):
        # 节点 2 种颜色 + 边 1 种颜色 = 3 种，应生成图例
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
        ]
        edges = [{"from": "a", "to": "b", "color": "node_orange"}]
        result = build_legend(plan, nodes, edges, palette)
        assert len(result["layers"]) == 1
        # 应有 3 个 swatch
        swatches = [n for n in result["nodes"] if n["id"].startswith("__legend_swatch_")]
        assert len(swatches) == 3

    def test_edge_color_not_in_palette_ignored(self, plan, palette):
        # 边颜色既不在 palette 也不是 hex，应被忽略
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
        ]
        edges = [{"from": "a", "to": "b", "color": "random_named_color"}]
        result = build_legend(plan, nodes, edges, palette)
        # 仅 2 种颜色，不画图例
        assert result["layers"] == []
        assert result["nodes"] == []

    def test_duplicate_edge_color_not_double_counted(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
        ]
        edges = [
            {"from": "a", "to": "b", "color": "node_orange"},
            {"from": "a", "to": "b", "color": "node_orange"},  # 重复
        ]
        result = build_legend(plan, nodes, edges, palette)
        swatches = [n for n in result["nodes"] if n["id"].startswith("__legend_swatch_")]
        assert len(swatches) == 3  # 不应是 4

    def test_empty_edges(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
            {"id": "c", "color": "node_orange"},
        ]
        result = build_legend(plan, nodes, None, palette)
        assert len(result["layers"]) == 1


# ===== 自定义 hex 颜色 =====

class TestCustomHexColors:
    """测试自定义 hex 颜色纳入图例"""

    def test_custom_hex_color_included(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
            {"id": "c", "color": "#FF00FF"},
        ]
        result = build_legend(plan, nodes, [], palette)
        assert len(result["layers"]) == 1
        swatches = [n for n in result["nodes"] if n["id"].startswith("__legend_swatch_")]
        assert len(swatches) == 3
        # 第三个 swatch 应使用 #FF00FF 作为 fill（palette 中无此 key，fallback 到 #CCCCCC）
        # 实际行为：palette.get("#FF00FF", {}) → {} → fill = "#CCCCCC"
        third_swatch = [s for s in swatches if s["id"].endswith("_2__")][0]
        assert third_swatch["_fill_override"] == "#CCCCCC"

    def test_custom_hex_via_edge(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "node_green"},
        ]
        edges = [{"from": "a", "to": "b", "color": "#00AA00"}]
        result = build_legend(plan, nodes, edges, palette)
        assert len(result["layers"]) == 1
        swatches = [n for n in result["nodes"] if n["id"].startswith("__legend_swatch_")]
        assert len(swatches) == 3

    def test_mixed_palette_and_hex(self, plan, palette):
        nodes = [
            {"id": "a", "color": "node_blue"},
            {"id": "b", "color": "#FF0000"},
            {"id": "c", "color": "#00FF00"},
            {"id": "d", "color": "#0000FF"},
        ]
        result = build_legend(plan, nodes, [], palette)
        swatches = [n for n in result["nodes"] if n["id"].startswith("__legend_swatch_")]
        assert len(swatches) == 4
