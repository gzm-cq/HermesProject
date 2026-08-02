"""buildup 测试 — 解析 .drawio → 拓扑排序 → 帧划分 → HTML 生成。"""

import os
import tempfile

from buildup import (
    parse_drawio,
    topological_sort,
    assign_depths,
    build_frames,
    generate_html,
)
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
                nodes=[{"id": "a", "label": "Gateway", "x": 50, "y": 100, "w": 150, "h": 60}],
                edges=[],
            )
            nodes, edges = parse_drawio(path)

            assert len(nodes) == 1
            assert nodes[0]["label"] == "Gateway"
            assert nodes[0]["x"] == 50
            assert nodes[0]["y"] == 100
            assert nodes[0]["w"] == 150
            assert nodes[0]["h"] == 60
            assert len(edges) == 0
        finally:
            os.unlink(path)

    def test_parse_nodes_and_edges(self):
        """解析带依赖的节点和边"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            path = f.name
        try:
            _create_test_drawio(
                path,
                nodes=[
                    {"id": "a", "label": "Frontend", "x": 50, "y": 100, "w": 120, "h": 50},
                    {"id": "b", "label": "Backend", "x": 300, "y": 100, "w": 120, "h": 50},
                    {"id": "c", "label": "Database", "x": 550, "y": 100, "w": 120, "h": 50},
                ],
                edges=[
                    {"from": "a", "to": "b"},
                    {"from": "b", "to": "c"},
                ],
            )
            nodes, edges = parse_drawio(path)

            assert len(nodes) == 3
            assert len(edges) == 2
            # 按标签查找（节点 ID 是 drawio cell ID，非逻辑 ID）
            labels = [n["label"] for n in nodes]
            assert "Frontend" in labels
            assert "Backend" in labels
            assert "Database" in labels
        finally:
            os.unlink(path)


class TestTopologicalSort:
    """测试拓扑排序"""

    def test_simple_chain(self):
        """链式依赖：a → b → c"""
        nodes = [
            {"id": "a", "x": 50, "y": 100, "w": 100, "h": 50},
            {"id": "b", "x": 250, "y": 100, "w": 100, "h": 50},
            {"id": "c", "x": 450, "y": 100, "w": 100, "h": 50},
        ]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]

        order = topological_sort(nodes, edges)
        # a 必须在 b 前，b 必须在 c 前
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_diamond_dependency(self):
        """菱形依赖：a → b, a → c, b → d, c → d"""
        nodes = [
            {"id": "a", "x": 200, "y": 50, "w": 100, "h": 50},
            {"id": "b", "x": 50, "y": 200, "w": 100, "h": 50},
            {"id": "c", "x": 350, "y": 200, "w": 100, "h": 50},
            {"id": "d", "x": 200, "y": 350, "w": 100, "h": 50},
        ]
        edges = [
            {"from": "a", "to": "b"},
            {"from": "a", "to": "c"},
            {"from": "b", "to": "d"},
            {"from": "c", "to": "d"},
        ]

        order = topological_sort(nodes, edges)
        # a 是第一个
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        # d 是最后一个
        assert order.index("d") > order.index("b")
        assert order.index("d") > order.index("c")

    def test_no_edges_sorted_by_y(self):
        """无边节点按 y 位置排序"""
        nodes = [
            {"id": "bottom", "x": 100, "y": 300, "w": 100, "h": 50},
            {"id": "top", "x": 100, "y": 50, "w": 100, "h": 50},
            {"id": "middle", "x": 100, "y": 150, "w": 100, "h": 50},
        ]
        edges = []

        order = topological_sort(nodes, edges)
        # top < middle < bottom (by y)
        assert order.index("top") < order.index("middle")
        assert order.index("middle") < order.index("bottom")


class TestDepthAssignment:
    """测试深度分配"""

    def test_chain_depths(self):
        """链式深度的正确性"""
        nodes = [
            {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "b", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "c", "x": 0, "y": 0, "w": 100, "h": 50},
        ]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
        order = topological_sort(nodes, edges)
        depth = assign_depths(order, edges)

        assert depth["a"] == 0
        assert depth["b"] == 1
        assert depth["c"] == 2

    def test_diamond_depths(self):
        """菱形依赖的深度"""
        nodes = [
            {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "b", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "c", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "d", "x": 0, "y": 0, "w": 100, "h": 50},
        ]
        edges = [
            {"from": "a", "to": "b"},
            {"from": "a", "to": "c"},
            {"from": "b", "to": "d"},
            {"from": "c", "to": "d"},
        ]
        order = topological_sort(nodes, edges)
        depth = assign_depths(order, edges)

        assert depth["a"] == 0
        assert depth["b"] == 1
        assert depth["c"] == 1
        assert depth["d"] == 2


class TestFrameBuilding:
    """测试帧构建"""

    def test_chain_frames(self):
        """链式依赖产生 3 帧"""
        nodes = [
            {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "b", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "c", "x": 0, "y": 0, "w": 100, "h": 50},
        ]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
        order = topological_sort(nodes, edges)
        depth = assign_depths(order, edges)
        frames = build_frames(nodes, edges, order, depth)

        assert len(frames) == 3
        assert "a" in frames[0]
        assert "b" in frames[1]
        assert "c" in frames[2]

    def test_no_edges_multi_frame(self):
        """无边图自动分散到多帧"""
        nodes = [
            {"id": "a", "x": 100, "y": 50, "w": 100, "h": 50},
            {"id": "b", "x": 100, "y": 150, "w": 100, "h": 50},
            {"id": "c", "x": 100, "y": 250, "w": 100, "h": 50},
            {"id": "d", "x": 100, "y": 350, "w": 100, "h": 50},
        ]
        edges = []
        order = topological_sort(nodes, edges)
        depth = assign_depths(order, edges)
        frames = build_frames(nodes, edges, order, depth)

        # 应有多个帧（4 个节点，max 6 帧，分散到对应帧）
        assert len(frames) > 1
        # 所有节点都被分配
        all_nids = set()
        for nids in frames.values():
            all_nids.update(nids)
        assert all_nids == {"a", "b", "c", "d"}


class TestGenerateHtml:
    """测试 HTML 生成"""

    def test_generate_html_chain(self):
        """链式依赖生成 HTML，验证结构和动画"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            drawio_path = f.name
        html_path = drawio_path + ".html"
        try:
            _create_test_drawio(
                drawio_path,
                nodes=[
                    {"id": "a", "label": "A", "x": 50, "y": 100, "w": 120, "h": 60},
                    {"id": "b", "label": "B", "x": 300, "y": 100, "w": 120, "h": 60},
                    {"id": "c", "label": "C", "x": 550, "y": 100, "w": 120, "h": 60},
                ],
                edges=[
                    {"from": "a", "to": "b"},
                    {"from": "b", "to": "c"},
                ],
            )
            nodes, edges = parse_drawio(drawio_path)
            node_map = {n["id"]: n for n in nodes}
            order = topological_sort(nodes, edges)
            depth = assign_depths(order, edges)
            frames = build_frames(nodes, edges, order, depth)

            generate_html(nodes, edges, frames, depth, node_map, html_path)

            assert os.path.isfile(html_path)

            with open(html_path, encoding="utf-8") as f:
                content = f.read()

            # HTML 基本结构
            assert "<!DOCTYPE html>" in content
            assert "<svg" in content
            assert "</svg>" in content
            # CSS 关键帧动画
            assert "@keyframes reveal-node" in content
            assert "@keyframes dim-node" in content
            # 节点标签
            assert "A" in content
            assert "B" in content
            assert "C" in content
            # 3 帧动画类
            assert "f0-node" in content
            assert "f1-node" in content
            assert "f2-node" in content
            # 步骤指示器
            assert "step-bar" in content
            assert "step-dot" in content
        finally:
            for p in (drawio_path, html_path):
                if os.path.isfile(p):
                    os.unlink(p)

    def test_generate_html_single_node(self):
        """单个节点生成 HTML"""
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            drawio_path = f.name
        html_path = drawio_path + ".html"
        try:
            _create_test_drawio(
                drawio_path,
                nodes=[{"id": "a", "label": "Solo", "x": 50, "y": 50, "w": 120, "h": 60}],
                edges=[],
            )
            nodes, edges = parse_drawio(drawio_path)
            node_map = {n["id"]: n for n in nodes}
            order = topological_sort(nodes, edges)
            depth = assign_depths(order, edges)
            frames = build_frames(nodes, edges, order, depth)

            generate_html(nodes, edges, frames, depth, node_map, html_path)

            assert os.path.isfile(html_path)

            with open(html_path, encoding="utf-8") as f:
                content = f.read()

            assert "<!DOCTYPE html>" in content
            assert "<svg" in content
            assert "Solo" in content
            # 至少有一帧
            assert "f0-node" in content
        finally:
            for p in (drawio_path, html_path):
                if os.path.isfile(p):
                    os.unlink(p)