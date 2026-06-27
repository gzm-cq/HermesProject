"""Regression tests for knowledge_review_queue status consistency."""

from __future__ import annotations

from unittest.mock import MagicMock

from knowledge_tree_builder.adapters.database import DatabaseAdapter


def test_list_review_queue_default_status_includes_legacy_pending() -> None:
    """Default review listing should see both pending_review and legacy pending rows."""
    adapter = object.__new__(DatabaseAdapter)
    adapter.cursor = MagicMock()
    adapter.cursor.fetchall.return_value = []

    adapter.list_review_queue()

    sql, params = adapter.cursor.execute.call_args.args
    assert "status = ANY(%s)" in sql
    assert params == (["pending_review", "pending"],)


def test_insert_review_uses_pending_review_status() -> None:
    """Rows inserted by adapter must be visible to review processors by default."""
    adapter = object.__new__(DatabaseAdapter)
    adapter.cursor = MagicMock()
    adapter.cursor.fetchone.return_value = [123]
    adapter.conn = MagicMock()

    review_id = adapter.insert_review(
        new_text="new",
        existing_node_id=1,
        existing_text="old",
        conflict_type="contradiction",
        similarity=0.9,
    )

    assert review_id == 123
    sql, params = adapter.cursor.execute.call_args.args
    assert "VALUES (%s, %s, %s, %s, %s, %s)" in sql
    assert params[-1] == "pending_review"
