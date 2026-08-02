"""自动布局引擎单元测试"""

from drawio_generator.layout import (
    layout_plan, _build_adjacency, _topological_sort,
    _assign_layers, _compute_coordinates, _reduce_crossings,
    _identify_back_edges, _separate_isolated,
    DEFAULT_NODE_W, DEFAULT_NODE_H,
)
from drawio_generator.geometry import (
    _compute_orthogonal_edge, _compute_straight_edge,
    _compute_bezier_edge, compute_edge_path, _clip_line_to_rect,
)


class TestBuildAdjacency:
    """测试邻接表构建"""

    def test_empty_nodes(self):
        adj, indeg = _build_adjacency([], [])
        assert adj == {}
        assert indeg == {}

    def test_no_edges(self):
        nodes = [{"id": "a"}, {"id": "b"}]
        adj, indeg = _build_adjacency(nodes, [])
        assert "a" in adj and "b" in adj
        assert indeg["a"] == 0 and indeg["b"] == 0

    def test_simple_edge(self):
        nodes = [{"id": "a"}, {"id": "b"}]
        edges = [{"from": "a", "to": "b"}]
        adj, indeg = _build_adjacency(nodes, edges)
        assert adj["a"] == ["b"]
        assert adj["b"] == []
        assert indeg["a"] == 0
        assert indeg["b"] == 1

    def test_unknown_node_edge_skipped(self):
        nodes = [{"id": "a"}]
        edges = [{"from": "a", "to": "nonexistent"}]
        adj, indeg = _build_adjacency(nodes, edges)
        assert adj["a"] == []


class TestTopologicalSort:
    """测试拓扑排序"""

    def test_empty_adj(self):
        order, has_cycle = _topological_sort({}, {}, [])
        assert order == []
        assert not has_cycle

    def test_linear_chain(self):
        adj = {"a": ["b"], "b": ["c"], "c": []}
        indeg = {"a": 0, "b": 1, "c": 1}
        nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        order, has_cycle = _topological_sort(adj, indeg, nodes)
        assert order == ["a", "b", "c"]
        assert not has_cycle

    def test_cycle_detected(self):
        adj = {"a": ["b"], "b": ["c"], "c": ["a"]}
        indeg = {"a": 1, "b": 1, "c": 1}
        nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        order, has_cycle = _topological_sort(adj, indeg, nodes)
        assert has_cycle
        # 环中节点应附加在末尾
        assert len(order) == 3

    def test_diamond_graph(self):
        # a → b → d
        # a → c → d
        adj = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}
        indeg = {"a": 0, "b": 1, "c": 1, "d": 2}
        nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]
        order, has_cycle = _topological_sort(adj, indeg, nodes)
        assert not has_cycle
        assert order[0] == "a"
        assert order[-1] == "d"


class TestAssignLayers:
    """测试层级分配"""

    @staticmethod
    def _build_node_map_and_preds(nodes, adj):
        """辅助：构建 node_map 和 predecessors"""
        node_map = {n["id"]: n for n in nodes if "id" in n}
        preds = {}
        for src, targets in adj.items():
            for tgt in targets:
                preds.setdefault(tgt, []).append(src)
        return node_map, preds

    def test_linear_chain(self):
        nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        adj = {"a": ["b"], "b": ["c"], "c": []}
        topo = ["a", "b", "c"]
        nm, preds = self._build_node_map_and_preds(nodes, adj)
        layers = _assign_layers(topo, adj, nm, preds)
        assert layers["a"] == 0
        assert layers["b"] == 1
        assert layers["c"] == 2

    def test_diamond_graph(self):
        nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]
        adj = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}
        topo = ["a", "b", "c", "d"]
        nm, preds = self._build_node_map_and_preds(nodes, adj)
        layers = _assign_layers(topo, adj, nm, preds)
        assert layers["a"] == 0
        assert layers["b"] == 1
        assert layers["c"] == 1
        assert layers["d"] == 2

    def test_single_node(self):
        nodes = [{"id": "a"}]
        adj = {"a": []}
        nm, preds = self._build_node_map_and_preds(nodes, adj)
        layers = _assign_layers(["a"], adj, nm, preds)
        assert layers["a"] == 0


