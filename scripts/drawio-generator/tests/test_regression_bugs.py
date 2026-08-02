"""drawio-generator 回归测试 — 覆盖历史 Bug 修复。

每个测试方法对应一个独立的 Bug 场景，使用 pytest fixture 自动管理临时文件。
"""

import io
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

from drawio_generator.render import render
from drawio_generator.drawio_renderer import repair_drawio

# Skill CLI 路径（基于 __file__ 推导，兼容 Windows 原生与 WSL）
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_RENDER = os.path.join(_project_root, "scripts", "render.py")


# ---- 共享 Fixture ----

@pytest.fixture
def fixtures():
    """提供测试用的 fix plan 字典集合。"""
    basic = {
        "title": "T", "width": 500, "height": 300,
        "nodes": [
            {"id": "a", "label": "A", "x": 100, "y": 100, "w": 120, "h": 50},
            {"id": "b", "label": "B", "x": 300, "y": 100, "w": 120, "h": 50},
        ],
        "edges": [{"from": "a", "to": "b", "label": "X"}],
    }
    return {
        "basic": basic,
        "br": {**basic, "title": "BR", "format": "svg",
               "nodes": [
                   {"id": "a", "label": "上层<br>子层", "x": 100, "y": 100, "w": 150, "h": 60},
                   {"id": "b", "label": "另一节点", "x": 300, "y": 100, "w": 150, "h": 60},
               ]},
        "cn": {**basic, "title": "CNID",
               "nodes": [
                   {"id": "\u8282\u70b91", "label": "A", "x": 100, "y": 100, "w": 100, "h": 50},
                   {"id": "\u8282\u70b92", "label": "B", "x": 300, "y": 100, "w": 100, "h": 50},
               ]},
        "paper": {**basic, "title": "Paper", "paper_mode": True},
        "pres": {**basic, "title": "Pres", "presentation": True,
                 "nodes": [
                     {"id": "a", "label": "A", "x": 100, "y": 100, "w": 120, "h": 50, "sub_label": "100ms"},
                     {"id": "b", "label": "B", "x": 300, "y": 100, "w": 120, "h": 50},
                 ]},
        "empty": {"title": "Empty", "width": 400, "height": 300, "nodes": [], "edges": []},
        "br_drawio": {
            "title": "BRD", "width": 500, "height": 300,
            "nodes": [
                {"id": "a", "label": "上层<br>子层", "x": 100, "y": 100, "w": 150, "h": 60},
                {"id": "b", "label": "B", "x": 300, "y": 100, "w": 150, "h": 60},
            ],
            "edges": [{"from": "a", "to": "b"}],
        },
    }


def read_text(path):
    """读取文件内容。"""
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---- Bug 1: Extension detection ----

class TestBug1ExtensionDetection:
    """文件扩展名 → 格式推断"""

    def test_ext_svg(self, fixtures, tmp_path):
        out = str(tmp_path / "ext.svg")
        render(fixtures["basic"], out)
        c = read_text(out)
        assert c.strip().startswith("<svg"), "expect svg content"
        assert not c.startswith("<mxfile")

    def test_ext_drawio(self, fixtures, tmp_path):
        out = str(tmp_path / "ext.drawio")
        render(fixtures["basic"], out)
        c = read_text(out)
        assert "<mxfile" in c, "expect drawio content"

    def test_ext_paper_mode_drawio(self, fixtures, tmp_path):
        FIX_PO = {**fixtures["basic"], "title": "PO", "paper_mode": True}
        out = str(tmp_path / "po.drawio")
        render(FIX_PO, out)
        c = read_text(out)
        assert "<mxfile" in c, "paper_mode+.drawio -> drawio"

    def test_ext_format_svg_drawio(self, fixtures, tmp_path):
        FIX_FS = {**fixtures["basic"], "title": "FS", "format": "svg"}
        out = str(tmp_path / "fs.drawio")
        render(FIX_FS, out)
        c = read_text(out)
        assert "<mxfile" in c, "format=svg+.drawio -> drawio"


# ---- Bug 2: <br> in SVG ----

class TestBug2BrInSvg:
    """SVG 渲染中 <br> 标签处理"""

    def test_br_svg_split(self, fixtures, tmp_path):
        out = str(tmp_path / "br.svg")
        render(fixtures["br"], out)
        c = read_text(out)
        assert "\u4e0a\u5c42" in c, "第一行在SVG中"
        assert "\u5b50\u5c42" in c, "第二行在SVG中"
        assert "&lt;br&gt;" not in c, "无字面<br>残留"


# ---- Bug 5: <br> in drawio ----

