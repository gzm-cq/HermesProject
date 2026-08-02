"""测试 restyle.py — drawio 文件换配色功能"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from restyle import restyle_drawio, _parse_style, _build_style, _normalize_hex, _build_target_map, _detect_source_palette  # noqa: E402
from drawio_generator.palettes import PALETTES  # noqa: E402

TEST_DATA = Path(__file__).resolve().parent / "test_data"
INPUT_FILE = TEST_DATA / "test_restyle_input.drawio"


# ===== 工具函数测试 =====

class TestNormalizeHex:
    def test_uppercase_to_lowercase(self):
        assert _normalize_hex("#DAE8FC") == "#dae8fc"

    def test_short_form(self):
        assert _normalize_hex("#ABC") == "#aabbcc"

    def test_already_normalized(self):
        assert _normalize_hex("#dae8fc") == "#dae8fc"

    def test_no_hash(self):
        assert _normalize_hex("dae8fc") == "#dae8fc"


class TestParseStyle:
    def test_basic_parse(self):
        s = "rounded=0;fillColor=#dae8fc;strokeColor=#6c8ebf;"
        parts = _parse_style(s)
        assert parts["fillColor"] == "#dae8fc"
        assert parts["strokeColor"] == "#6c8ebf"
        assert parts["rounded"] == "0"

    def test_roundtrip(self):
        s = "rounded=0;fillColor=#dae8fc;strokeColor=#6c8ebf;"
        parts = _parse_style(s)
        rebuilt = _build_style(parts)
        # 重建后应包含相同内容
        for k, v in parts.items():
            assert f"{k}={v}" in rebuilt


# ===== 颜色映射测试 =====

class TestBuildTargetMap:
    def test_dark_map_has_all_keys(self):
        """dark palette 的映射应覆盖所有节点颜色"""
        tmap = _build_target_map(PALETTES["dark"])
        assert len(tmap) >= 14  # 7 节点 × 2 (fill+stroke) = 14

    def test_tech_map_has_correct_fill(self):
        """tech palette 的 fill 映射应指向正确颜色"""
        tmap = _build_target_map(PALETTES["tech"])
        # academic 的 node_blue fill = #dae8fc → tech 的 node_blue fill = #EFF6FF
        assert tmap.get("fill:#dae8fc") == "#eff6ff"


# ===== Palette 检测测试 =====

class TestDetectSourcePalette:
    def test_detect_academic(self):
        """输入文件使用 academic 配色，应正确检测"""
        tree = ET.parse(str(INPUT_FILE))
        root = tree.getroot()
        detected = _detect_source_palette(root)
        assert detected == "academic"

    def test_detect_unknown_returns_none(self):
        """没有匹配颜色的空文件应返回 None"""
        xml = '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>'
        root = ET.fromstring(xml)
        detected = _detect_source_palette(root)
        assert detected is None


# ===== 集成测试 =====

class TestRestyleDrawio:
    def test_restyle_to_dark(self, tmp_path):
        """将 academic 配色文件转为 dark 配色"""
        output = tmp_path / "output_dark.drawio"
        count = restyle_drawio(str(INPUT_FILE), "dark", str(output))
        assert count >= 7  # 至少替换了 7 个 mxCell + 背景

        # 验证输出文件存在且可解析
        tree = ET.parse(str(output))
        root = tree.getroot()

        # 验证背景色已更新
        model = root.find(".//mxGraphModel")
        assert model is not None
        assert model.get("background") == "#1A1A2E"

        # 验证节点颜色已更新
        cells = list(root.iter("mxCell"))
        blue_cell = None
        for cell in cells:
            style = cell.get("style", "")
            if "fillColor" in style and "dae8fc" in style:
                blue_cell = cell
                break
        # 原始 academic 的蓝色 fillColor=#dae8fc 应被替换
        for cell in cells:
            style = cell.get("style", "")
            if "fillColor=#dae8fc" in style:
                blue_cell = cell
                break
        assert blue_cell is None, "旧颜色不应再出现"

        # 验证 dark 颜色已出现
        dark_fill_found = False
        for cell in cells:
            style = cell.get("style", "")
            if "fillColor=#1E3A5F" in style:
                dark_fill_found = True
                break
        assert dark_fill_found, "dark 颜色应已替换"

    def test_restyle_to_tech(self, tmp_path):
        """将 academic 配色文件转为 tech 配色"""
        output = tmp_path / "output_tech.drawio"
        count = restyle_drawio(str(INPUT_FILE), "tech", str(output))
        assert count >= 7

        tree = ET.parse(str(output))
        root = tree.getroot()
        cells = list(root.iter("mxCell"))

        # 验证 tech 颜色出现
        tech_fill_found = False
        for cell in cells:
            style = cell.get("style", "")
            if "fillColor=#EFF6FF" in style:
                tech_fill_found = True
                break
        assert tech_fill_found, "tech 的蓝色 fill 应出现"

    def test_stroke_color_updated(self, tmp_path):
        """边的 strokeColor 也应更新"""
        output = tmp_path / "output_stroke.drawio"
        restyle_drawio(str(INPUT_FILE), "dark", str(output))

        tree = ET.parse(str(output))
        root = tree.getroot()
        cells = list(root.iter("mxCell"))

        # academic 的 strokeColor=#6c8ebf 应被替换为 dark 的 #4DA8DA
        old_stroke_found = False
        new_stroke_found = False
        for cell in cells:
            style = cell.get("style", "")
            if "strokeColor=#6c8ebf" in style.lower():
                old_stroke_found = True
            if "strokeColor=#4DA8DA" in style:
                new_stroke_found = True
        assert not old_stroke_found, "旧 stroke 颜色不应再出现"
        assert new_stroke_found, "dark 的 stroke 颜色应出现"

    def test_geometry_unchanged(self, tmp_path):
        """mxGeometry 的布局信息应保持不变"""
        input_tree = ET.parse(str(INPUT_FILE))
        output = tmp_path / "output_geom.drawio"
        restyle_drawio(str(INPUT_FILE), "business", str(output))
        output_tree = ET.parse(str(output))

        input_geoms = [(geo.get("x"), geo.get("y"), geo.get("width"), geo.get("height"))
                       for geo in input_tree.iter("mxGeometry")]
        output_geoms = [(geo.get("x"), geo.get("y"), geo.get("width"), geo.get("height"))
                        for geo in output_tree.iter("mxGeometry")]

        assert input_geoms == output_geoms, "几何信息不应改变"

    def test_fill_color_none_untouched(self, tmp_path):
        """fillColor=none 的 cell 应保持 none"""
        output = tmp_path / "output_none.drawio"
        restyle_drawio(str(INPUT_FILE), "dark", str(output))

        tree = ET.parse(str(output))
        root = tree.getroot()
        none_fill_found = False
        for cell in root.iter("mxCell"):
            style = cell.get("style", "")
            if "fillColor=none" in style:
                none_fill_found = True
                break
        assert none_fill_found, "fillColor=none 应保持不变"

    def test_invalid_palette_returns_negative(self, tmp_path):
        """未知 palette 应返回 -1"""
        output = tmp_path / "output_invalid.drawio"
        # 直接测试 restyle_drawio 返回 -1
        count = restyle_drawio(str(INPUT_FILE), "nonexistent", str(output))
        assert count == -1