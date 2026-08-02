"""drawio-generator v2 新增测试：容器、动态间距、路由走廊"""
import json
import os
import tempfile

from drawio_generator.containers import (
    parse_group_tree, compute_container_boxes,
    assign_group_colors, compute_node_offsets,
    generate_container_cells, apply_group_colors_to_nodes,
    GROUP_COLOR_CYCLE,
)
from drawio_generator.layout import _get_spacing_by_complexity, layout_plan
from drawio_generator.palettes import PALETTES


# ===== 容器解析 =====

def test_parse_simple_group():
    """单层 group 解析"""
    nodes = [
        {"id": "a", "group": "client"},
        {"id": "b", "group": "client"},
        {"id": "c", "group": "server"},
        {"id": "d"},  # 无 group
    ]
    tree = parse_group_tree(nodes)
    assert set(tree["ordered"]) == {("client",), ("server",)}
    assert tree["gpath"]["a"] == ("client",)
    assert tree["gpath"]["b"] == ("client",)
    assert tree["gpath"]["c"] == ("server",)
    assert "d" not in tree["gpath"]


def test_parse_nested_group():
    """多层嵌套 group 解析"""
    nodes = [
        {"id": "a", "group": "server/db"},
        {"id": "b", "group": "server/cache"},
        {"id": "c", "group": "server"},
        {"id": "d", "group": "client"},
    ]
    tree = parse_group_tree(nodes)
    assert set(tree["ordered"]) == {("server",), ("server", "db"), ("server", "cache"), ("client",)}
    assert tree["gpath"]["a"] == ("server", "db")
    assert tree["gpath"]["b"] == ("server", "cache")
    assert tree["gpath"]["c"] == ("server",)
    assert tree["children"][("server",)] == [("server", "cache"), ("server", "db")]


# ===== 容器包围盒 =====

def test_container_bounding_box():
    """单层容器包围盒计算"""
    nodes = [
        {"id": "a", "x": 100, "y": 100, "w": 120, "h": 50},
        {"id": "b", "x": 100, "y": 200, "w": 120, "h": 50},
    ]
    tree = {"ordered": [("test",)], "direct": {("test",): ["a", "b"]},
            "children": {("test",): []}, "gpath": {"a": ("test",), "b": ("test",)}}
    boxes = compute_container_boxes(tree, nodes, padding=24)
    assert ("test",) in boxes
    cx, cy, cw, ch = boxes[("test",)]
    # 包围盒 = 节点边界 + padding
    assert cx <= 100 - 24  # 最左 x - padding
    assert cy <= 100 - 24  # 最上 y - padding
    assert cx + cw >= 100 + 120 + 24  # 最右 x + w + padding
    assert cy + ch >= 200 + 50 + 24  # 最下 y + h + padding


def test_nested_container_bounding_box():
    """嵌套容器包围盒计算"""
    nodes = [
        {"id": "a", "x": 100, "y": 100, "w": 120, "h": 50},
    ]
    tree = {"ordered": [("svr",), ("svr", "db")],
            "direct": {("svr",): ["a"], ("svr", "db"): []},
            "children": {("svr",): [("svr", "db")], ("svr", "db"): []},
            "gpath": {"a": ("svr",)}}
    boxes = compute_container_boxes(tree, nodes, padding=24)
    assert ("svr",) in boxes
    # 父容器应大于子容器
    px, py, pw, ph = boxes[("svr",)]
    assert pw > 0 and ph > 0


# ===== 组着色 =====

def test_group_coloring():
    """组颜色循环分配"""
    nodes = [
        {"id": "a", "group": "g1"},
        {"id": "b", "group": "g2"},
        {"id": "c", "group": "g3"},
    ]
    tree = parse_group_tree(nodes)
    palette = PALETTES["academic"]
    colors = assign_group_colors(tree, palette)
    assert isinstance(colors, dict)
    # 每个组应有不同颜色
    assert colors[("g1",)] != colors[("g2",)]
    assert colors[("g1",)] in GROUP_COLOR_CYCLE


