"""测试 validator 模块 — 含 validate_tree / judge_subcluster_structure"""

from unittest.mock import patch

from knowledge_tree_builder.core.validator import (
    _collect_node_texts,
    _validate_node,
    validate_tree,
    judge_subcluster_structure,
    binary_verify,
)


class TestValidator:
    """测试结构校验模块"""

    def test_collect_node_texts_leaf(self) -> None:
        node = {"type": "leaf", "points": ["p1", "p2"]}
        texts = _collect_node_texts(node)
        assert texts == ["p1", "p2"]

    def test_collect_node_texts_node(self) -> None:
        node = {
            "type": "node",
            "children": [
                {"type": "leaf", "points": ["p1", "p2"]},
                {"type": "leaf", "points": ["p3"]},
            ],
        }
        texts = _collect_node_texts(node)
        assert texts == ["p1", "p2", "p3"]

    def test_validate_node_leaf(self) -> None:
        node = {"type": "leaf", "points": ["p1"]}
        result = _validate_node(node, api_url="", api_key="", model="")
        assert result["type"] == "leaf"
        assert "structure" not in result

    def test_validate_node_single_child(self) -> None:
        node = {
            "type": "node",
            "children": [{"type": "leaf", "points": ["p1"]}],
        }
        result = _validate_node(node, api_url="", api_key="", model="")
        assert result["structure"] == "single"

    @patch("knowledge_tree_builder.core.validator.call_llm", return_value="A")
    def test_judge_parallel(self, mock_call_llm) -> None:
        """平行结构判断"""
        result = judge_subcluster_structure(
            {"0": ["知识A"], "1": ["知识B"]},
            api_url="http://test/api", model="test",
        )
        assert result == "parallel"

    @patch("knowledge_tree_builder.core.validator.call_llm", return_value="B")
    def test_judge_hierarchical(self, mock_call_llm) -> None:
        """上下位关系判断"""
        result = judge_subcluster_structure(
            {"0": ["知识A"], "1": ["知识B"]},
            api_url="http://test/api", model="test",
        )
        assert result == "hierarchical"

    @patch("knowledge_tree_builder.core.validator.call_llm", return_value="判据：理论还是实践")
    def test_binary_verify(self, mock_call_llm) -> None:
        """二分校验返回判据"""
        result = binary_verify(
            {"0": ["理论知识点"], "1": ["实践知识点"]},
            api_url="http://test/api", model="test",
        )
        assert "criterion" in result
        assert "理论还是实践" in result["criterion"]

    def test_validate_node_noise(self) -> None:
        """噪声节点跳过校验"""
        node = {"type": "leaf", "points": ["噪声"], "noise": True}
        result = _validate_node(node, api_url="", api_key="", model="")
        assert result["structure"] == "noise"

    @patch("knowledge_tree_builder.core.validator.call_llm", return_value="B")
    def test_validate_tree_hierarchical(self, mock_call_llm) -> None:
        """上下位关系的树校验应包含二分判据"""
        tree = [{
            "type": "node",
            "children": [
                {"type": "leaf", "points": ["理论知识点A", "理论知识点B"]},
                {"type": "leaf", "points": ["实践知识点"]},
            ],
        }]
        # 第一次返回 B（上下位），第二次返回二分判据
        mock_call_llm.side_effect = ["B", "判据：理论还是实践"]
        result = validate_tree(tree, api_url="http://test/api", api_key="", model="test")
        assert len(result) == 1
        assert result[0]["structure"] == "hierarchical"
        assert "binary_criterion" in result[0]

    @patch("knowledge_tree_builder.core.validator.call_llm", return_value="A")
    def test_validate_tree(self, mock_call_llm) -> None:
        """整个树的校验"""
        tree = [{
            "type": "node",
            "children": [
                {"type": "leaf", "points": ["点A"]},
                {"type": "leaf", "points": ["点B"]},
            ],
        }]
        result = validate_tree(tree, api_url="http://test/api", model="test")
        assert len(result) == 1
        assert "structure" in result[0]
