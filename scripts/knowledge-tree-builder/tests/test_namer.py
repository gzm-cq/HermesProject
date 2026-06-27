"""测试 namer 模块 — 含递归命名"""

from unittest.mock import patch

from knowledge_tree_builder.core.namer import _collect_first_texts, name_tree, _name_node


class TestNamer:
    """测试命名模块"""

    @patch("knowledge_tree_builder.core.namer.call_llm", return_value="测试知识点")
    def test_name_calls_llm(self, mock_call_llm) -> None:
        from knowledge_tree_builder.core.namer import name_node

        result = name_node(["这是一个测试知识点"], "leaf",
                           api_url="http://test/api", model="test")
        assert result == "测试知识点"
        mock_call_llm.assert_called_once()

    def test_collect_first_texts(self) -> None:
        nodes = [
            {"type": "leaf", "points": ["text1", "text2"]},
            {"type": "leaf", "points": ["text3"]},
        ]
        texts = _collect_first_texts(nodes)
        assert texts == ["text1", "text3"]

    def test_collect_first_texts_limit(self) -> None:
        nodes = [
            {"type": "leaf", "points": ["t1"]},
            {"type": "leaf", "points": ["t2"]},
            {"type": "leaf", "points": ["t3"]},
            {"type": "leaf", "points": ["t4"]},
        ]
        texts = _collect_first_texts(nodes)
        assert len(texts) == 3

    @patch("knowledge_tree_builder.core.namer.call_llm", return_value="测试叶子")
    def test_name_leaf_node(self, mock_call_llm) -> None:
        """叶子节点命名"""
        node = {"type": "leaf", "points": ["这是一个知识点"]}
        result = _name_node(node, api_url="http://test/api", api_key="", model="test")
        assert result["name"] == "测试叶子"
        assert result["type"] == "leaf"

    @patch("knowledge_tree_builder.core.namer.call_llm", return_value="测试科目")
    def test_name_node_with_children(self, mock_call_llm) -> None:
        """非叶子节点递归命名"""
        node = {
            "type": "node",
            "children": [
                {"type": "leaf", "points": ["子知识点1"]},
                {"type": "leaf", "points": ["子知识点2"]},
            ],
        }
        result = _name_node(node, api_url="http://test/api", api_key="", model="test")
        assert result["name"] == "测试科目"
        assert "children" in result
        assert len(result["children"]) == 2
        # 子节点应已被递归命名
        for child in result["children"]:
            assert "name" in child

    @patch("knowledge_tree_builder.core.namer.call_llm", return_value="根科目")
    def test_name_tree(self, mock_call_llm) -> None:
        """整个树的递归命名"""
        tree = [{
            "type": "node",
            "children": [
                {"type": "leaf", "points": ["点A"]},
                {"type": "leaf", "points": ["点B"]},
            ],
        }]
        result = name_tree(tree, api_url="http://test/api", model="test")
        assert len(result) == 1
        assert result[0]["name"] == "根科目"