class TestBug5BrInDrawio:
    """drawio 格式中 <br> 不应被双转义"""

    def test_br_drawio_no_double_escape(self, fixtures, tmp_path):
        out = str(tmp_path / "br.drawio")
        render(fixtures["br_drawio"], out)
        c = read_text(out)
        # ET 写入 value="..." 时会把 <br> 转义为 &lt;br&gt;
        assert "&lt;br&gt;" in c, "XML 中应有 &lt;br&gt;"
        # escape() 会额外转义 & → &amp; 导致 &amp;lt;br&amp;gt;
        assert "&amp;lt;br" not in c, "不应出现双转义 &amp;lt;br"


# ---- Bug 3: Output messages ----

class TestBug3OutputMessages:
    """渲染输出消息以 'Generated:' 开头"""

    def test_msg_svg(self, fixtures, tmp_path):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render(fixtures["br"], str(tmp_path / "msg.svg"))
        assert buf.getvalue().strip().startswith("Generated:")

    def test_msg_drawio(self, fixtures, tmp_path):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render(fixtures["basic"], str(tmp_path / "msg.drawio"))
        assert buf.getvalue().strip().startswith("Generated:")

    def test_msg_paper(self, fixtures, tmp_path):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render(fixtures["paper"], str(tmp_path / "msg_p.svg"))
        assert buf.getvalue().strip().startswith("Generated:")


# ---- Bug 4: Chinese IDs ----

class TestBug4ChineseIds:
    """中文节点 ID 转换为数字 ID"""

    def test_cn_id_mapped_to_numbers(self, fixtures, tmp_path):
        out = str(tmp_path / "cn.drawio")
        render(fixtures["cn"], out)
        c = read_text(out)
        ids = re.findall(r'id="([^"]+)"', c)
        non_num = [i for i in ids if not i.isdigit()]
        assert len(non_num) == 0, f"non-numeric ids: {non_num}"

    def test_cn_id_no_false_positive_warn(self, fixtures, tmp_path):
        out = str(tmp_path / "cn2.drawio")
        render(fixtures["cn"], out)
        _fixed, issues = repair_drawio(out)
        non_ascii_w = [i for i in issues if "\u975eASCII" in str(i)]
        assert len(non_ascii_w) == 0, str(non_ascii_w)


# ---- 回归: 基本正确性 ----

class TestRegressionBasic:
    """基础渲染回归"""

    def test_basic_vertex_edge_count(self, fixtures, tmp_path):
        out = str(tmp_path / "reg_basic.drawio")
        render(fixtures["basic"], out)
        c = read_text(out)
        assert c.count('vertex="1"') == 3, "title + 2 nodes"
        assert c.count('edge="1"') == 1

    def test_paper_mode_svg(self, fixtures, tmp_path):
        out = str(tmp_path / "reg_paper.svg")
        render(fixtures["paper"], out)
        c = read_text(out)
        assert c.strip().startswith("<svg")

    def test_presentation_features(self, fixtures, tmp_path):
        out = str(tmp_path / "reg_pres.drawio")
        render(fixtures["pres"], out)
        c = read_text(out)
        assert "shadow=1" in c
        assert "100ms" in c
        assert "gradientColor" in c

    def test_empty_graph(self, fixtures, tmp_path):
        out = str(tmp_path / "reg_empty.drawio")
        render(fixtures["empty"], out)
        c = read_text(out)
        assert c.count('vertex="1"') == 1, "only title"

    def test_paper_grayscale(self, fixtures, tmp_path):
        out = str(tmp_path / "reg_gray.svg")
        render({**fixtures["paper"], "grayscale": True}, out)
        c = read_text(out)
        assert c.strip().startswith("<svg")


# ---- Skill CLI (deploy-only, skip if not deployed) ----

class TestSkillCli:
    """Skill CLI 集成测试（需要已部署的 render.py）"""

    def test_skill_cli_basic(self, fixtures, tmp_path):
        if not os.path.isfile(SKILL_RENDER):
            pytest.skip("deployed skill not found")
        import json
        json_path = str(tmp_path / "basic.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(fixtures["basic"], f)
        out = str(tmp_path / "cli.drawio")
        r = subprocess.run(
            [sys.executable, SKILL_RENDER, json_path, out],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"stderr: {r.stderr[:60]}"
        assert os.path.isfile(out)

    def test_skill_cli_missing_file(self, tmp_path):
        if not os.path.isfile(SKILL_RENDER):
            pytest.skip("deployed skill not found")
        r = subprocess.run(
            [sys.executable, SKILL_RENDER,
             os.path.join(tmp_path, "nx.json"),
             str(tmp_path / "x.drawio")],
            capture_output=True, text=True,
        )
        assert r.returncode != 0
        assert "Error" in r.stderr
