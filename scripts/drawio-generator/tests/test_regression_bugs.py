#!/usr/bin/env python3
"""drawio-generator 回归测试 — 覆盖历史 Bug 修复"""
import json
import os
import re
import sys
import tempfile
import io
import subprocess
import pytest
from contextlib import redirect_stdout

from drawio_generator.render import render
from drawio_generator.drawio_renderer import repair_drawio

# Skill CLI 路径（仅部署环境可用）
SKILL_RENDER = "/root/.hermes/skills/diagramming/drawio-generator/scripts/render.py"

NG = 0
NP = 0


def test(name, ok, detail=""):
    global NG, NP
    if ok:
        NP += 1
        print(f"  [+] {name}")
    else:
        NG += 1
        print(f"  [x] {name}  --  {detail}")


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# Prepare test fixtures
FIXTURES_DIR = tempfile.mkdtemp(prefix="drawio_test_")


def wf(path, content):
    full = os.path.join(FIXTURES_DIR, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(content, f)
    return full


FIX_BASIC = {"title": "T", "width": 500, "height": 300,
    "nodes": [{"id": "a", "label": "A", "x": 100, "y": 100, "w": 120, "h": 50},
              {"id": "b", "label": "B", "x": 300, "y": 100, "w": 120, "h": 50}],
    "edges": [{"from": "a", "to": "b", "label": "X"}]}

FIX_BR = {**FIX_BASIC, "title": "BR", "format": "svg",
    "nodes": [{"id": "a", "label": "上层<br>子层", "x": 100, "y": 100, "w": 150, "h": 60},
              {"id": "b", "label": "另一节点", "x": 300, "y": 100, "w": 150, "h": 60}]}

FIX_CN = {**FIX_BASIC, "title": "CNID",
    "nodes": [{"id": "\u8282\u70b91", "label": "A", "x": 100, "y": 100, "w": 100, "h": 50},
              {"id": "\u8282\u70b92", "label": "B", "x": 300, "y": 100, "w": 100, "h": 50}]}

FIX_PAPER = {**FIX_BASIC, "title": "Paper", "paper_mode": True}

FIX_PRES = {**FIX_BASIC, "title": "Pres", "presentation": True,
    "nodes": [{"id": "a", "label": "A", "x": 100, "y": 100, "w": 120, "h": 50, "sub_label": "100ms"},
              {"id": "b", "label": "B", "x": 300, "y": 100, "w": 120, "h": 50}]}

FIX_EMPTY = {"title": "Empty", "width": 400, "height": 300, "nodes": [], "edges": []}

OUT = os.path.join(FIXTURES_DIR, "out")
os.makedirs(OUT, exist_ok=True)

f_basic = wf("basic.json", FIX_BASIC)
f_cn = wf("cn.json", FIX_CN)
f_br = wf("br.json", FIX_BR)


class TestRegressionBugs:
    """Bug 1-4 回归测试 + 集成测试"""

    # ==== Bug 1: Extension detection ====
    def test_ext_svg(self):
        out = os.path.join(OUT, "ext.svg")
        render(FIX_BASIC, out)
        c = read_text(out)
        assert c.strip().startswith("<svg"), "expect svg content"
        assert not c.startswith("<mxfile")

    def test_ext_drawio(self):
        out = os.path.join(OUT, "ext.drawio")
        render(FIX_BASIC, out)
        c = read_text(out)
        assert "<mxfile" in c, "expect drawio content"

    def test_ext_paper_mode_drawio(self):
        FIX_PO = {**FIX_BASIC, "title": "PO", "paper_mode": True}
        out = os.path.join(OUT, "po.drawio")
        render(FIX_PO, out)
        c = read_text(out)
        assert "<mxfile" in c, "paper_mode+.drawio -> drawio"

    def test_ext_format_svg_drawio(self):
        FIX_FS = {**FIX_BASIC, "title": "FS", "format": "svg"}
        out = os.path.join(OUT, "fs.drawio")
        render(FIX_FS, out)
        c = read_text(out)
        assert "<mxfile" in c, "format=svg+.drawio -> drawio"

    # ==== Bug 2: <br> in SVG ====
    def test_br_svg_split(self):
        out = os.path.join(OUT, "br.svg")
        render(FIX_BR, out)
        c = read_text(out)
        assert "\u4e0a\u5c42" in c, "第一行在SVG中"
        assert "\u5b50\u5c42" in c, "第二行在SVG中"
        assert "&lt;br&gt;" not in c, "无字面<br>残留"

    # ==== Bug 5: <br> in drawio ====
    FIX_BR_DRAWIO = {"title": "BRD", "width": 500, "height": 300,
        "nodes": [{"id": "a", "label": "上层<br>子层", "x": 100, "y": 100, "w": 150, "h": 60},
                  {"id": "b", "label": "B", "x": 300, "y": 100, "w": 150, "h": 60}],
        "edges": [{"from": "a", "to": "b"}]}

    def test_br_drawio_no_double_escape(self):
        """drawio 格式：<br> 不应被双转义"""
        out = os.path.join(OUT, "br.drawio")
        render(self.FIX_BR_DRAWIO, out)
        c = read_text(out)
        # ET 写入 value="..." 时会把 <br> 转义为 &lt;br&gt;
        assert "&lt;br&gt;" in c, "XML 中应有 &lt;br&gt;"
        # escape() 会额外转义 & → &amp; 导致 &amp;lt;br&amp;gt;
        assert "&amp;lt;br" not in c, "不应出现双转义 &amp;lt;br"

    # ==== Bug 3: Output messages ====
    def test_msg_svg(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render(FIX_BR, os.path.join(OUT, "msg.svg"))
        assert buf.getvalue().strip().startswith("Generated:")

    def test_msg_drawio(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render(FIX_BASIC, os.path.join(OUT, "msg.drawio"))
        assert buf.getvalue().strip().startswith("Generated:")

    def test_msg_paper(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render(FIX_PAPER, os.path.join(OUT, "msg_p.svg"))
        assert buf.getvalue().strip().startswith("Generated:")

    # ==== Bug 4: Chinese IDs ====
    def test_cn_id_mapped_to_numbers(self):
        out = os.path.join(OUT, "cn.drawio")
        render(FIX_CN, out)
        c = read_text(out)
        ids = re.findall(r'id="([^"]+)"', c)
        non_num = [i for i in ids if not i.isdigit()]
        assert len(non_num) == 0, f"non-numeric ids: {non_num}"

    def test_cn_id_no_false_positive_warn(self):
        out = os.path.join(OUT, "cn2.drawio")
        render(FIX_CN, out)
        _fixed, issues = repair_drawio(out)
        non_ascii_w = [i for i in issues if "\u975eASCII" in str(i)]
        assert len(non_ascii_w) == 0, str(non_ascii_w)

    # ==== Regression ====
    def test_basic_vertex_edge_count(self):
        out = os.path.join(OUT, "reg_basic.drawio")
        render(FIX_BASIC, out)
        c = read_text(out)
        assert c.count('vertex="1"') == 3, "title + 2 nodes"
        assert c.count('edge="1"') == 1

    def test_paper_mode_svg(self):
        out = os.path.join(OUT, "reg_paper.svg")
        render(FIX_PAPER, out)
        c = read_text(out)
        assert c.strip().startswith("<svg")

    def test_presentation_features(self):
        out = os.path.join(OUT, "reg_pres.drawio")
        render(FIX_PRES, out)
        c = read_text(out)
        assert "shadow=1" in c
        assert "100ms" in c
        assert "gradientColor" in c

    def test_empty_graph(self):
        out = os.path.join(OUT, "reg_empty.drawio")
        render(FIX_EMPTY, out)
        c = read_text(out)
        assert c.count('vertex="1"') == 1, "only title"

    def test_paper_grayscale(self):
        out = os.path.join(OUT, "reg_gray.svg")
        render({**FIX_PAPER, "grayscale": True}, out)
        c = read_text(out)
        assert c.strip().startswith("<svg")

    # ==== Skill CLI (deploy-only) ====
    def test_skill_cli_basic(self):
        if not os.path.isfile(SKILL_RENDER):
            pytest.skip("deployed skill not found")
        out = os.path.join(OUT, "cli.drawio")
        r = subprocess.run(["python3", SKILL_RENDER, f_basic, out],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"stderr: {r.stderr[:60]}"
        assert os.path.isfile(out)

    def test_skill_cli_missing_file(self):
        if not os.path.isfile(SKILL_RENDER):
            pytest.skip("deployed skill not found")
        r = subprocess.run(
            ["python3", SKILL_RENDER, "/tmp/nx.json", os.path.join(OUT, "x.drawio")],
            capture_output=True, text=True)
        assert r.returncode != 0
        assert "Error" in r.stderr