class TestComputeCoordinates:
    """测试坐标计算"""

    @staticmethod
    def _build_node_map_and_preds(nodes, adj):
        node_map = {n["id"]: n for n in nodes if "id" in n}
        preds = {}
        for src, targets in adj.items():
            for tgt in targets:
                preds.setdefault(tgt, []).append(src)
        return node_map, preds

    def test_vertical_layout(self):
        layers = {"a": 0, "b": 1}
        nodes = [{"id": "a"}, {"id": "b"}]
        nm, preds = self._build_node_map_and_preds(nodes, {})
        result = _compute_coordinates(layers, nm, preds, {}, "vertical")
        assert len(result) == 2
        a_node = [n for n in result if n["id"] == "a"][0]
        b_node = [n for n in result if n["id"] == "b"][0]
        assert "x" in a_node and "y" in a_node
        assert "x" in b_node and "y" in b_node
        assert b_node["y"] > a_node["y"]

    def test_horizontal_layout(self):
        layers = {"a": 0, "b": 1}
        nodes = [{"id": "a"}, {"id": "b"}]
        nm, preds = self._build_node_map_and_preds(nodes, {})
        result = _compute_coordinates(layers, nm, preds, {}, "horizontal")
        a_node = [n for n in result if n["id"] == "a"][0]
        b_node = [n for n in result if n["id"] == "b"][0]
        assert b_node["x"] > a_node["x"]

    def test_custom_node_size(self):
        layers = {"a": 0}
        nodes = [{"id": "a", "w": 300, "h": 100}]
        nm, preds = self._build_node_map_and_preds(nodes, {})
        result = _compute_coordinates(layers, nm, preds, {}, "vertical")
        assert result[0]["w"] == 300
        assert result[0]["h"] == 100


class TestReduceCrossings:
    """测试重心启发式层内排序"""

    @staticmethod
    def _build_preds(adj):
        preds = {}
        for src, targets in adj.items():
            for tgt in targets:
                preds.setdefault(tgt, []).append(src)
        return preds

    def test_single_layer_no_change(self):
        groups = {0: ["a", "b"]}
        result = _reduce_crossings(groups, {}, {})
        assert result == {0: ["a", "b"]}

    def test_empty_adj_no_crash(self):
        groups = {0: ["a"], 1: ["b"]}
        result = _reduce_crossings(groups, {}, {})
        assert 0 in result and 1 in result

    def test_crossing_reduced(self):
        groups = {0: ["a", "b"], 1: ["c", "d"]}
        adj = {"a": ["d"], "b": ["c"]}
        preds = self._build_preds(adj)
        result = _reduce_crossings(groups, adj, preds)
        d_idx = result[1].index("d")
        c_idx = result[1].index("c")
        assert d_idx < c_idx, f"期望 d 在 c 前，实际顺序: {result[1]}"

    def test_upward_sweep(self):
        groups = {0: ["a", "b"], 1: ["c", "d"]}
        adj = {"a": ["d"], "b": ["c"]}
        preds = self._build_preds(adj)
        result = _reduce_crossings(groups, adj, preds)
        assert len(result[0]) == 2

    def test_multi_layer_complex(self):
        groups = {0: ["a", "b"], 1: ["c", "d", "e"], 2: ["f", "g"]}
        adj = {"a": ["c", "d"], "b": ["e"], "c": ["f"], "d": ["g"], "e": ["f", "g"]}
        preds = self._build_preds(adj)
        result = _reduce_crossings(groups, adj, preds)
        assert len(result[0]) == 2
        assert len(result[1]) == 3
        assert len(result[2]) == 2
        # 验证所有节点都在
        all_nids = []
        for nids in result.values():
            all_nids.extend(nids)
        assert set(all_nids) == {"a", "b", "c", "d", "e", "f", "g"}


