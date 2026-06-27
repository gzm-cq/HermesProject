"""测试 writer 模块 — 含 mock DB"""

from unittest.mock import MagicMock, patch

from knowledge_tree_builder.core.writer import _map_node_type, write_tree


class TestWriter:
    """测试写入模块"""

    def test_map_node_type_subject(self) -> None:
        node = {"type": "node", "children": [{"type": "leaf", "points": ["p1"]}]}
        assert _map_node_type(node) == "subject"

    def test_map_node_type_knowledge_point(self) -> None:
        node = {"type": "leaf", "points": ["p1"]}
        assert _map_node_type(node) == "knowledge_point"

    def test_map_node_type_empty_children(self) -> None:
        node = {"type": "node", "children": []}
        assert _map_node_type(node) == "subject"

    def test_write_tree_empty(self) -> None:
        """空树写入"""
        mock_db = MagicMock()
        stats = write_tree([], mock_db)
        assert stats["nodes"] == 0

    def test_write_tree_single_leaf(self) -> None:
        """单叶子节点写入"""
        mock_db = MagicMock()
        mock_db.insert_node.return_value = 1

        tree = [
            {"name": "测试科目", "type": "leaf", "points": ["测试知识点"]}
        ]
        stats = write_tree(tree, mock_db)
        assert stats["nodes"] >= 1
        mock_db.insert_node.assert_called()

    def test_write_tree_with_nested(self) -> None:
        """多层树写入"""
        mock_db = MagicMock()
        mock_db.insert_node.return_value = 1

        tree = [{
            "name": "根科目",
            "type": "node",
            "structure": "parallel",
            "children": [
                {"name": "子科A", "type": "leaf", "points": ["点A1", "点A2"]},
                {"name": "子科B", "type": "leaf", "points": ["点B1"]},
            ],
        }]
        stats = write_tree(tree, mock_db)
        assert stats["nodes"] >= 3
        assert stats["points"] >= 3

    @patch("knowledge_tree_builder.core.writer.DatabaseAdapter")
    def test_write_tree_with_dedup(self, MockDB) -> None:
        """带去重的增量写入"""
        mock_db = MagicMock()
        mock_db.insert_node.return_value = 1
        mock_db.get_leaf_nodes.return_value = [
            {"id": 1, "k_vector": [0.1, 0.2, 0.3]},
        ]

        def fake_cosine(a, b):
            return 0.96  # 超过阈值 0.95，视为重复

        def fake_embed(texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

        from knowledge_tree_builder.core.writer import write_tree_with_dedup
        result = write_tree_with_dedup(
            ["新知识点"],
            mock_db,
            source_article_id=5,
            embed_fn=fake_embed,
            cosine_similarity_fn=fake_cosine,
            dedup_threshold=0.95,
        )
        assert result["merged_ids"] == [1]
        mock_db.update_source_ids.assert_called_with(1, 5)
