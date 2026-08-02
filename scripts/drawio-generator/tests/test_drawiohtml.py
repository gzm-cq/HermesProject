"""drawiohtml.py 测试"""
import json
import os
import tempfile
from drawio_generator.render import render

from drawiohtml import _parse_drawio, _render_page_svg, _generate_html


def _create_test_drawio(nodes, edges, path):
    """生成测试 .drawio 文件"""
    plan = {
        "title": "Test",
        "width": 500, "height": 300,
        "nodes": nodes,
        "edges": edges,
    }
    render(plan, path)


def test_parse_single_page():
    """解析单页 .drawio"""
    with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False, mode="w") as f:
        path = f.name
    try:
        _create_test_drawio(
            [{"id": "a", "label": "A", "x": 50, "y": 50, "w": 100, "h": 50}],
            [],
            path
        )
        pages = _parse_drawio(path)
        assert len(pages) == 1
        assert pages[0]["name"] is not None
        assert len(pages[0]["nodes"]) >= 1
    finally:
        os.unlink(path)


def test_parse_nodes_and_edges():
    """解析节点和边"""
    with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False, mode="w") as f:
        path = f.name
    try:
        _create_test_drawio(
            [
                {"id": "a", "label": "A", "x": 50, "y": 50, "w": 100, "h": 50},
                {"id": "b", "label": "B", "x": 250, "y": 50, "w": 100, "h": 50},
            ],
            [{"from": "a", "to": "b", "label": "conn"}],
            path
        )
        pages = _parse_drawio(path)
        assert len(pages) == 1
        # 至少有一个边
        edge_count = len(pages[0].get("edges", []))
        assert edge_count >= 1
    finally:
        os.unlink(path)


def test_render_svg_has_elements():
    """SVG 渲染包含 rect 和 path 元素"""
    nodes = [
        {"id": "a", "label": "A", "x": 50, "y": 50, "w": 100, "h": 50},
        {"id": "b", "label": "B", "x": 250, "y": 50, "w": 100, "h": 50},
    ]
    edges = [{"from": "a", "to": "b", "label": "conn"}]
    page = {"id": "1", "name": "Test", "nodes": nodes, "edges": edges}
    svg = _render_page_svg(page, 0)
    assert "<rect" in svg
    assert "<path" in svg or "M" in svg


def test_generate_html_has_tabs():
    """HTML 包含页面切换标签"""
    pages = [{"id": "1", "name": "Page-1", "nodes": [], "edges": []}]
    svgs = ["<svg></svg>"]
    html = _generate_html(pages, svgs)
    assert "switchPage" in html
    assert "Page-1" in html
    assert "search" in html


def test_cli_help():
    """CLI --help 输出"""
    import subprocess
    import sys
    # 基于 __file__ 推导项目根目录，兼容 Windows 原生与 WSL
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _script = os.path.join(_project_root, "scripts", "drawiohtml.py")
    r = subprocess.run(
        [sys.executable, _script, "--help"],
        capture_output=True, text=True, cwd=_project_root
    )
    assert r.returncode == 0
    assert ".drawio" in r.stdout or "input" in r.stdout