class TestLayoutPlan:
    """测试 layout_plan 主入口"""

    def test_empty_input(self):
        result = layout_plan([], [])
        assert result["nodes"] == []
        assert result["width"] > 0
        assert result["height"] > 0
        assert not result["has_cycle"]
        assert result["back_edges"] == []

    def test_single_node(self):
        result = layout_plan([{"id": "a", "label": "A"}], [])
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "a"
        assert "x" in result["nodes"][0]
        assert "y" in result["nodes"][0]

    def test_linear_vertical(self):
        nodes = [
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B"},
            {"id": "c", "label": "C"},
        ]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
        result = layout_plan(nodes, edges, direction="vertical")
        assert len(result["nodes"]) == 3
        assert not result["has_cycle"]
        # 验证顺序：a → b → c 纵向排列
        a_node = result["nodes"][0]
        b_node = result["nodes"][1]
        c_node = result["nodes"][2]
        assert b_node["y"] > a_node["y"]
        assert c_node["y"] > b_node["y"]

    def test_linear_horizontal(self):
        nodes = [
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B"},
        ]
        edges = [{"from": "a", "to": "b"}]
        result = layout_plan(nodes, edges, direction="horizontal")
        a_node = result["nodes"][0]
        b_node = result["nodes"][1]
        assert b_node["x"] > a_node["x"]

    def test_diamond_graph(self):
        nodes = [
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B"},
            {"id": "c", "label": "C"},
            {"id": "d", "label": "D"},
        ]
        edges = [{"from": "a", "to": "b"}, {"from": "a", "to": "c"},
                 {"from": "b", "to": "d"}, {"from": "c", "to": "d"}]
        result = layout_plan(nodes, edges)
        assert len(result["nodes"]) == 4
        assert not result["has_cycle"]

    def test_cycle_graph(self):
        """环形图应有环标记，但仍生成布局"""
        nodes = [
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B"},
            {"id": "c", "label": "C"},
        ]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"},
                 {"from": "c", "to": "a"}]
        result = layout_plan(nodes, edges)
        assert result["has_cycle"]
        assert len(result["nodes"]) == 3

    def test_default_sizes_filled(self):
        result = layout_plan([{"id": "a", "label": "A"}], [])
        assert result["nodes"][0]["w"] == DEFAULT_NODE_W
        assert result["nodes"][0]["h"] == DEFAULT_NODE_H

    def test_custom_sizes_preserved(self):
        result = layout_plan([{"id": "a", "label": "A", "w": 300, "h": 80}], [])
        assert result["nodes"][0]["w"] == 300
        assert result["nodes"][0]["h"] == 80


class TestIdentifyBackEdges:
    """测试回边识别"""

    def test_no_cycle_no_back_edges(self):
        topo = ["a", "b", "c"]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
        assert _identify_back_edges(topo, edges) == []

    def test_simple_back_edge_detected(self):
        topo = ["a", "b", "c"]
        edges = [{"from": "a", "to": "b"}, {"from": "c", "to": "a"}]  # c→a 是回边
        back = _identify_back_edges(topo, edges)
        assert ("c", "a") in back
        assert len(back) == 1

    def test_cycle_back_edge(self):
        """三节点环：c→a 应为回边"""
        topo = ["a", "b", "c"]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}]
        back = _identify_back_edges(topo, edges)
        assert ("c", "a") in back
        assert len(back) == 1

    def test_self_loop_is_back_edge(self):
        topo = ["a"]
        edges = [{"from": "a", "to": "a"}]
        back = _identify_back_edges(topo, edges)
        assert ("a", "a") in back

    def test_unknown_nodes_skipped(self):
        topo = ["a", "b"]
        edges = [{"from": "a", "to": "x"}]
        assert _identify_back_edges(topo, edges) == []

    def test_layout_plan_returns_back_edges(self):
        """有环时 layout_plan 应返回 back_edges"""
        nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}]
        result = layout_plan(nodes, edges)
        assert result["has_cycle"]
        assert len(result["back_edges"]) >= 1


