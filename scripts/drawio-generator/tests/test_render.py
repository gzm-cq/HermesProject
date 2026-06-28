"""drawio_generator 单元测试"""

import json
import os
import tempfile
from drawio_generator.render import (
    generate_svg, render, PALETTES, DEFAULT_PALETTE, SHAPES,
    _desaturate, _lighten, _compute_bounding_box, validate_plan,
    repair_drawio,
)


class TestPalettes:
    """测试内置配色方案"""

    def test_academic_palette_exists(self):
        assert "academic" in PALETTES
        p = PALETTES["academic"]
        assert "node_blue" in p
        assert "bg" in p

    def test_all_palettes_have_required_keys(self):
        required = {"node_blue", "node_green", "node_orange", "node_yellow",
                    "node_purple", "node_red", "node_cyan", "bg",
                    "layer_bg", "layer_stroke"}
        for name, palette in PALETTES.items():
            assert required.issubset(palette.keys()), f"{name} missing keys"

    def test_default_palette_is_academic(self):
        assert DEFAULT_PALETTE == PALETTES["academic"]

    def test_paper_wireframe_palette_exists(self):
        assert "paper-wireframe" in PALETTES
        assert "paper-grayscale" in PALETTES
        for k in PALETTES["paper-wireframe"]:
            if isinstance(PALETTES["paper-wireframe"][k], dict):
                assert "fill" in PALETTES["paper-wireframe"][k]
                assert "stroke" in PALETTES["paper-wireframe"][k]

    def test_paper_grayscale_palette_exists(self):
        p = PALETTES["paper-grayscale"]
        assert "bg" in p
        assert "title_color" in p
        assert "text_color" in p


class TestColorUtils:
    """测试颜色工具函数"""

    def test_desaturate_blue(self):
        assert _desaturate("#4A6FA5") == "#6A6A6A"

    def test_desaturate_red(self):
        assert _desaturate("#FF0000") == "#4C4C4C"

    def test_lighten_blue(self):
        result = _lighten("#4A6FA5", 0.3)
        assert result.startswith("#")
        assert len(result) == 7

    def test_lighten_white(self):
        assert _lighten("#FFFFFF", 0.5) == "#FFFFFF"


class TestBoundingBox:
    """测试自动裁剪计算"""

    def test_empty_nodes(self):
        vx, vy, vw, vh = _compute_bounding_box([], [], 1000, 800)
        assert vw == 1000
        assert vh == 800

    def test_single_node(self):
        nodes = [{"id": "n1", "x": 100, "y": 100, "w": 200, "h": 80}]
        vx, vy, vw, vh = _compute_bounding_box(nodes, [], 1000, 800, padding=30)
        assert vx == 70
        assert vy == 70
        assert vw == 260  # 200 + 30*2
        assert vh == 140  # 80 + 30*2


