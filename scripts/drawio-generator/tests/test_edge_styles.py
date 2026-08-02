"""edge_styles 单元测试"""

from drawio_generator.edge_styles import (
    apply_flow_animation,
    check_arrowhead_gap,
    distribute_ports,
    get_base_edge_style,
)


# ===== A. 基础边样式 =====

def test_base_edge_style():
    """默认 orthogonal 基础样式"""
    style = get_base_edge_style()
    assert "edgeStyle=orthogonalEdgeStyle" in style
    assert "rounded=1" in style
    assert "orthogonalLoop=1" in style
    assert "jettySize=auto" in style
    assert "html=1" in style
    assert "labelBackgroundColor=#ffffff" in style


def test_base_edge_style_orthogonal_explicit():
    """显式指定 orthogonal"""
    style = get_base_edge_style(edge_style="orthogonal")
    assert "edgeStyle=orthogonalEdgeStyle" in style


# ===== B. flowAnimation =====

def test_flow_animation():
    """启用 flowAnimation"""
    style = "endArrow=classic;strokeWidth=1;"
    result = apply_flow_animation(style, enabled=True)
    assert "flowAnimation=1" in result


def test_flow_animation_disabled():
    """禁用 flowAnimation 时不追加"""
    style = "endArrow=classic;strokeWidth=1;"
    result = apply_flow_animation(style, enabled=False)
    assert "flowAnimation" not in result


def test_flow_animation_idempotent():
    """已含 flowAnimation 时不重复追加"""
    style = "endArrow=classic;flowAnimation=1;"
    result = apply_flow_animation(style, enabled=True)
    assert result.count("flowAnimation") == 1


# ===== C. edgeports 算法 =====

def test_distribute_ports_single():
    """单条水平边：端口位于中心"""
    nodes = [
        {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
        {"id": "b", "x": 200, "y": 0, "w": 100, "h": 50},
    ]
    edges = [{"from": "a", "to": "b"}]

    result = distribute_ports(nodes, edges)

    assert ("a", "b") in result
    exitX, exitY, entryX, entryY = result[("a", "b")]

    # a→b：水平向右，出口在右侧 (exitX=1, exitY=0.5)，入口在左侧 (entryX=0, entryY=0.5)
    assert exitX == 1.0
    assert exitY == 0.5
    assert entryX == 0.0
    assert entryY == 0.5


def test_distribute_ports_multi():
    """多条边从同一个源节点出去：同侧边均匀分布"""
    nodes = [
        {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
        {"id": "b", "x": 200, "y": -30, "w": 100, "h": 50},
        {"id": "c", "x": 200, "y": 30, "w": 100, "h": 50},
    ]
    edges = [
        {"from": "a", "to": "b"},
        {"from": "a", "to": "c"},
    ]

    result = distribute_ports(nodes, edges)

    # 两个边出口都在 a 的右侧，N=2，均匀分布
    assert ("a", "b") in result
    assert ("a", "c") in result

    # 出口都在右侧 (exitX=1)
    assert result[("a", "b")][0] == 1.0
    assert result[("a", "c")][0] == 1.0

    # 出口 exitY 均匀分布在 [0,1] 之间
    exitY_values = {result[("a", "b")][1], result[("a", "c")][1]}
    assert len(exitY_values) == 2  # 两个不同的 exitY
    for y in exitY_values:
        assert 0 <= y <= 1

    # 入口都在各自目标的左侧 (entryX=0)
    assert result[("a", "b")][2] == 0.0
    assert result[("a", "c")][2] == 0.0

    # 入口 entryY 在中心 (N=1, 单条边入边)
    assert result[("a", "b")][3] == 0.5
    assert result[("a", "c")][3] == 0.5


def test_distribute_ports_vertical():
    """垂直边：从上到下，出口在底部，入口在顶部"""
    nodes = [
        {"id": "top", "x": 0, "y": 0, "w": 100, "h": 50},
        {"id": "bot", "x": 0, "y": 200, "w": 100, "h": 50},
    ]
    edges = [{"from": "top", "to": "bot"}]

    result = distribute_ports(nodes, edges)

    assert ("top", "bot") in result
    exitX, exitY, entryX, entryY = result[("top", "bot")]

    # top→bot：垂直向下，出口在底部 (exitX=0.5, exitY=1)，入口在顶部 (entryX=0.5, entryY=0)
    assert exitX == 0.5
    assert exitY == 1.0
    assert entryX == 0.5
    assert entryY == 0.0


def test_distribute_ports_unknown_node():
    """未知节点 id 被跳过"""
    nodes = [
        {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
    ]
    edges = [{"from": "a", "to": "nonexistent"}]

    result = distribute_ports(nodes, edges)
    assert len(result) == 0


# ===== D. 箭头头间距检查 =====

def test_arrowhead_gap():
    """两条边箭头头距离过近时发出警告"""
    paths = [
        [(0, 0), (100, 100)],
        [(0, 50), (100, 100)],
    ]
    warnings = check_arrowhead_gap(paths, min_gap=20)
    assert len(warnings) >= 1
    assert "间距过近" in warnings[0]


def test_arrowhead_gap_no_warning():
    """两条边距离足够远时无警告"""
    paths = [
        [(0, 0), (100, 0)],
        [(0, 200), (100, 200)],
    ]
    warnings = check_arrowhead_gap(paths, min_gap=20)
    assert len(warnings) == 0


def test_arrowhead_gap_single_edge():
    """单条边不触发检查"""
    paths = [[(0, 0), (100, 100)]]
    warnings = check_arrowhead_gap(paths)
    assert len(warnings) == 0


def test_arrowhead_gap_empty():
    """空列表不触发检查"""
    warnings = check_arrowhead_gap([])
    assert len(warnings) == 0