class TestSeparateIsolated:
    """测试孤立节点分离"""

    def test_all_connected(self):
        """全连通节点 → 无孤立节点"""
        nodes = [{"id": "a"}, {"id": "b"}]
        edges = [{"from": "a", "to": "b"}]
        iso, con = _separate_isolated(nodes, edges)
        assert iso == []
        assert len(con) == 2

    def test_all_isolated(self):
        """无边 → 全孤立"""
        iso, con = _separate_isolated([{"id": "a"}, {"id": "b"}], [])
        assert len(iso) == 2
        assert con == []

    def test_mixed(self):
        """混和：部分节点参与边，部分不参与"""
        nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        edges = [{"from": "a", "to": "b"}]
        iso, con = _separate_isolated(nodes, edges)
        assert len(iso) == 1
        assert iso[0]["id"] == "c"
        assert {n["id"] for n in con} == {"a", "b"}

    def test_node_without_id_not_isolated(self):
        """无 id 的节点不参与分离"""
        iso, con = _separate_isolated([{"id": "a"}, {"x": 1}], [])
        assert len(iso) == 1  # a 是孤立
        assert len(con) == 1  # 无 id 节点进入 connected（会被布局忽略）

    def test_unknown_edge_ref_ignored(self):
        """边引用了不存在的节点 ID → 忽略"""
        nodes = [{"id": "a"}]
        edges = [{"from": "a", "to": "x"}]  # x 不在节点中
        iso, con = _separate_isolated(nodes, edges)
        assert iso == []  # a 在边中出现
        assert len(con) == 1


class TestIsolatedNodeLayout:
    """测试孤立节点布局位置"""

    def test_isolated_above_connected_vertical(self):
        """孤立节点在连通图上方 (vertical)"""
        nodes = [
            {"id": "a", "w": 100, "h": 50},
            {"id": "b", "w": 100, "h": 50},
            {"id": "c", "w": 100, "h": 50},
        ]
        edges = [{"from": "a", "to": "b"}]
        result = layout_plan(nodes, edges, direction="vertical")
        node_map = {n["id"]: n for n in result["nodes"]}
        assert node_map["c"]["y"] < node_map["a"]["y"]
        assert node_map["c"]["y"] < node_map["b"]["y"]
        assert node_map["b"]["y"] > node_map["a"]["y"]  # a→b 连接关系保持

    def test_isolated_left_of_connected_horizontal(self):
        """孤立节点在连通图左侧 (horizontal)"""
        nodes = [
            {"id": "a"},
            {"id": "b"},
            {"id": "c"},
        ]
        edges = [{"from": "b", "to": "c"}]
        result = layout_plan(nodes, edges, direction="horizontal")
        node_map = {n["id"]: n for n in result["nodes"]}
        assert node_map["a"]["x"] < node_map["b"]["x"]
        assert node_map["c"]["x"] > node_map["b"]["x"]

    def test_all_isolated_vertical_row(self):
        """全孤立节点 (vertical)：排成一行"""
        result = layout_plan([{"id": "a"}, {"id": "b"}], [], direction="vertical")
        assert len(result["nodes"]) == 2
        assert result["nodes"][0]["y"] == result["nodes"][1]["y"]
        assert result["nodes"][1]["x"] > result["nodes"][0]["x"]

    def test_all_isolated_horizontal_column(self):
        """全孤立节点 (horizontal)：排成一列"""
        result = layout_plan([{"id": "a"}, {"id": "b"}], [], direction="horizontal")
        assert len(result["nodes"]) == 2
        assert result["nodes"][0]["x"] == result["nodes"][1]["x"]
        assert result["nodes"][1]["y"] > result["nodes"][0]["y"]

    def test_only_isolated_with_label(self):
        """全孤立节点：label 和尺寸保留"""
        result = layout_plan([{"id": "x", "label": "X", "w": 200, "h": 80}], [])
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["label"] == "X"
        assert result["nodes"][0]["w"] == 200
        assert result["nodes"][0]["h"] == 80


