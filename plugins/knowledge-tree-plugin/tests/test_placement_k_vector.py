"""Regression tests for placement k_vector persistence."""

from unittest.mock import MagicMock, patch


def test_place_new_knowledge_points_passes_embeddings_to_batch_insert(mock_adapter: MagicMock) -> None:
    """New knowledge points must persist their own K vectors, not only parent K."""
    from knowledge_tree_plugin.placement import place_new_knowledge_points

    embeddings = [
        [1.0] + [0.0] * 1023,
        [0.0, 1.0] + [0.0] * 1022,
    ]
    mock_adapter.get_leaf_nodes.return_value = []
    mock_adapter.get_child_nodes.return_value = []
    mock_adapter.get_sibling_points.return_value = []
    mock_adapter.batch_insert_knowledge_points.return_value = [100, 101]
    mock_adapter.get_node_embedding.return_value = None
    mock_adapter.get_placement_count.return_value = 0

    with patch("knowledge_tree_plugin.placement.batch_embed", return_value=embeddings):
        result = place_new_knowledge_points(
            new_points=["知识点A", "知识点B"],
            adapter=mock_adapter,
            session_id="test",
            user_message="test query",
            embed_base_url="http://test",
            embed_model="test-model",
            embed_api_key="",
        )

    assert result["new_nodes"] == 2
    mock_adapter.batch_insert_knowledge_points.assert_called_once_with(
        [("知识点A", "知识点A"), ("知识点B", "知识点B")],
        parent_id=2,
        k_vectors=embeddings,
    )
