"""svgflow 测试 — 解析 .drawio 并生成带蚂蚁线动画的 SVG"""

import os
import tempfile

from svgflow import parse_drawio, generate_svg
from tests._helpers import create_drawio_file as _create_test_drawio


class TestParseDrawio:
    """测试 .drawio 解析"""

    def test_parse_single_node(self):
        """解析单个节点"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            path = f.name
        try:
            _create_test_drawio(
                path,
                nodes=[{"id": "a", "label": "Node A", "x": 50, "y": 100, "w": 150, "h": 60}],
                edges=[],
            )
            nodes, edges = parse_drawio(path)

            assert len(nodes) == 1
            assert nodes[0]["label"] == "Node A"
            assert nodes[0]["x"] == 50
            assert nodes[0]["y"] == 100
            assert nodes[0]["w"] == 150
            assert nodes[0]["h"] == 60
            assert len(edges) == 0
        finally:
            os.unlink(path)

    def test_parse_nodes_and_edges(self):
        """解析节点和边"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            path = f.name
        try:
            _create_test_drawio(
                path,
                nodes=[
                    {"id": "a", "label": "A", "x": 50, "y": 100, "w": 100, "h": 50},
                    {"id": "b", "label": "B", "x": 300, "y": 100, "w": 100, "h": 50},
                ],
                edges=[{"from": "a", "to": "b"}],
            )
            nodes, edges = parse_drawio(path)

            assert len(nodes) == 2
            assert len(edges) == 1
        finally:
            os.unlink(path)

    def test_parse_multiple_edges(self):
        """解析多条边"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            path = f.name
        try:
            _create_test_drawio(
                path,
                nodes=[
                    {"id": "a", "label": "A", "x": 50, "y": 50, "w": 100, "h": 50},
                    {"id": "b", "label": "B", "x": 250, "y": 50, "w": 100, "h": 50},
                    {"id": "c", "label": "C", "x": 450, "y": 50, "w": 100, "h": 50},
                ],
                edges=[{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
            )
            nodes, edges = parse_drawio(path)

            assert len(nodes) == 3
            assert len(edges) == 2
        finally:
            os.unlink(path)


class TestGenerateSvg:
    """测试 SVG 生成"""

    def test_generate_svg_with_animation(self):
        """生成 SVG，验证包含蚂蚁线动画元素"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            drawio_path = f.name
        svg_path = drawio_path + ".svg"
        try:
            _create_test_drawio(
                drawio_path,
                nodes=[
                    {"id": "a", "label": "A", "x": 50, "y": 100, "w": 120, "h": 60},
                    {"id": "b", "label": "B", "x": 320, "y": 125, "w": 120, "h": 60},
                ],
                edges=[{"from": "a", "to": "b"}],
            )
            nodes, edges = parse_drawio(drawio_path)
            generate_svg(nodes, edges, svg_path)

            assert os.path.isfile(svg_path)

            with open(svg_path, encoding="utf-8") as f:
                content = f.read()

            # SVG 基本结构
            assert "<svg" in content
            # animateMotion — marching ants 圆点动画
            assert "<animateMotion" in content
            # stroke-dashoffset — dasharray 流动效果动画
            assert "<animate" in content
            # mpath 引用路径定义
            assert "<mpath" in content
            # defs — path id 引用
            assert '<path id="e' in content
        finally:
            for p in (drawio_path, svg_path):
                if os.path.isfile(p):
                    os.unlink(p)

    def test_generate_svg_no_edges(self):
        """无边的图也能正常生成 SVG"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            drawio_path = f.name
        svg_path = drawio_path + ".svg"
        try:
            _create_test_drawio(
                drawio_path,
                nodes=[{"id": "a", "label": "Solo", "x": 50, "y": 50, "w": 120, "h": 60}],
                edges=[],
            )
            nodes, edges = parse_drawio(drawio_path)
            generate_svg(nodes, edges, svg_path)

            assert os.path.isfile(svg_path)

            with open(svg_path, encoding="utf-8") as f:
                content = f.read()

            assert "<svg" in content
            # 无边时不应有动画元素
            assert "<animateMotion" not in content
        finally:
            for p in (drawio_path, svg_path):
                if os.path.isfile(p):
                    os.unlink(p)

    def test_generate_svg_multiple_edges_parallel_animation(self):
        """多条边并行动画"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            drawio_path = f.name
        svg_path = drawio_path + ".svg"
        try:
            _create_test_drawio(
                drawio_path,
                nodes=[
                    {"id": "a", "label": "A", "x": 50, "y": 50, "w": 120, "h": 60},
                    {"id": "b", "label": "B", "x": 320, "y": 75, "w": 120, "h": 60},
                    {"id": "c", "label": "C", "x": 640, "y": 75, "w": 120, "h": 60},
                ],
                edges=[{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
            )
            nodes, edges = parse_drawio(drawio_path)
            generate_svg(nodes, edges, svg_path)

            with open(svg_path, encoding="utf-8") as f:
                content = f.read()

            # 每条边至少有一个 animateMotion
            count_motion = content.count("<animateMotion")
            assert count_motion >= len(edges), (
                f"Expected >= {len(edges)} animateMotion elements, got {count_motion}"
            )

            # 所有动画使用 dur=2s 和 repeatCount=indefinite (parallel)
            assert 'dur="2s"' in content
            assert 'repeatCount="indefinite"' in content
        finally:
            for p in (drawio_path, svg_path):
                if os.path.isfile(p):
                    os.unlink(p)

    def test_generate_svg_contains_node_labels(self):
        """SVG 包含节点标签"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            drawio_path = f.name
        svg_path = drawio_path + ".svg"
        try:
            _create_test_drawio(
                drawio_path,
                nodes=[
                    {"id": "a", "label": "Frontend", "x": 50, "y": 50, "w": 120, "h": 60},
                    {"id": "b", "label": "Backend", "x": 320, "y": 75, "w": 120, "h": 60},
                ],
                edges=[{"from": "a", "to": "b"}],
            )
            nodes, edges = parse_drawio(drawio_path)
            generate_svg(nodes, edges, svg_path)

            with open(svg_path, encoding="utf-8") as f:
                content = f.read()

            assert "Frontend" in content
            assert "Backend" in content
        finally:
            for p in (drawio_path, svg_path):
                if os.path.isfile(p):
                    os.unlink(p)