class TestAutoDirection:
    """测试布局方向自动检测"""

    def test_deep_chain_detects_vertical(self):
        """深度链：4 层每层 1 节点 → vertical"""
        nodes = [{"id": chr(ord("a") + i)} for i in range(4)]
        edges = [{"from": chr(ord("a") + i), "to": chr(ord("a") + i + 1)} for i in range(3)]
        result = layout_plan(nodes, edges, direction="auto")
        node_map = {n["id"]: n for n in result["nodes"]}
        # 纵向排列：y 递增
        assert node_map["b"]["y"] > node_map["a"]["y"]
        assert node_map["c"]["y"] > node_map["b"]["y"]

    def test_wide_graph_detects_horizontal(self):
        """宽图：1 层扇出到 4 节点 → horizontal"""
        nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}, {"id": "e"}]
        edges = [{"from": "a", "to": "b"}, {"from": "a", "to": "c"},
                 {"from": "a", "to": "d"}, {"from": "a", "to": "e"}]
        result = layout_plan(nodes, edges, direction="auto")
        node_map = {n["id"]: n for n in result["nodes"]}
        # 水平排列：x 递增
        assert node_map["b"]["x"] > node_map["a"]["x"]

    def test_auto_direction_preserves_all_nodes(self):
        """auto 方向不丢节点"""
        nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
        result = layout_plan(nodes, edges, direction="auto")
        assert len(result["nodes"]) == 3


class TestOrthogonalEdge:
    """测试正交边路径计算"""

    def test_horizontal_right(self):
        """目标在右：出口在源右侧，入口在目标左侧"""
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 200, "y": 0, "w": 100, "h": 50}
        pts = _compute_orthogonal_edge(src, tgt)
        assert len(pts) >= 2
        assert pts[0][0] == 100  # src 右边缘

    def test_horizontal_left(self):
        """目标在左：出口在源左侧，入口在目标右侧"""
        src = {"x": 200, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 0, "y": 0, "w": 100, "h": 50}
        pts = _compute_orthogonal_edge(src, tgt)
        assert pts[0][0] == 200  # src 左边缘
        assert pts[-1][0] == 100  # tgt 右边缘

    def test_vertical_down(self):
        """目标在下方：出口在源底部，入口在目标顶部"""
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 0, "y": 200, "w": 100, "h": 50}
        pts = _compute_orthogonal_edge(src, tgt)
        assert pts[0][1] == 50  # src 底部
        assert pts[-1][1] == 200  # tgt 顶部

    def test_vertical_up(self):
        """目标在上方：出口在源顶部，入口在目标底部"""
        src = {"x": 0, "y": 200, "w": 100, "h": 50}
        tgt = {"x": 0, "y": 0, "w": 100, "h": 50}
        pts = _compute_orthogonal_edge(src, tgt)
        assert pts[0][1] == 200  # src 顶部
        assert pts[-1][1] == 50  # tgt 底部

    def test_collinear_horizontal(self):
        """共线水平：目标与源同 y → 2 点直达"""
        src = {"x": 0, "y": 25, "w": 100, "h": 50}
        tgt = {"x": 200, "y": 25, "w": 100, "h": 50}
        pts = _compute_orthogonal_edge(src, tgt)
        assert len(pts) == 2

    def test_collinear_vertical(self):
        """共线垂直：目标与源同 x → 2 点直达"""
        src = {"x": 50, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 50, "y": 200, "w": 100, "h": 50}
        pts = _compute_orthogonal_edge(src, tgt)
        assert len(pts) == 2

    def test_orthogonal_4_points(self):
        """非共线应返回 4 点（含 2 个中间拐点）"""
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 150, "y": 100, "w": 100, "h": 50}
        pts = _compute_orthogonal_edge(src, tgt)
        assert len(pts) == 4


