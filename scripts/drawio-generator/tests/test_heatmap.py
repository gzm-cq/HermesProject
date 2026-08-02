"""
Test heatmap.py — 热力图着色单元测试
"""

import json
import os
import tempfile
import xml.etree.ElementTree as ET

import pytest

# heatmap 模块就从 scripts/heatmap.py 导入
from heatmap import (
    PALETTES,
    apply_heatmap,
    hex_to_rgb,
    lerp_color,
    rgb_to_hex,
    value_to_color,
    parse_style,
    build_style,
)


# ===== 辅助函数 =====

def _make_drawio(nodes):
    """生成一个简单的 .drawio XML 字符串，nodes 为 [(id, label, style), ...]"""
    mxfile = ET.Element("mxfile", host="Electron", version="24.6.4")
    diagram = ET.SubElement(mxfile, "diagram", id="1", name="Test")
    model = ET.SubElement(diagram, "mxGraphModel", dx="0", dy="0", grid="1", gridSize="10")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    for i, (cid, label, style) in enumerate(nodes):
        cell = ET.SubElement(root, "mxCell", id=cid, parent="1", value=label, style=style, vertex="1")
        ET.SubElement(cell, "mxGeometry", x="0", y=str(i * 60), width="100", height="50", **{"as": "geometry"})

    return ET.tostring(mxfile, encoding="unicode")


def _read_fillcolor(drawio_path, cell_id):
    """从 .drawio 文件中读取指定 cell 的 fillColor"""
    tree = ET.parse(drawio_path)
    for cell in tree.iter("mxCell"):
        if cell.get("id") == cell_id:
            style = cell.get("style", "")
            for part in style.split(";"):
                part = part.strip()
                if part.startswith("fillColor="):
                    return part.split("=", 1)[1]
    return None


# ===== fixture =====

@pytest.fixture
def sample_drawio():
    """生成一个含 3 个节点的 .drawio 临时文件"""
    nodes = [
        ("node-a", "Node A", "rounded=1;fillColor=#ffffff;strokeColor=#000000;"),
        ("node-b", "Node B", "rounded=1;fillColor=#ffffff;strokeColor=#000000;"),
        ("node-c", "Node C", "rounded=1;fillColor=#ffffff;strokeColor=#000000;"),
    ]
    xml_str = _make_drawio(nodes)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".drawio", delete=False, encoding="utf-8") as f:
        f.write(xml_str)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def sample_metrics():
    metrics = {"node-a": 0.1, "node-b": 0.5, "node-c": 0.9}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(metrics, f)
        path = f.name
    yield path
    os.unlink(path)


# ===== 单元测试：颜色工具函数 =====