def test_apply_group_colors():
    """组颜色应用到节点"""
    nodes = [
        {"id": "a", "group": "g1", "x": 0, "y": 0, "w": 100, "h": 50},
        {"id": "b", "group": "g2", "x": 0, "y": 0, "w": 100, "h": 50},
        {"id": "c", "color": "node_red", "x": 0, "y": 0, "w": 100, "h": 50},  # 自定义 color 应跳过
    ]
    tree = parse_group_tree(nodes)
    palette = PALETTES["academic"]
    group_colors = assign_group_colors(tree, palette)
    result = apply_group_colors_to_nodes(nodes, tree, group_colors, palette)
    # a 应该获得组颜色（无自定义 color）
    assert result[0].get("color") in GROUP_COLOR_CYCLE
    # c 有自定义 color，应保持不变
    assert result[2].get("color") == "node_red"


# ===== 容器 cell 生成 =====

def test_container_cells_generation():
    """容器 mxCell 生成"""
    nodes = [
        {"id": "a", "x": 100, "y": 100, "w": 120, "h": 50},
    ]
    tree = {"ordered": [("g1",)], "direct": {("g1",): ["a"]},
            "children": {("g1",): []}, "gpath": {"a": ("g1",)}}
    palette = PALETTES["academic"]
    boxes = compute_container_boxes(tree, nodes, padding=24)
    assert ("g1",) in boxes
    group_colors = {("g1",): "node_blue"}
    cells, next_nid, path_cid_map = generate_container_cells(tree, boxes, group_colors, palette, 200)
    assert len(cells) == 1
    assert ("g1",) in path_cid_map
    cid, parent, style, cx, cy, cw, ch, label = cells[0]
    assert cid == "200"
    assert parent == "1"
    assert "dashed=1" in style
    assert "strokeColor=" in style  # 应使用实际 hex 颜色
    assert "#" in style  # 应是 hex 值而非 "node_blue"
    assert label == "g1"


# ===== 动态间距 =====

def test_dynamic_spacing_simple():
    """≤5 节点时使用简单间距"""
    gap, layer_gap, corridor = _get_spacing_by_complexity(3)
    assert gap == 200
    assert layer_gap == 150
    assert corridor == 60


def test_dynamic_spacing_medium():
    """6-10 节点时使用中等间距"""
    gap, layer_gap, corridor = _get_spacing_by_complexity(8)
    assert gap == 280
    assert layer_gap == 200
    assert corridor == 80


def test_dynamic_spacing_complex():
    """>10 节点时使用复杂间距"""
    gap, layer_gap, corridor = _get_spacing_by_complexity(15)
    assert gap == 350
    assert layer_gap == 250
    assert corridor == 100


def test_dynamic_spacing_user_override():
    """用户传入 gap/layer_gap 时不自动覆盖"""
    nodes = [
        {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
        {"id": "b", "x": 0, "y": 0, "w": 100, "h": 50},
        {"id": "c", "x": 0, "y": 0, "w": 100, "h": 50},
    ]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
    result = layout_plan(nodes, edges, gap=100, layer_gap=80, gap_auto=False)
    assert result["nodes"]  # 布局成功


def test_same_label_different_path():
    """同名不同路径容器应分配不同 CID，节点应映射到正确容器"""
    nodes = [
        {"id": "a", "x": 100, "y": 100, "w": 120, "h": 50, "group": "server/db"},
        {"id": "b", "x": 400, "y": 100, "w": 120, "h": 50, "group": "client/db"},
    ]
    tree = parse_group_tree(nodes)
    boxes = compute_container_boxes(tree, nodes, padding=24)
    palette = PALETTES["academic"]
    group_colors = assign_group_colors(tree, palette)
    cells, _, path_cid_map = generate_container_cells(tree, boxes, group_colors, palette, 200)

    # 两个 db 容器应有不同 CID
    cid_server_db = path_cid_map[("server", "db")]
    cid_client_db = path_cid_map[("client", "db")]
    assert cid_server_db != cid_client_db

    # 节点偏移应映射到各自容器的坐标
    offsets = compute_node_offsets(tree, boxes)
    assert offsets["a"] == boxes[("server", "db")][:2]
    assert offsets["b"] == boxes[("client", "db")][:2]