class TestStraightEdge:
    """测试直线边路径计算"""

    def test_horizontal_straight(self):
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 200, "y": 0, "w": 100, "h": 50}
        pts = _compute_straight_edge(src, tgt)
        assert len(pts) == 2
        # 起点应在 src 右边界，终点应在 tgt 左边界
        assert pts[0][0] == 100
        assert pts[-1][0] == 200

    def test_vertical_straight(self):
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 0, "y": 200, "w": 100, "h": 50}
        pts = _compute_straight_edge(src, tgt)
        assert len(pts) == 2
        assert pts[0][1] == 50
        assert pts[-1][1] == 200


class TestBezierEdge:
    """测试贝塞尔曲线边路径计算"""

    def test_returns_4_points(self):
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 200, "y": 0, "w": 100, "h": 50}
        pts = _compute_bezier_edge(src, tgt)
        assert len(pts) == 4
        # (start, cp1, cp2, end)
        assert pts[0][0] == 100  # src 右边界
        assert pts[-1][0] == 200  # tgt 左边界

    def test_horizontal_dominant_control_points(self):
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 300, "y": 10, "w": 100, "h": 50}
        pts = _compute_bezier_edge(src, tgt)
        _, (c1x, c1y), (c2x, c2y), _ = pts
        # 水平主导时，控制点 x 偏移应较大
        assert c1x > pts[0][0]
        assert c2x < pts[-1][0]

    def test_vertical_dominant_control_points(self):
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 10, "y": 300, "w": 100, "h": 50}
        pts = _compute_bezier_edge(src, tgt)
        _, (c1x, c1y), (c2x, c2y), _ = pts
        # 垂直主导时，控制点 y 偏移应较大
        assert c1y > pts[0][1]
        assert c2y < pts[-1][1]


class TestComputeEdgePath:
    """测试 compute_edge_path 分发"""

    def test_orthogonal_dispatch(self):
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 200, "y": 0, "w": 100, "h": 50}
        pts = compute_edge_path(src, tgt, curve="orthogonal")
        assert len(pts) >= 2

    def test_straight_dispatch(self):
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 200, "y": 0, "w": 100, "h": 50}
        pts = compute_edge_path(src, tgt, curve="straight")
        assert len(pts) == 2

    def test_bezier_dispatch(self):
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 200, "y": 0, "w": 100, "h": 50}
        pts = compute_edge_path(src, tgt, curve="bezier")
        assert len(pts) == 4

    def test_default_is_orthogonal(self):
        src = {"x": 0, "y": 0, "w": 100, "h": 50}
        tgt = {"x": 200, "y": 100, "w": 100, "h": 50}
        pts = compute_edge_path(src, tgt)
        assert len(pts) == 4  # orthogonal default


class TestClipLineToRect:
    """测试线段裁剪到矩形"""

    def test_clip_right(self):
        """向右射线与右边界相交"""
        ix, iy = _clip_line_to_rect(50, 25, 200, 25, 0, 0, 100, 50)
        assert ix == 100
        assert iy == 25

    def test_clip_left(self):
        """向左射线与左边界相交"""
        ix, iy = _clip_line_to_rect(50, 25, -100, 25, 0, 0, 100, 50)
        assert ix == 0
        assert iy == 25

    def test_clip_down(self):
        """向下射线与下边界相交"""
        ix, iy = _clip_line_to_rect(50, 25, 50, 200, 0, 0, 100, 50)
        assert ix == 50
        assert iy == 50

    def test_clip_up(self):
        """向上射线与上边界相交"""
        ix, iy = _clip_line_to_rect(50, 25, 50, -100, 0, 0, 100, 50)
        assert ix == 50
        assert iy == 0

    def test_zero_direction_returns_origin(self):
        """方向为零向量返回原点"""
        ix, iy = _clip_line_to_rect(50, 25, 50, 25, 0, 0, 100, 50)
        assert ix == 50
        assert iy == 25
