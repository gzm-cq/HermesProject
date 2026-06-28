"""placement 模块测试 — 增量放置流程。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestPlaceNewKnowledgePoints:
    """place_new_knowledge_points 测试。"""

    @patch("knowledge_tree_plugin.placement.batch_embed")
    def test_new_nodes_inserted(
        self,
        mock_embed: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        """新知识点成功插入，并且批内只更新一次父 K 向量。"""
        from knowledge_tree_plugin.placement import place_new_knowledge_points

        mock_embed.return_value = [
            [1.0] + [0.0] * 1023,
            [0.0, 1.0] + [0.0] * 1022,
        ]
        mock_adapter.get_leaf_nodes.return_value = []
        mock_adapter.get_child_nodes.return_value = []
        mock_adapter.get_sibling_points.return_value = []
        mock_adapter.batch_insert_knowledge_points.return_value = [100, 101]
        mock_adapter.get_node_embedding.return_value = None
        mock_adapter.get_placement_count.return_value = 0

        result = place_new_knowledge_points(
            new_points=["知识点A", "知识点B"],
            adapter=mock_adapter,
            session_id="test",
            user_message="test query",
            embed_base_url="http://test",
            embed_model="test-model",
            embed_api_key="",
        )

        assert result["total"] == 2
        assert result["new_nodes"] == 2
        assert result["dedup_merged"] == 0
        assert result["conflicts"] == 0
        assert mock_adapter.batch_insert_knowledge_points.call_count == 1
        mock_embed.assert_called_once()
        assert mock_adapter.get_child_nodes.call_count >= 1
        mock_adapter.get_sibling_points.assert_not_called()
        mock_adapter.update_k_vector.assert_called_once()

    @patch("knowledge_tree_plugin.placement.batch_embed")
    def test_dedup_merged(
        self,
        mock_embed: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        """重复知识点被合并，dedup 复用已计算 embedding。"""
        from knowledge_tree_plugin.placement import place_new_knowledge_points

        mock_embed.return_value = [[0.1] * 1024]
        mock_adapter.get_leaf_nodes.return_value = [
            {"id": 10, "name": "重复知识点", "k_vector": [0.1] * 1024},
        ]
        mock_adapter.get_child_nodes.return_value = []

        result = place_new_knowledge_points(
            new_points=["重复知识点"],
            adapter=mock_adapter,
            session_id="test",
            user_message="test",
            embed_base_url="http://test",
            embed_model="test-model",
            embed_api_key="",
        )

        assert result["total"] == 1
        assert result["dedup_merged"] == 1
        assert result["new_nodes"] == 0
        mock_adapter.insert_node.assert_not_called()
        mock_adapter.update_k_vector.assert_not_called()
        mock_embed.assert_called_once()

    @patch("knowledge_tree_plugin.placement.batch_embed")
    def test_embedding_failure(
        self,
        mock_embed: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        """embedding 全部失败时标记 errors。"""
        from knowledge_tree_plugin.placement import place_new_knowledge_points

        mock_embed.return_value = None

        result = place_new_knowledge_points(
            new_points=["知识点A"],
            adapter=mock_adapter,
            session_id="test",
            user_message="test",
            embed_base_url="http://test",
            embed_model="test-model",
            embed_api_key="",
        )

        assert result["total"] == 1
        assert result["errors"] == 1

    def test_empty_points(self, mock_adapter: MagicMock) -> None:
        """空列表直接返回。"""
        from knowledge_tree_plugin.placement import place_new_knowledge_points

        result = place_new_knowledge_points(
            new_points=[],
            adapter=mock_adapter,
            session_id="test",
            user_message="test",
            embed_base_url="http://test",
            embed_model="test-model",
            embed_api_key="",
        )
        assert result["total"] == 0

    @patch("knowledge_tree_plugin.placement.batch_embed")
    def test_conflict_detected(
        self,
        mock_embed: MagicMock,
        mock_adapter: MagicMock,
    ) -> None:
        """矛盾检测触发 review 插入。"""
        from knowledge_tree_plugin.placement import place_new_knowledge_points

        mock_embed.return_value = [[0.1] * 1024]
        mock_adapter.get_leaf_nodes.return_value = []
        mock_adapter.get_child_nodes.return_value = [
            {"id": 10, "name": "旧知识点", "k_vector": [0.1] * 1024},
        ]
        mock_adapter.get_node_embedding.return_value = None
        mock_adapter.get_placement_count.return_value = 0

        mock_adapter.batch_insert_knowledge_points.return_value = [100]



        result = place_new_knowledge_points(

            new_points=["不能使用旧知识点"],

            adapter=mock_adapter,

            session_id="test",

            user_message="test",

            embed_base_url="http://test",

            embed_model="test-model",

            embed_api_key="",

        )



        assert result["conflicts"] == 1

        assert result["new_nodes"] == 1  # 矛盾不阻止插入

        mock_adapter.insert_review.assert_called_once()



class TestUpdateKVector:
    """_update_k_vector 测试。"""

    def test_first_placement_sets_k_vector(self, mock_adapter: MagicMock) -> None:
        """首次放置用新 embedding 作为 K 向量。"""
        from knowledge_tree_plugin.placement import _update_k_vector

        mock_adapter.get_node_embedding.return_value = None
        mock_adapter.get_placement_count.return_value = 0

        _update_k_vector(mock_adapter, 1, [0.5] * 1024)

        mock_adapter.update_k_vector.assert_called_once_with(
            node_id=1,
            k_vector=[0.5] * 1024,
            placement_count=1,
        )

    def test_ema_update(self, mock_adapter: MagicMock) -> None:
        """后续放置走指数移动平均。"""
        from knowledge_tree_plugin.placement import _update_k_vector

        old_k = [1.0] * 1024
        new_emb = [2.0] * 1024
        mock_adapter.get_node_embedding.return_value = old_k
        mock_adapter.get_placement_count.return_value = 5

        _update_k_vector(mock_adapter, 1, new_emb, alpha_max=0.1)

        # alpha = min(1.0/6, 0.1) = 0.1
        # new_k = 0.9 * 1.0 + 0.1 * 2.0 = 1.1
        call_args = mock_adapter.update_k_vector.call_args[1]
        assert call_args["node_id"] == 1
        assert call_args["k_vector"][0] == 1.1
        assert call_args["placement_count"] == 6
