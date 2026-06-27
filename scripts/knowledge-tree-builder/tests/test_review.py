"""测试 consolidate/review.py — 审查队列状态机"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from knowledge_tree_builder.consolidate.review import (
    REVIEW_TYPES,
    TIMEOUT_DAYS,
    DEFAULT_ACTIONS,
    _check_timeout,
    accept_review,
    reject_review,
    insert_review_item,
    list_reviews,
    process_timeouts,
)


class TestReviewTypes:
    """审查类型常量测试"""

    def test_all_types_defined(self) -> None:
        assert "consistency_warning" in REVIEW_TYPES
        assert "incomplete_split" in REVIEW_TYPES
        assert "contradiction" in REVIEW_TYPES
        assert "orphan" in REVIEW_TYPES
        assert "obsolete" in REVIEW_TYPES
        assert "move_suggestion" in REVIEW_TYPES

    def test_timeout_days_all_types(self) -> None:
        for t in REVIEW_TYPES:
            assert t in TIMEOUT_DAYS, f"missing timeout for {t}"

    def test_default_actions_all_types(self) -> None:
        for t in REVIEW_TYPES:
            assert t in DEFAULT_ACTIONS, f"missing action for {t}"


class TestCheckTimeout:
    """超时检查测试"""

    def test_no_timeout_at_future(self, monkeypatch) -> None:
        import datetime
        # timeout_at 在未来
        item = {"timeout_at": "2099-01-01T00:00:00+00:00"}
        assert _check_timeout(item) is False

    def test_no_timeout_no_field(self) -> None:
        assert _check_timeout({}) is False

    def test_timeout_reached(self, monkeypatch) -> None:
        import datetime
        item = {"timeout_at": "2020-01-01T00:00:00+00:00"}
        assert _check_timeout(item) is True


class TestListReviews:
    """列表查询测试"""

    def test_list_calls_adapter(self) -> None:
        mock_db = MagicMock()
        mock_db.list_review_queue.return_value = [{"id": 1, "type": "orphan"}]
        result = list_reviews(mock_db)
        assert len(result) == 1
        mock_db.list_review_queue.assert_called_once_with(review_type=None, status="pending_review")

    def test_list_with_type(self) -> None:
        mock_db = MagicMock()
        mock_db.list_review_queue.return_value = []
        result = list_reviews(mock_db, review_type="contradiction")
        assert result == []
        mock_db.list_review_queue.assert_called_once_with(review_type="contradiction", status="pending_review")

    def test_list_adapter_error(self) -> None:
        mock_db = MagicMock()
        mock_db.list_review_queue.side_effect = Exception("DB down")
        result = list_reviews(mock_db)
        assert result == []


class TestAcceptReview:
    """接受审查项测试"""

    def test_accept_existing(self) -> None:
        mock_db = MagicMock()
        mock_db.get_review_item.return_value = {"id": 1, "type": "orphan", "target_knowledge_id": None}
        assert accept_review(1, mock_db) is True
        mock_db.update_review_status.assert_called_once_with(1, "accepted")

    def test_accept_nonexistent(self) -> None:
        mock_db = MagicMock()
        mock_db.get_review_item.return_value = None
        assert accept_review(999, mock_db) is False

    def test_accept_deletes_obsolete(self) -> None:
        mock_db = MagicMock()
        mock_db.get_review_item.return_value = {"id": 1, "type": "obsolete", "target_knowledge_id": 42}
        assert accept_review(1, mock_db) is True
        mock_db.delete_node.assert_called_once_with(42)

    def test_accept_db_error(self) -> None:
        mock_db = MagicMock()
        mock_db.get_review_item.side_effect = Exception("DB error")
        assert accept_review(1, mock_db) is False


class TestRejectReview:
    """拒绝审查项测试"""

    def test_reject(self) -> None:
        mock_db = MagicMock()
        assert reject_review(1, mock_db) is True
        mock_db.update_review_status.assert_called_once_with(1, "rejected")

    def test_reject_db_error(self) -> None:
        mock_db = MagicMock()
        mock_db.update_review_status.side_effect = Exception("DB error")
        assert reject_review(1, mock_db) is False


class TestInsertReviewItem:
    """插入审查项测试"""

    def test_insert_without_db(self) -> None:
        item = insert_review_item("orphan", "游离知识", "原文", 1, "无法归入")
        assert item is not None
        assert item["type"] == "orphan"
        assert item["status"] == "pending_review"
        assert item["timeout_at"] is not None  # orphan 有 30 天超时

    def test_insert_with_db(self) -> None:
        mock_db = MagicMock()
        item = insert_review_item(
            "orphan", "游离知识", "原文", 1, "无法归入",
            db_adapter=mock_db,
        )
        assert item is not None
        mock_db.insert_review.assert_called_once()

    def test_insert_unknown_type(self) -> None:
        item = insert_review_item("unknown_type", "text", "orig", 1, "reason")
        assert item is None

    def test_contradiction_no_timeout(self) -> None:
        item = insert_review_item("contradiction", "矛盾知识", "原文", 1, "矛盾检测")
        assert item is not None
        assert item["timeout_at"] is None  # contradiction 永久 pending


class TestProcessTimeouts:
    """超时处理测试"""

    def test_no_timeouts(self) -> None:
        mock_db = MagicMock()
        mock_db.list_review_queue.return_value = []
        count = process_timeouts(mock_db)
        assert count == 0

    def test_skips_non_timeout_items(self) -> None:
        mock_db = MagicMock()
        mock_db.list_review_queue.return_value = [
            {"id": 1, "type": "orphan", "timeout_at": "2099-01-01T00:00:00+00:00", "target_knowledge_id": None},
        ]
        count = process_timeouts(mock_db)
        assert count == 0

    def test_deletes_timeout_obsolete(self) -> None:
        mock_db = MagicMock()
        mock_db.list_review_queue.return_value = [
            {"id": 1, "type": "obsolete", "timeout_at": "2020-01-01T00:00:00+00:00", "target_knowledge_id": 42},
        ]
        count = process_timeouts(mock_db)
        assert count >= 1
        mock_db.delete_node.assert_called_once_with(42)
        mock_db.update_review_status.assert_called_once_with(1, "timeout")

    def test_db_error(self) -> None:
        mock_db = MagicMock()
        mock_db.list_review_queue.side_effect = Exception("DB error")
        count = process_timeouts(mock_db)
        assert count == 0