class TestRenderDrawio:
    """测试 .drawio 渲染"""

    def test_simple_architecture(self):
        plan = {
            "title": "测试架构图",
            "width": 800,
            "height": 500,
            "nodes": [
                {"id": "node1", "label": "前端", "x": 50, "y": 100, "w": 150, "h": 60, "color": "node_blue"},
                {"id": "node2", "label": "后端", "x": 350, "y": 100, "w": 150, "h": 60, "color": "node_green"},
            ],
            "edges": [
                {"from": "node1", "to": "node2"},
            ],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            assert os.path.isfile(out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "<mxfile" in content
            assert "前端" in content
            assert "后端" in content
        finally:
            os.unlink(out_path)

    def test_empty_nodes(self):
        plan = {"title": "空图", "width": 400, "height": 300, "nodes": [], "edges": [], "format": "drawio"}
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            assert os.path.isfile(out_path)
        finally:
            os.unlink(out_path)

    def test_dashed_edge(self):
        plan = {
            "title": "虚线测试", "width": 600, "height": 400,
            "nodes": [
                {"id": "a", "label": "A", "x": 50, "y": 100, "w": 100, "h": 50},
                {"id": "b", "label": "B", "x": 300, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [{"from": "a", "to": "b", "dashed": True}],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            assert os.path.isfile(out_path)
        finally:
            os.unlink(out_path)

    def test_custom_palette(self):
        plan = {
            "title": "自定义配色", "width": 600, "height": 400,
            "nodes": [
                {"id": "n1", "label": "测试", "x": 50, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [],
            "palette": {"node_blue": {"fill": "#FF0000", "stroke": "#CC0000"},
                        "node_green": {"fill": "#00FF00", "stroke": "#00CC00"},
                        "bg": "#000000"},
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            assert os.path.isfile(out_path)
        finally:
            os.unlink(out_path)

    def test_emoji_node(self):
        """测试 emoji 图标"""
        plan = {
            "title": "Emoji Test", "width": 600, "height": 400,
            "nodes": [
                {"id": "n1", "label": "数据库", "x": 50, "y": 100, "w": 120, "h": 60, "emoji": "🗄️"},
            ],
            "edges": [],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            assert os.path.isfile(out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "🗄️" in content
        finally:
            os.unlink(out_path)

    def test_bold_node(self):
        """测试粗体文字"""
        plan = {
            "title": "Bold Test", "width": 600, "height": 400,
            "nodes": [
                {"id": "n1", "label": "重要节点", "x": 50, "y": 100, "w": 120, "h": 50, "bold": True},
            ],
            "edges": [],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            assert os.path.isfile(out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "&lt;b&gt;" in content
        finally:
            os.unlink(out_path)

    def test_node_without_id(self):
        """测试节点无 id 字段时自动分配"""
        plan = {
            "title": "No ID", "width": 600, "height": 400,
            "nodes": [
                {"label": "Node A", "x": 50, "y": 100, "w": 100, "h": 50},
                {"label": "Node B", "x": 250, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            assert os.path.isfile(out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "Node A" in content
            assert "Node B" in content
        finally:
            os.unlink(out_path)

    def test_layers_without_label(self):
        """测试区域层无 label 的情况"""
        plan = {
            "title": "Layer no label", "width": 600, "height": 400,
            "layers": [{"x": 30, "y": 80, "w": 540, "h": 120}],
            "nodes": [
                {"id": "n1", "label": "X", "x": 50, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            assert os.path.isfile(out_path)
        finally:
            os.unlink(out_path)

    def test_drawio_stroke_width(self):
        """drawio 描边宽度参数"""
        plan = {
            "title": "SW", "width": 600, "height": 400,
            "nodes": [{"id": "n1", "label": "X", "x": 50, "y": 50, "w": 100, "h": 50}],
            "edges": [],
            "stroke_width": 3,
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "strokeWidth=3" in content
        finally:
            os.unlink(out_path)

    def test_drawio_sub_label(self):
        """drawio sub_label 数据标注"""
        plan = {
            "title": "Sub", "width": 600, "height": 400,
            "nodes": [{"id": "n1", "label": "API", "x": 50, "y": 50, "w": 100, "h": 50, "sub_label": "100ms"}],
            "edges": [],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "100ms" in content
        finally:
            os.unlink(out_path)

    def test_drawio_edge_label(self):
        """drawio 边标签"""
        plan = {
            "title": "EdgeLabel", "width": 600, "height": 400,
            "nodes": [
                {"id": "a", "label": "A", "x": 50, "y": 100, "w": 100, "h": 50},
                {"id": "b", "label": "B", "x": 300, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [{"from": "a", "to": "b", "label": "HTTP"}],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "HTTP" in content
        finally:
            os.unlink(out_path)


class TestRenderSvg:
    """测试 SVG 渲染"""

    def test_simple_svg(self):
        plan = {
            "title": "SVG Test",
            "width": 600,
            "height": 400,
            "nodes": [
                {"id": "n1", "label": "Hello", "x": 50, "y": 100, "w": 120, "h": 50},
            ],
            "edges": [],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            assert os.path.isfile(out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "<svg" in content
            assert "Hello" in content
        finally:
            os.unlink(out_path)

    def test_svg_uses_title_color(self):
        """SVG 应使用配色的 title_color"""
        plan = {
            "title": "Color Test",
            "width": 600,
            "height": 400,
            "nodes": [],
            "edges": [],
            "palette": {"bg": "#ffffff", "title_color": "#FF0000", "text_color": "#00FF00"},
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "#FF0000" in content
        finally:
            os.unlink(out_path)

    def test_svg_vertical_edge(self):
        """SVG 垂直箭头"""
        plan = {
            "title": "Vertical",
            "width": 400,
            "height": 500,
            "nodes": [
                {"id": "top", "label": "上", "x": 100, "y": 50, "w": 100, "h": 50},
                {"id": "bot", "label": "下", "x": 100, "y": 250, "w": 100, "h": 50},
            ],
            "edges": [{"from": "top", "to": "bot"}],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            assert os.path.isfile(out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "M 150,100 150,250" in content
        finally:
            os.unlink(out_path)

    def test_svg_auto_fit(self):
        """SVG 自动裁剪 viewBox"""
        plan = {
            "title": "Crop",
            "width": 1000,
            "height": 800,
            "nodes": [
                {"id": "n1", "label": "N1", "x": 100, "y": 100, "w": 200, "h": 80},
            ],
            "edges": [],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            # 自动裁剪 viewBox 应接近节点范围
            assert "viewBox" in content
            assert "260" in content  # 200+60
        finally:
            os.unlink(out_path)

    def test_svg_dashed_edge(self):
        """SVG 虚线箭头"""
        plan = {
            "title": "dashed",
            "width": 600,
            "height": 400,
            "nodes": [
                {"id": "a", "label": "A", "x": 50, "y": 100, "w": 100, "h": 50},
                {"id": "b", "label": "B", "x": 300, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [{"from": "a", "to": "b", "dashed": True}],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "stroke-dasharray" in content
        finally:
            os.unlink(out_path)

    def test_svg_left_to_right_edge(self):
        """SVG 水平右箭头"""
        plan = {
            "title": "L2R",
            "width": 600,
            "height": 400,
            "nodes": [
                {"id": "l", "label": "左", "x": 50, "y": 100, "w": 100, "h": 50},
                {"id": "r", "label": "右", "x": 350, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [{"from": "l", "to": "r"}],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            generate_svg(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "M 150,125 350,125" in content
        finally:
            os.unlink(out_path)

    def test_svg_font_family(self):
        """SVG 自定义字体"""
        plan = {
            "title": "Font", "width": 400, "height": 300,
            "nodes": [{"id": "n1", "label": "Text", "x": 50, "y": 50, "w": 100, "h": 50}],
            "edges": [],
            "font_family": "Times New Roman, serif",
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "Times New Roman" in content
        finally:
            os.unlink(out_path)

    def test_svg_shadow(self):
        """SVG 投影 filter"""
        plan = {
            "title": "Shadow", "width": 400, "height": 300,
            "nodes": [{"id": "n1", "label": "X", "x": 50, "y": 50, "w": 100, "h": 50}],
            "edges": [],
            "shadow": True,
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "feDropShadow" in content
        finally:
            os.unlink(out_path)

    def test_svg_gradient(self):
        """SVG 渐变填充"""
        plan = {
            "title": "Grad", "width": 400, "height": 300,
            "nodes": [{"id": "n1", "label": "X", "x": 50, "y": 50, "w": 100, "h": 50}],
            "edges": [],
            "gradient": True,
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "linearGradient" in content
            assert "url(#g_" in content
        finally:
            os.unlink(out_path)

    def test_svg_sub_label(self):
        """SVG 数据标注"""
        plan = {
            "title": "Sub", "width": 400, "height": 300,
            "nodes": [{"id": "n1", "label": "API", "x": 50, "y": 50, "w": 100, "h": 50, "sub_label": "5TB"}],
            "edges": [],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "5TB" in content
            assert "opacity=\"0.65\"" in content
        finally:
            os.unlink(out_path)

    def test_svg_edge_label(self):
        """SVG 边标签"""
        plan = {
            "title": "EL", "width": 600, "height": 400,
            "nodes": [
                {"id": "a", "label": "A", "x": 50, "y": 100, "w": 100, "h": 50},
                {"id": "b", "label": "B", "x": 300, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [{"from": "a", "to": "b", "label": "TCP"}],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "TCP" in content
        finally:
            os.unlink(out_path)

    def test_svg_open_arrow(self):
        """SVG open arrow 样式"""
        plan = {
            "title": "OpenArrow", "width": 600, "height": 400,
            "nodes": [
                {"id": "a", "label": "A", "x": 50, "y": 100, "w": 100, "h": 50},
                {"id": "b", "label": "B", "x": 300, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [{"from": "a", "to": "b"}],
            "arrow_style": "open",
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            # open arrow 使用 stroke 而非 fill
            assert 'fill="none"' in content
            assert 'stroke="#' in content
        finally:
            os.unlink(out_path)

    def test_svg_diamond_arrow(self):
        """SVG diamond arrow 样式"""
        plan = {
            "title": "Diamond", "width": 600, "height": 400,
            "nodes": [
                {"id": "a", "label": "A", "x": 50, "y": 100, "w": 100, "h": 50},
                {"id": "b", "label": "B", "x": 300, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [{"from": "a", "to": "b"}],
            "arrow_style": "diamond",
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "M 0 5 L 5 0 L 10 5 L 5 10 z" in content
        finally:
            os.unlink(out_path)

    def test_svg_grayscale(self):
        """SVG 灰度模式"""
        plan = {
            "title": "Gray", "width": 400, "height": 300,
            "nodes": [{"id": "n1", "label": "X", "x": 50, "y": 50, "w": 100, "h": 50, "color": "node_red"}],
            "edges": [],
            "grayscale": True,
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            # 灰度模式不应该出现原始彩色值
            assert "#A05050" not in content  # academic node_red stroke
        finally:
            os.unlink(out_path)

    def test_svg_paper_wireframe_palette(self):
        """SVG 使用 paper-wireframe 配色"""
        plan = {
            "title": "WF", "width": 600, "height": 400,
            "nodes": [{"id": "n1", "label": "Node", "x": 50, "y": 50, "w": 150, "h": 60}],
            "edges": [],
            "palette": "paper-wireframe",
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "#5C5C5C" in content
        finally:
            os.unlink(out_path)

    def test_svg_paper_grayscale_palette(self):
        """SVG 使用 paper-grayscale 配色"""
        plan = {
            "title": "GS", "width": 600, "height": 400,
            "nodes": [{"id": "n1", "label": "Node", "x": 50, "y": 50, "w": 150, "h": 60}],
            "edges": [],
            "palette": "paper-grayscale",
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "#333333" in content  # stroke 颜色
        finally:
            os.unlink(out_path)


class TestConfigPresets:
    """测试 paper_mode / presentation 预设"""

    def test_paper_mode_defaults(self):
        """paper_mode 应自动设为 SVG、Times 字体、open arrow"""
        plan = {
            "title": "Paper", "width": 600, "height": 400,
            "nodes": [{"id": "n1", "label": "X", "x": 50, "y": 50, "w": 100, "h": 50}],
            "edges": [],
            "paper_mode": True,
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "Times New Roman" in content
            assert 'fill="none"' in content  # open arrow
        finally:
            os.unlink(out_path)

    def test_presentation_has_shadow(self):
        """presentation 应开启投影"""
        plan = {
            "title": "Pres", "width": 400, "height": 300,
            "nodes": [{"id": "n1", "label": "X", "x": 50, "y": 50, "w": 100, "h": 50}],
            "edges": [],
            "presentation": True,
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "feDropShadow" in content
            assert "linearGradient" in content
        finally:
            os.unlink(out_path)


class TestRender:
    """测试新的 render() 入口"""

    def test_render_dict(self):
        plan = {"title": "R", "width": 400, "height": 300, "nodes": [], "edges": [], "format": "drawio"}
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            assert os.path.isfile(out_path)
        finally:
            os.unlink(out_path)

    def test_render_default_to_drawio(self):
        """不指定 format 时默认 drawio"""
        plan = {"title": "Default", "width": 400, "height": 300, "nodes": [], "edges": []}
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "<mxfile" in content
        finally:
            os.unlink(out_path)

    def test_render_with_all_features(self):
        """全功能组合渲染"""
        plan = {
            "title": "All In",
            "width": 1000,
            "height": 600,
            "nodes": [
                {"id": "a", "label": "主模块", "x": 100, "y": 200, "w": 160, "h": 60,
                 "color": "node_blue", "sub_label": "核心"},
                {"id": "b", "label": "从模块", "x": 400, "y": 200, "w": 160, "h": 60,
                 "color": "node_green"},
            ],
            "edges": [
                {"from": "a", "to": "b", "label": "RPC", "dashed": False},
            ],
            "layers": [
                {"x": 50, "y": 160, "w": 900, "h": 140, "label": "服务层"},
            ],
            "font_family": "Arial",
            "stroke_width": 2,
            "arrow_style": "diamond",
            "shadow": True,
            "auto_fit": True,
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "Arial" in content
            assert "feDropShadow" in content
            assert "diamond" in content or "M 0 5 L 5 0" in content
            assert "核心" in content
            assert "RPC" in content
            assert "服务层" in content
        finally:
            os.unlink(out_path)


class TestMainEntry:
    """测试 main() CLI 入口"""

    def test_main_with_json_file(self):
        plan = {
            "title": "CLI Test",
            "width": 500,
            "height": 300,
            "nodes": [{"id": "x", "label": "X", "x": 50, "y": 50, "w": 100, "h": 50}],
            "edges": [],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as f:
            json.dump(plan, f)
            json_path = f.name
        out_path = json_path.replace(".json", ".drawio")
        try:
            from drawio_generator.render import main
            import sys
            old_argv = sys.argv
            sys.argv = ["drawio-render", json_path, out_path]
            try:
                main()
            except SystemExit:
                pass
            assert os.path.isfile(out_path)
        finally:
            sys.argv = old_argv
            os.unlink(json_path)
            if os.path.isfile(out_path):
                os.unlink(out_path)

    def test_main_missing_args(self):
        """缺参数应报错退出"""
        from drawio_generator.render import main
        import sys
        old_argv = sys.argv
        sys.argv = ["drawio-render"]
        try:
            main()
            assert False, "应该退出"
        except SystemExit:
            pass
        finally:
            sys.argv = old_argv


class TestValidatePlan:
    """测试输入校验"""

    def test_valid_plan_no_errors(self):
        """合法 plan 应无 error"""
        plan = {
            "title": "Test", "width": 800, "height": 600,
            "nodes": [{"id": "a", "label": "A", "x": 50, "y": 50, "w": 100, "h": 50}],
            "edges": [],
        }
        issues = validate_plan(plan)
        errors = [i for i in issues if i[0] == "error"]
        assert errors == [], f"unexpected errors: {errors}"

    def test_missing_title(self):
        """缺 title 应报错"""
        issues = validate_plan({"nodes": [], "edges": []})
        assert any(i[0] == "error" and "title" in i[1] for i in issues)

    def test_missing_nodes(self):
        """缺 nodes 应报错"""
        issues = validate_plan({"title": "T", "edges": []})
        assert any(i[0] == "error" and "nodes" in i[1] for i in issues)

    def test_duplicate_node_id(self):
        """重复 id 应报错"""
        plan = {
            "title": "T", "nodes": [
                {"id": "x", "label": "A", "x": 10, "y": 10, "w": 50, "h": 30},
                {"id": "x", "label": "B", "x": 100, "y": 10, "w": 50, "h": 30},
            ], "edges": [],
        }
        issues = validate_plan(plan)
        assert any(i[0] == "error" and "重复" in i[2] for i in issues)

    def test_node_missing_coords(self):
        """节点缺坐标应报错"""
        plan = {
            "title": "T", "nodes": [{"id": "a", "label": "A"}], "edges": [],
        }
        issues = validate_plan(plan)
        assert any(i[0] == "error" and ".x" in i[1] for i in issues)

    def test_unknown_palette_warning(self):
        """未知配色应 warning"""
        plan = {
            "title": "T", "nodes": [], "edges": [], "palette": "nonexistent",
        }
        issues = validate_plan(plan)
        assert any(i[0] == "warning" and "palette" in i[1] for i in issues)

    def test_unknown_format_warning(self):
        """未知 format 应 warning"""
        plan = {
            "title": "T", "nodes": [], "edges": [], "format": "pdf",
        }
        issues = validate_plan(plan)
        assert any(i[0] == "warning" and "format" in i[1] for i in issues)

    def test_unknown_shape_warning(self):
        """未知 shape 应 warning"""
        plan = {
            "title": "T",
            "nodes": [{"id": "a", "label": "A", "x": 10, "y": 10, "w": 50, "h": 30,
                        "shape": "star"}],
            "edges": [],
        }
        issues = validate_plan(plan)
        assert any(i[0] == "warning" and "shape" in i[1] for i in issues)

    def test_edge_ref_undefined_node(self):
        """边引用未定义节点应 warning"""
        plan = {
            "title": "T",
            "nodes": [{"id": "a", "label": "A", "x": 10, "y": 10, "w": 50, "h": 30}],
            "edges": [{"from": "a", "to": "nonexistent"}],
        }
        issues = validate_plan(plan)
        assert any("nonexistent" in i[2] for i in issues)

    def test_not_dict_error(self):
        """非 dict 输入应报错"""
        issues = validate_plan("not a dict")
        assert any(i[0] == "error" and "root" in i[1] for i in issues)


class TestNodeShapes:
    """测试节点形状渲染"""

    def test_shapes_defined(self):
        """SHAPES 应包含基本形状"""
        for name in ("rect", "process", "cylinder", "hexagon"):
            assert name in SHAPES, f"missing shape: {name}"
            assert "drawio" in SHAPES[name]

    def test_svg_cylinder_shape(self):
        """SVG cylinder 应包含椭圆"""
        plan = {
            "title": "DB", "width": 400, "height": 300,
            "nodes": [{"id": "db", "label": "数据库", "x": 50, "y": 50,
                        "w": 120, "h": 80, "shape": "cylinder"}],
            "edges": [],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "<ellipse" in content
            assert "数据库" in content
        finally:
            os.unlink(out_path)

    def test_svg_hexagon_shape(self):
        """SVG hexagon 应包含 polygon"""
        plan = {
            "title": "Hex", "width": 400, "height": 300,
            "nodes": [{"id": "h", "label": "处理", "x": 50, "y": 50,
                        "w": 100, "h": 60, "shape": "hexagon"}],
            "edges": [],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "<polygon" in content
        finally:
            os.unlink(out_path)

    def test_svg_process_shape(self):
        """SVG process 应为无圆角矩形"""
        plan = {
            "title": "Proc", "width": 400, "height": 300,
            "nodes": [{"id": "p", "label": "处理", "x": 50, "y": 50,
                        "w": 100, "h": 50, "shape": "process"}],
            "edges": [],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert 'rx="0"' in content
        finally:
            os.unlink(out_path)

    def test_drawio_cylinder_shape(self):
        """drawio cylinder 应包含 shape=cylinder"""
        plan = {
            "title": "DB", "width": 400, "height": 300,
            "nodes": [{"id": "db", "label": "DB", "x": 50, "y": 50,
                        "w": 120, "h": 80, "shape": "cylinder"}],
            "edges": [],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "shape=cylinder" in content
        finally:
            os.unlink(out_path)

    def test_drawio_hexagon_shape(self):
        """drawio hexagon 应包含 shape=hexagon"""
        plan = {
            "title": "Hex", "width": 400, "height": 300,
            "nodes": [{"id": "h", "label": "Hex", "x": 50, "y": 50,
                        "w": 100, "h": 60, "shape": "hexagon"}],
            "edges": [],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "shape=hexagon" in content
        finally:
            os.unlink(out_path)

    def test_drawio_process_shape(self):
        """drawio process 应为 rounded=0"""
        plan = {
            "title": "Proc", "width": 400, "height": 300,
            "nodes": [{"id": "p", "label": "Proc", "x": 50, "y": 50,
                        "w": 100, "h": 50, "shape": "process"}],
            "edges": [],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "rounded=0" in content
        finally:
            os.unlink(out_path)


class TestRepairDrawio:
    """测试 drawio 文件修复"""

    def _make_healthy_xml(self, path):
        """创建一个健康的 drawio XML"""
        import xml.etree.ElementTree as ET
        mxfile = ET.Element("mxfile", host="Electron", version="24.6.4")
        d = ET.SubElement(mxfile, "diagram", id="1", name="Test")
        g = ET.SubElement(d, "mxGraphModel")
        r = ET.SubElement(g, "root")
        ET.SubElement(r, "mxCell", id="0")
        ET.SubElement(r, "mxCell", id="1", parent="0")
        cell = ET.SubElement(r, "mxCell", id="100", parent="1", vertex="1",
                              value="Node", style="rounded=1;")
        geo = ET.SubElement(cell, "mxGeometry",
                            x="50", y="50", width="100", height="50",
                            **{"as": "geometry"})
        tree = ET.ElementTree(mxfile)
        tree.write(path, encoding="utf-8", xml_declaration=True)

    def test_healthy_file(self):
        """正常文件应无 error"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            self._make_healthy_xml(out_path)
            fixed, issues = repair_drawio(out_path)
            errors = [i for i in issues if i[0] == "error"]
            assert errors == [], f"unexpected: {errors}"
            assert fixed == 0
        finally:
            os.unlink(out_path)

    def test_broken_parent(self):
        """parent 引用不存在 cell → 自动修复"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            import xml.etree.ElementTree as ET
            mxfile = ET.Element("mxfile")
            d = ET.SubElement(mxfile, "diagram")
            g = ET.SubElement(d, "mxGraphModel")
            r = ET.SubElement(g, "root")
            ET.SubElement(r, "mxCell", id="0")
            ET.SubElement(r, "mxCell", id="1", parent="0")
            ET.SubElement(r, "mxCell", id="bug", parent="999",
                           vertex="1", value="X", style="rounded=1;")
            tree = ET.ElementTree(mxfile)
            tree.write(out_path, encoding="utf-8", xml_declaration=True)

            fixed, issues = repair_drawio(out_path)
            assert any(i[0] == "fixed" for i in issues), f"no fix: {issues}"
            assert fixed >= 1

            # 验证修复结果
            tree2 = ET.parse(out_path)
            for c in tree2.iter("mxCell"):
                if c.get("id") == "bug":
                    assert c.get("parent") == "1"
        finally:
            os.unlink(out_path)

    def test_missing_relative(self):
        """edge mxGeometry 缺 relative → 自动修复"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            import xml.etree.ElementTree as ET
            mxfile = ET.Element("mxfile")
            d = ET.SubElement(mxfile, "diagram")
            g = ET.SubElement(d, "mxGraphModel")
            r = ET.SubElement(g, "root")
            ET.SubElement(r, "mxCell", id="0")
            ET.SubElement(r, "mxCell", id="1", parent="0")
            edge = ET.SubElement(r, "mxCell", id="200", parent="1",
                                  edge="1", source="100", target="101",
                                  style="endArrow=classic;")
            # 故意不设 relative
            ET.SubElement(edge, "mxGeometry", **{"as": "geometry"})
            tree = ET.ElementTree(mxfile)
            tree.write(out_path, encoding="utf-8", xml_declaration=True)

            fixed, issues = repair_drawio(out_path)
            assert fixed >= 1, f"no fix: {issues}"

            tree2 = ET.parse(out_path)
            for c in tree2.iter("mxCell"):
                if c.get("edge") == "1":
                    for g in c.iter("mxGeometry"):
                        assert g.get("relative") == "1"
        finally:
            os.unlink(out_path)

    def test_non_ascii_id(self):
        """非 ASCII id 应 warning"""
        content = '''<?xml version="1.0" encoding="utf-8"?>\n<mxfile>\n  <diagram>\n    <mxGraphModel>\n      <root>\n        <mxCell id="0"/>\n        <mxCell id="1" parent="0"/>\n        <mxCell id="\u8282\u70b91" parent="1" vertex="1" value="T" style="r=1;"/>\n      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>'''
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False, mode="w", encoding="utf-8") as f:
            f.write(content)
            out_path = f.name
        try:
            _, issues = repair_drawio(out_path)
            assert any(i[0] == "warn" and "非 ASCII" in i[1] for i in issues)
        finally:
            os.unlink(out_path)

    def test_invalid_xml(self):
        """无效 XML → error"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False, mode="w", encoding="utf-8") as f:
            f.write("not valid xml<<<>>>")
            out_path = f.name
        try:
            _, issues = repair_drawio(out_path)
            assert any(i[0] == "error" for i in issues)
        finally:
            os.unlink(out_path)

    def test_file_not_found(self):
        """文件不存在 → error"""
        _, issues = repair_drawio("/nonexistent/foo.drawio")
        assert any(i[0] == "error" and "不存在" in i[1] for i in issues)

    def test_normal_render_clean(self):
        """正常渲染生成的 drawio 应无问题"""
        plan = {
            "title": "Test", "width": 600, "height": 400,
            "nodes": [
                {"id": "a", "label": "A", "x": 50, "y": 100, "w": 100, "h": 50},
                {"id": "b", "label": "B", "x": 300, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [{"from": "a", "to": "b"}],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            fixed, issues = repair_drawio(out_path)
            errors = [i for i in issues if i[0] == "error"]
            assert errors == []
        finally:
            os.unlink(out_path)


class TestAutoLayout:
    """测试 auto_layout 集成"""

    def test_auto_layout_flag(self):
        """auto_layout=True 时自动布局，节点坐标被补全"""
        plan = {
            "title": "AutoLayout", "width": 800, "height": 600,
            "nodes": [
                {"id": "a", "label": "A"},
                {"id": "b", "label": "B"},
            ],
            "edges": [{"from": "a", "to": "b"}],
            "format": "drawio",
            "auto_layout": True,
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            assert os.path.isfile(out_path)
            # 验证 layout 已被执行：plan 中的节点已被填充坐标
            assert "x" in plan["nodes"][0]
            assert "y" in plan["nodes"][0]
            assert plan["nodes"][0]["x"] >= 0
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "A" in content
            assert "B" in content
        finally:
            os.unlink(out_path)

    def test_missing_coordinates_triggers_layout(self):
        """节点缺 x/y 时自动调用布局"""
        plan = {
            "title": "NoCoords", "width": 800, "height": 600,
            "nodes": [
                {"id": "a", "label": "A"},
                {"id": "b", "label": "B"},
                {"id": "c", "label": "C"},
            ],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            assert os.path.isfile(out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "<svg" in content
            assert "A" in content
            assert "B" in content
            assert "C" in content
        finally:
            os.unlink(out_path)

    def test_horizontal_layout_direction(self):
        """layout_direction=horizontal 生效，节点水平排列"""
        plan = {
            "title": "Horizontal", "width": 800, "height": 600,
            "nodes": [
                {"id": "a", "label": "A"},
                {"id": "b", "label": "B"},
            ],
            "edges": [{"from": "a", "to": "b"}],
            "format": "drawio",
            "auto_layout": True,
            "layout_direction": "horizontal",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            assert os.path.isfile(out_path)
            # 水平布局：a 和 b 在同一行，b 在 a 右侧
            a_node = plan["nodes"][0]
            b_node = plan["nodes"][1]
            assert b_node["x"] > a_node["x"]
            assert abs(b_node["y"] - a_node["y"]) < 60  # 同层 y 接近
        finally:
            os.unlink(out_path)

    def test_auto_layout_with_existing_coords_unchanged(self):
        """已有坐标的节点不触发重新布局"""
        plan = {
            "title": "Fixed", "width": 800, "height": 600,
            "nodes": [
                {"id": "a", "label": "A", "x": 100, "y": 100, "w": 100, "h": 50},
                {"id": "b", "label": "B", "x": 400, "y": 100, "w": 100, "h": 50},
            ],
            "edges": [{"from": "a", "to": "b"}],
            "format": "drawio",
        }
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            assert os.path.isfile(out_path)
        finally:
            os.unlink(out_path)

    def test_auto_layout_with_isolated_nodes(self):
        """孤立节点独立排布在上方，不影响连通图"""
        plan = {
            "title": "Isolated", "width": 800, "height": 600,
            "nodes": [
                {"id": "a", "label": "A"},
                {"id": "b", "label": "B"},
                {"id": "c", "label": "C"},
            ],
            "edges": [{"from": "a", "to": "b"}],
            "format": "svg",
            "auto_layout": True,
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            assert os.path.isfile(out_path)
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
            assert "<svg" in content
            assert "A" in content
            assert "B" in content
            assert "C" in content
            # 孤立节点 c 的 y 坐标应小于连通节点 a, b
            c_node = plan["nodes"][2]
            a_node = plan["nodes"][0]
            b_node = plan["nodes"][1]
            assert c_node["y"] < a_node["y"]
            assert c_node["y"] < b_node["y"]
        finally:
            os.unlink(out_path)

    def test_svg_cylinder_small_size(self):
        """小尺寸 cylinder 不会崩溃"""
        plan = {
            "title": "CylSmall", "width": 400, "height": 200,
            "nodes": [
                {"id": "a", "label": "A", "x": 100, "y": 80,
                 "w": 60, "h": 10, "shape": "cylinder"},
            ],
            "edges": [],
            "format": "svg",
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                assert "<svg" in f.read()
        finally:
            os.unlink(out_path)

    def test_svg_font_size_non_dict(self):
        """font_size 传入非 dict 不崩溃"""
        plan = {
            "title": "FontTest", "width": 400, "height": 200,
            "nodes": [
                {"id": "a", "label": "A", "x": 100, "y": 80, "w": 100, "h": 50},
            ],
            "edges": [],
            "format": "svg",
            "font_size": 12,
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            out_path = f.name
        try:
            render(plan, out_path)
            with open(out_path, encoding="utf-8") as f:
                assert "<svg" in f.read()
        finally:
            os.unlink(out_path)
