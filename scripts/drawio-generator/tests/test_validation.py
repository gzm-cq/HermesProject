"""P6 布局质量校验单元测试 — 边穿过节点、边交叉、布局评分"""

from drawio_generator.validator import (
    check_edge_through_vertex,
    check_edge_crossings,
    score_layout,
    validate_plan,
)


class TestCheckEdgeThroughVertex:
    """测试边穿过节点检测"""

    def test_no_through_vertex(self):
        """边不穿过任何节点"""
        nodes = [
            {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "b", "x": 300, "y": 0, "w": 100, "h": 50},
        ]
        edges = [{"from": "a", "to": "b"}]
        result = check_edge_through_vertex(nodes, edges)
        assert result == []

    def test_detects_through_vertex(self):
        """边穿过中间节点"""
        nodes = [
            {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "b", "x": 200, "y": 0, "w": 100, "h": 50},  # 中间节点
            {"id": "c", "x": 400, "y": 0, "w": 100, "h": 50},
        ]
        edges = [{"from": "a", "to": "c"}]
        result = check_edge_through_vertex(nodes, edges)
        assert len(result) == 1
        assert result[0][0] == "a"
        assert result[0][1] == "c"
        assert result[0][2] == "b"

    def test_does_not_flag_endpoints(self):
        """端点节点不被标记为穿过"""
        nodes = [
            {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "b", "x": 200, "y": 0, "w": 100, "h": 50},
        ]
        edges = [{"from": "a", "to": "b"}]
        result = check_edge_through_vertex(nodes, edges)
        assert result == []

    def test_multiple_through_vertices(self):
        """一条边穿过多个节点"""
        nodes = [
            {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "b", "x": 200, "y": 0, "w": 80, "h": 50},
            {"id": "c", "x": 320, "y": 0, "w": 80, "h": 50},
            {"id": "d", "x": 440, "y": 0, "w": 100, "h": 50},
        ]
        edges = [{"from": "a", "to": "d"}]
        result = check_edge_through_vertex(nodes, edges)
        assert len(result) == 2


class TestCheckEdgeCrossings:
    """测试边交叉检测"""

    def test_no_crossings(self):
        """两条平行水平边，不交叉"""
        nodes = [
            {"id": "a", "x": 128, "y": 384, "w": 64, "h": 64},
            {"id": "b", "x": 288, "y": 384, "w": 64, "h": 64},
            {"id": "c", "x": 128, "y": 224, "w": 64, "h": 64},
            {"id": "d", "x": 288, "y": 224, "w": 64, "h": 64},
        ]
        edges = [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}]
        result = check_edge_crossings(nodes, edges)
        assert result == []

    def test_detects_crossing(self):
        """两条边交叉"""
        nodes = [
            {"id": "a", "x": 128, "y": 224, "w": 64, "h": 64},
            {"id": "b", "x": 288, "y": 384, "w": 64, "h": 64},
            {"id": "c", "x": 288, "y": 224, "w": 64, "h": 64},
            {"id": "d", "x": 128, "y": 384, "w": 64, "h": 64},
        ]
        edges = [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}]
        result = check_edge_crossings(nodes, edges)
        assert len(result) == 1

    def test_shared_endpoint_not_crossing(self):
        """共享端点的边不视为交叉"""
        nodes = [
            {"id": "a", "x": 128, "y": 224, "w": 64, "h": 64},
            {"id": "b", "x": 288, "y": 224, "w": 64, "h": 64},
            {"id": "c", "x": 288, "y": 384, "w": 64, "h": 64},
        ]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
        result = check_edge_crossings(nodes, edges)
        assert result == []


class TestScoreLayout:
    """测试布局质量评分"""

    def test_score_layout(self):
        """布局质量评分返回正确结构"""
        nodes = [
            {"id": "a", "x": 128, "y": 224, "w": 64, "h": 64},
            {"id": "b", "x": 288, "y": 384, "w": 64, "h": 64},
            {"id": "c", "x": 288, "y": 224, "w": 64, "h": 64},
            {"id": "d", "x": 128, "y": 384, "w": 64, "h": 64},
        ]
        edges = [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}]
        result = score_layout(nodes, edges)
        assert isinstance(result["score"], (int, float))
        assert isinstance(result["through_vertex"], int)
        assert isinstance(result["crossings"], int)
        assert isinstance(result["total_length"], (int, float))
        assert result["grade"] in ("优秀", "良好", "一般", "较差")

    def test_score_layout_excellent(self):
        """简单布局应得优秀评分"""
        nodes = [
            {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
            {"id": "b", "x": 300, "y": 0, "w": 100, "h": 50},
        ]
        edges = [{"from": "a", "to": "b"}]
        result = score_layout(nodes, edges)
        assert result["through_vertex"] == 0
        assert result["crossings"] == 0
        assert result["score"] < 0.1
        assert result["grade"] == "优秀"


class TestValidatePlanLayout:
    """测试 validate_plan 中的布局检测集成"""

    def test_validate_layout_disabled(self):
        """validate_layout 为 False 时不应执行布局检测"""
        plan = {
            "title": "Test",
            "nodes": [
                {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
                {"id": "b", "x": 200, "y": 0, "w": 100, "h": 50},
                {"id": "c", "x": 400, "y": 0, "w": 100, "h": 50},
            ],
            "edges": [{"from": "a", "to": "c"}],
            "validate_layout": False,
        }
        issues = validate_plan(plan)
        layout_warnings = [i for i in issues if i[0] == "warning" and i[1] == "layout"]
        assert len(layout_warnings) == 0

    def test_validate_layout_enabled(self):
        """validate_layout 为 True 时应执行布局检测"""
        plan = {
            "title": "Test",
            "nodes": [
                {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50},
                {"id": "b", "x": 200, "y": 0, "w": 100, "h": 50},
                {"id": "c", "x": 400, "y": 0, "w": 100, "h": 50},
            ],
            "edges": [{"from": "a", "to": "c"}],
            "validate_layout": True,
        }
        issues = validate_plan(plan)
        layout_warnings = [i for i in issues if i[0] == "warning" and i[1] == "layout"]
        assert len(layout_warnings) > 0