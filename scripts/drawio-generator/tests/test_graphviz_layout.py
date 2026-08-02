"""Graphviz 可选布局引擎测试"""
import pytest

from drawio_generator.graphviz_layout import layout_plan_graphviz, is_available


@pytest.mark.skipif(not is_available(), reason="graphviz not installed")
def test_graphviz_vertical_layout():
    """Graphviz 垂直布局产生合理坐标"""
    nodes = [
        {"id": "a", "label": "A", "w": 160, "h": 60},
        {"id": "b", "label": "B", "w": 160, "h": 60},
        {"id": "c", "label": "C", "w": 160, "h": 60},
    ]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
    result = layout_plan_graphviz(nodes, edges, direction="vertical")
    assert len(result["nodes"]) == 3
    assert result["width"] > 0
    assert result["height"] > 0
    # 垂直布局：a 应在 b 上方，b 在 c 上方
    ys = {n["id"]: n["y"] for n in result["nodes"]}
    assert ys["a"] < ys["b"] < ys["c"]


@pytest.mark.skipif(not is_available(), reason="graphviz not installed")
def test_graphviz_horizontal_layout():
    """Graphviz 水平布局产生合理坐标"""
    nodes = [
        {"id": "a", "label": "A", "w": 160, "h": 60},
        {"id": "b", "label": "B", "w": 160, "h": 60},
        {"id": "c", "label": "C", "w": 160, "h": 60},
    ]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
    result = layout_plan_graphviz(nodes, edges, direction="horizontal")
    xs = {n["id"]: n["x"] for n in result["nodes"]}
    assert xs["a"] < xs["b"] < xs["c"]


@pytest.mark.skipif(not is_available(), reason="graphviz not installed")
def test_graphviz_preserves_custom_size():
    """Graphviz 布局保留用户传入的节点尺寸"""
    nodes = [
        {"id": "a", "w": 200, "h": 80},
    ]
    result = layout_plan_graphviz(nodes, [], direction="vertical")
    assert result["nodes"][0]["w"] == 200
    assert result["nodes"][0]["h"] == 80


@pytest.mark.skipif(not is_available(), reason="graphviz not installed")
def test_graphviz_empty_nodes():
    """空节点列表返回安全默认值"""
    result = layout_plan_graphviz([], [], direction="vertical")
    assert result["nodes"] == []
    assert result["width"] == 80  # padding 40 * 2


@pytest.mark.skipif(not is_available(), reason="graphviz not installed")
def test_graphviz_isolated_nodes():
    """孤立节点也应获得坐标"""
    nodes = [
        {"id": "a", "w": 160, "h": 60},
        {"id": "b", "w": 160, "h": 60},
    ]
    edges = []  # 无边
    result = layout_plan_graphviz(nodes, edges)
    assert len(result["nodes"]) == 2
    for n in result["nodes"]:
        assert "x" in n
        assert "y" in n


def test_graphviz_is_available():
    """is_available 返回布尔值"""
    assert isinstance(is_available(), bool)