class TestColorUtils:
    def test_hex_to_rgb(self):
        assert hex_to_rgb("#d5e8d4") == (213, 232, 212)
        assert hex_to_rgb("#f8cecc") == (248, 206, 204)
        assert hex_to_rgb("#fff2cc") == (255, 242, 204)
        assert hex_to_rgb("#000000") == (0, 0, 0)
        assert hex_to_rgb("#ffffff") == (255, 255, 255)

    def test_rgb_to_hex(self):
        assert rgb_to_hex(213, 232, 212) == "#d5e8d4"
        assert rgb_to_hex(248, 206, 204) == "#f8cecc"
        assert rgb_to_hex(0, 0, 0) == "#000000"
        assert rgb_to_hex(255, 255, 255) == "#ffffff"

    def test_lerp_color(self):
        """线性插值：绿色 → 红色"""
        # t=0 → 纯绿色
        assert lerp_color("#d5e8d4", "#f8cecc", 0.0) == "#d5e8d4"
        # t=1 → 纯红色
        assert lerp_color("#d5e8d4", "#f8cecc", 1.0) == "#f8cecc"
        # t=0.5 → 中间值
        mid = lerp_color("#d5e8d4", "#f8cecc", 0.5)
        r, g, b = hex_to_rgb(mid)
        # 应在绿色和红色之间
        assert 213 < r < 248
        assert 206 < g < 232
        assert 204 < b < 212

    def test_lerp_color_identity(self):
        """相同颜色插值应返回相同颜色"""
        assert lerp_color("#ff0000", "#ff0000", 0.0) == "#ff0000"
        assert lerp_color("#ff0000", "#ff0000", 0.5) == "#ff0000"
        assert lerp_color("#ff0000", "#ff0000", 1.0) == "#ff0000"

    def test_value_to_color_min(self):
        """最小值应映射到低段颜色"""
        palette = PALETTES["default"]
        color = value_to_color(0.1, 0.1, 0.9, palette)
        expected = lerp_color(palette["low"], palette["mid"], 0.0)
        assert color == expected

    def test_value_to_color_max(self):
        """最大值应映射到高段颜色"""
        palette = PALETTES["default"]
        color = value_to_color(0.9, 0.1, 0.9, palette)
        expected = lerp_color(palette["mid"], palette["high"], 1.0)
        assert color == expected

    def test_value_to_color_mid(self):
        """中间值应映射到 mid 颜色"""
        palette = PALETTES["default"]
        color = value_to_color(0.5, 0.1, 0.9, palette)
        assert color == palette["mid"]

    def test_value_to_color_single_value(self):
        """所有值相同时返回 mid 颜色"""
        palette = PALETTES["default"]
        color = value_to_color(0.5, 0.5, 0.5, palette)
        assert color == palette["mid"]

    def test_value_to_color_blue_palette(self):
        """使用 blue 调色板"""
        palette = PALETTES["blue"]
        color = value_to_color(0.0, 0.0, 1.0, palette)
        expected = lerp_color(palette["low"], palette["mid"], 0.0)
        assert color == expected
        color = value_to_color(1.0, 0.0, 1.0, palette)
        expected = lerp_color(palette["mid"], palette["high"], 1.0)
        assert color == expected


# ===== 单元测试：Style 解析 =====

class TestStyleParsing:
    def test_parse_style(self):
        style = "rounded=1;fillColor=#ffffff;strokeColor=#000000;"
        result = parse_style(style)
        assert result["rounded"] == "1"
        assert result["fillColor"] == "#ffffff"
        assert result["strokeColor"] == "#000000"

    def test_parse_style_no_trailing_semicolon(self):
        style = "rounded=1;fillColor=#ffffff"
        result = parse_style(style)
        assert result["rounded"] == "1"
        assert result["fillColor"] == "#ffffff"

    def test_parse_style_flag_only(self):
        style = "rounded=1;html=1;shadow=1;"
        result = parse_style(style)
        assert result["shadow"] == "1"

    def test_build_style(self):
        d = {"rounded": "1", "fillColor": "#ff0000"}
        result = build_style(d)
        assert "rounded=1" in result
        assert "fillColor=#ff0000" in result
        assert result.endswith(";")

    def test_roundtrip(self):
        original = "rounded=1;fillColor=#ffffff;strokeColor=#000000;fontSize=12;"
        result = build_style(parse_style(original))
        # 解析再构建应保持语义一致（顺序可能不同）
        assert parse_style(result) == parse_style(original)


# ===== 集成测试：完整热力图流程 =====

