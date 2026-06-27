"""backfill_k_vectors 脚本测试。

覆盖: 空结果 / dry-run / 正常回填 / embedding 失败 / 数量不匹配 / 多 text 去重。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from knowledge_tree_builder.scripts.backfill_k_vectors import backfill_k_vectors


@pytest.fixture
def mock_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.cursor = MagicMock()
    return adapter


class TestBackfillKVectorsEmpty:
    """空结果场景。"""

    def test_no_nodes_to_backfill(self, mock_adapter: MagicMock) -> None:
        """k_vector 全部已填充时返回全零。"""
        mock_adapter.cursor.fetchall.return_value = []

        stats = backfill_k_vectors(mock_adapter, dry_run=False, embed_api_key="test")

        assert stats == {"total": 0, "filled": 0, "errors": 0}
        mock_adapter.update_k_vector.assert_not_called()


class TestBackfillKVectorsDryRun:
    """dry-run 模式。"""

    def test_dry_run_counts_but_no_writes(self, mock_adapter: MagicMock) -> None:
        """dry-run 返回 total 但不调用 update_k_vector。"""
        mock_adapter.cursor.fetchall.return_value = [
            (1, "text A"),
            (2, "text B"),
            (3, "text C"),
        ]

        stats = backfill_k_vectors(mock_adapter, dry_run=True, embed_api_key="test")

        assert stats["total"] == 3
        assert stats["filled"] == 0
        assert stats["errors"] == 0
        mock_adapter.update_k_vector.assert_not_called()


class TestBackfillKVectorsNormal:
    """正常回填场景。"""

    @patch("knowledge_tree_builder.scripts.backfill_k_vectors.batch_embed")
    def test_all_nodes_filled(
        self, mock_embed: MagicMock, mock_adapter: MagicMock
    ) -> None:
        """全部成功回填。"""
        mock_adapter.cursor.fetchall.return_value = [
            (10, "text A"),
            (20, "text B"),
        ]
        mock_embed.return_value = [[0.1] * 1024, [0.2] * 1024]

        stats = backfill_k_vectors(
            mock_adapter, dry_run=False, batch_size=10, embed_api_key="test"
        )

        assert stats == {"total": 2, "filled": 2, "errors": 0}
        assert mock_adapter.update_k_vector.call_count == 2
        mock_adapter.update_k_vector.assert_any_call(
            node_id=10, k_vector=[0.1] * 1024, placement_count=0
        )
        mock_adapter.update_k_vector.assert_any_call(
            node_id=20, k_vector=[0.2] * 1024, placement_count=0
        )

    @patch("knowledge_tree_builder.scripts.backfill_k_vectors.batch_embed")
    def test_batch_size_respected(
        self, mock_embed: MagicMock, mock_adapter: MagicMock
    ) -> None:
        """batch_size 控制分批调用次数。"""
        rows = [(i, f"text {i}") for i in range(1, 6)]  # 5 个节点
        mock_adapter.cursor.fetchall.return_value = rows
        mock_embed.return_value = [[0.1] * 1024] * 3  # 每批 3 个

        stats = backfill_k_vectors(
            mock_adapter, dry_run=False, batch_size=3, embed_api_key="test"
        )

        # 2 批: 第一批 3 个，第二批 2 个（但 mock 返回 3 个会触发数量校验跳过）
        # 第一批: 3 embed == 3 texts → 3 filled
        # 第二批: 3 embed != 2 texts → 2 errors (fail-soft)
        assert stats["total"] == 5
        assert stats["filled"] == 3
        assert stats["errors"] == 2


class TestBackfillKVectorsFailures:
    """embedding 失败场景。"""

    @patch("knowledge_tree_builder.scripts.backfill_k_vectors.batch_embed")
    def test_embed_returns_none(
        self, mock_embed: MagicMock, mock_adapter: MagicMock
    ) -> None:
        """batch_embed 返回 None 时整批计为 error。"""
        mock_adapter.cursor.fetchall.return_value = [
            (1, "text A"),
            (2, "text B"),
        ]
        mock_embed.return_value = None

        stats = backfill_k_vectors(
            mock_adapter, dry_run=False, embed_api_key="test"
        )

        assert stats == {"total": 2, "filled": 0, "errors": 2}
        mock_adapter.update_k_vector.assert_not_called()

    @patch("knowledge_tree_builder.scripts.backfill_k_vectors.batch_embed")
    def test_embed_count_mismatch(
        self, mock_embed: MagicMock, mock_adapter: MagicMock
    ) -> None:
        """batch_embed 返回数量不匹配时 fail-soft 跳过整批。"""
        mock_adapter.cursor.fetchall.return_value = [
            (1, "text A"),
            (2, "text B"),
            (3, "text C"),
        ]
        # 只返回 2 个 embedding（期望 3 个）
        mock_embed.return_value = [[0.1] * 1024, [0.2] * 1024]

        stats = backfill_k_vectors(
            mock_adapter, dry_run=False, batch_size=10, embed_api_key="test"
        )

        assert stats == {"total": 3, "filled": 0, "errors": 3}
        mock_adapter.update_k_vector.assert_not_called()

    @patch("knowledge_tree_builder.scripts.backfill_k_vectors.batch_embed")
    def test_embed_raises_exception(
        self, mock_embed: MagicMock, mock_adapter: MagicMock
    ) -> None:
        """batch_embed 抛异常时整批计为 error。"""
        mock_adapter.cursor.fetchall.return_value = [
            (1, "text A"),
        ]
        mock_embed.side_effect = RuntimeError("API down")

        stats = backfill_k_vectors(
            mock_adapter, dry_run=False, embed_api_key="test"
        )

        assert stats == {"total": 1, "filled": 0, "errors": 1}
        mock_adapter.update_k_vector.assert_not_called()

    @patch("knowledge_tree_builder.scripts.backfill_k_vectors.batch_embed")
    def test_partial_embedding_none(
        self, mock_embed: MagicMock, mock_adapter: MagicMock
    ) -> None:
        """部分 embedding 为 None 时，对应节点计为 error。"""
        mock_adapter.cursor.fetchall.return_value = [
            (1, "text A"),
            (2, "text B"),
        ]
        # 第二个 embedding 为 None（会被数量校验拦截，因为 len != texts）
        mock_embed.return_value = [[0.1] * 1024, None]

        stats = backfill_k_vectors(
            mock_adapter, dry_run=False, batch_size=10, embed_api_key="test"
        )

        # len(embeddings)=2 == len(texts)=2 通过校验
        # 但 embeddings[1] is None → j=1 走 else 分支
        assert stats["total"] == 2
        assert stats["filled"] == 1
        assert stats["errors"] == 1


class TestBackfillSQLQuery:
    """SQL 查询正确性验证。"""

    def test_sql_uses_distinct_on(self, mock_adapter: MagicMock) -> None:
        """验证 SQL 包含 DISTINCT ON 防止多 text 节点重复。"""
        mock_adapter.cursor.fetchall.return_value = []

        backfill_k_vectors(mock_adapter, dry_run=False, embed_api_key="test")

        sql = mock_adapter.cursor.execute.call_args[0][0]
        assert "DISTINCT ON (kt.id)" in sql
        assert "k_vector IS NULL" in sql
        assert "ORDER BY kt.id" in sql