class TestHeatmapIntegration:
    def test_basic_heatmap(self, sample_drawio, sample_metrics):
        """基本的完整热力图着色流程"""
        output_path = sample_drawio + ".out.drawio"
        try:
            with open(sample_metrics, "r") as f:
                metrics = json.load(f)
            apply_heatmap(sample_drawio, metrics, output_path, "default", "auto")

            # 验证输出文件存在
            assert os.path.exists(output_path)

            # 验证节点颜色已修改
            c_a = _read_fillcolor(output_path, "node-a")
            c_b = _read_fillcolor(output_path, "node-b")
            c_c = _read_fillcolor(output_path, "node-c")

            assert c_a is not None, "node-a should have fillColor"
            assert c_b is not None, "node-b should have fillColor"
            assert c_c is not None, "node-c should have fillColor"

            # 最小值 (0.1) → 绿色
            assert c_a == PALETTES["default"]["low"], f"Expected {PALETTES['default']['low']}, got {c_a}"

            # 中间值 (0.5) → 黄色
            assert c_b == PALETTES["default"]["mid"], f"Expected {PALETTES['default']['mid']}, got {c_b}"

            # 最大值 (0.9) → 红色
            assert c_c == PALETTES["default"]["high"], f"Expected {PALETTES['default']['high']}, got {c_c}"

        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_heatmap_with_palette(self, sample_drawio):
        """使用 blue 调色板"""
        metrics = {"node-a": 0.1, "node-b": 0.5, "node-c": 0.9}
        output_path = sample_drawio + ".blue.drawio"
        try:
            apply_heatmap(sample_drawio, metrics, output_path, "blue", "auto")

            c_a = _read_fillcolor(output_path, "node-a")
            c_b = _read_fillcolor(output_path, "node-b")
            c_c = _read_fillcolor(output_path, "node-c")

            assert c_a == PALETTES["blue"]["low"]
            assert c_b == PALETTES["blue"]["mid"]
            assert c_c == PALETTES["blue"]["high"]
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_heatmap_partial_match(self, sample_drawio):
        """部分节点匹配时，只着色匹配的节点"""
        metrics = {"node-a": 0.2}
        output_path = sample_drawio + ".partial.drawio"
        try:
            apply_heatmap(sample_drawio, metrics, output_path, "default", "auto")

            c_a = _read_fillcolor(output_path, "node-a")
            c_b = _read_fillcolor(output_path, "node-b")
            c_c = _read_fillcolor(output_path, "node-c")

            # node-a 应有颜色 — 唯一值时返回 mid 色
            assert c_a is not None
            assert c_a == PALETTES["default"]["mid"]  # 唯一值，返回 mid 色
            # node-b 和 node-c 没有匹配，不应着色
            assert c_b == "#ffffff" or c_b is None
            assert c_c == "#ffffff" or c_c is None
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_empty_metrics(self, sample_drawio):
        """空 metrics 不应修改文件"""
        output_path = sample_drawio + ".empty.drawio"
        try:
            apply_heatmap(sample_drawio, {}, output_path, "default", "auto")
            assert os.path.exists(output_path)
            # 节点颜色应保持不变
            c_a = _read_fillcolor(output_path, "node-a")
            assert c_a == "#ffffff"
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_heatmap_value_match(self, sample_drawio):
        """通过节点 label (value 属性) 匹配"""
        # 创建一个使用 label 匹配的 drawio
        nodes = [
            ("c1", "cpu_usage", "rounded=1;fillColor=#ffffff;strokeColor=#000000;"),
            ("c2", "memory_usage", "rounded=1;fillColor=#ffffff;strokeColor=#000000;"),
            ("c3", "disk_usage", "rounded=1;fillColor=#ffffff;strokeColor=#000000;"),
        ]
        xml_str = _make_drawio(nodes)
        metrics = {"cpu_usage": 0.2, "memory_usage": 0.5, "disk_usage": 0.8}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".drawio", delete=False, encoding="utf-8") as f:
            f.write(xml_str)
            drawio_path = f.name
        output_path = None
        try:
            output_path = drawio_path + ".val.drawio"
            apply_heatmap(drawio_path, metrics, output_path, "default", "value")
            c_cpu = _read_fillcolor(output_path, "c1")
            c_mem = _read_fillcolor(output_path, "c2")
            c_disk = _read_fillcolor(output_path, "c3")
            assert c_cpu == PALETTES["default"]["low"]
            assert c_mem == PALETTES["default"]["mid"]
            assert c_disk == PALETTES["default"]["high"]
        finally:
            os.unlink(drawio_path)
            if output_path and os.path.exists(output_path):
                os.unlink(output_path)


# ===== 命令行接口测试 =====

class TestCLI:
    def test_cli_help(self):
        """--help 应正常输出"""
        import subprocess
        import sys as _sys
        result = subprocess.run(
            [_sys.executable, "-m", "heatmap", "--help"],
            capture_output=True, text=True,
            cwd=os.path.join(os.path.dirname(__file__), "..", "scripts"),
        )
        assert result.returncode == 0
        assert "用法" in result.stdout or "Usage" in result.stdout or "usage" in result.stdout

    def test_cli_list_palettes(self):
        """--list-palettes 应列出所有调色板"""
        import subprocess
        import sys as _sys
        result = subprocess.run(
            [_sys.executable, "-m", "heatmap", "--list-palettes"],
            capture_output=True, text=True,
            cwd=os.path.join(os.path.dirname(__file__), "..", "scripts"),
        )
        assert result.returncode == 0
        for name in PALETTES:
            assert name in result.stdout