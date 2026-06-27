"""测试 consolidate/confidence.py — 衰减公式 + 阈值"""

from __future__ import annotations

import pytest

from knowledge_tree_builder.consolidate.confidence import (
    update_confidence,
    batch_update_from_logs,
)


class TestUpdateConfidence:
    """confidence 衰减公式测试"""

    def test_click_boost(self) -> None:
        conf, action = update_confidence(0.5, was_recalled=True, was_clicked=True)
        assert conf == pytest.approx(0.55)  # 0.5 + 0.05
        assert action == "normal"

    def test_recall_no_click_decay(self) -> None:
        conf, action = update_confidence(0.8, was_recalled=True, was_clicked=False, days_since_last_event=10)
        expected = 0.8 * (0.99 ** 10)
        assert conf == pytest.approx(expected)
        assert action == "normal"

    def test_no_recall_decay(self) -> None:
        conf, action = update_confidence(0.8, was_recalled=False, was_clicked=False, days_since_last_event=30)
        expected = 0.8 * (0.997 ** 30)
        assert conf == pytest.approx(expected)

    def test_user_negation_halves(self) -> None:
        conf, action = update_confidence(0.8, user_negated=True)
        assert conf == 0.4
        assert action == "demote"

    def test_remove_threshold(self) -> None:
        conf, action = update_confidence(0.05, was_recalled=False)
        assert action == "remove"
        assert conf >= 0.0

    def test_review_threshold(self) -> None:
        conf, action = update_confidence(0.25, was_recalled=True, was_clicked=False, days_since_last_event=1)
        assert action == "review"

    def test_demote_threshold(self) -> None:
        conf, action = update_confidence(0.4, was_recalled=True, was_clicked=False, days_since_last_event=1)
        assert action == "demote"

    def test_promote_threshold(self) -> None:
        conf, action = update_confidence(0.96, was_recalled=True, was_clicked=True)
        assert action == "promote"

    def test_confidence_never_negative(self) -> None:
        conf, action = update_confidence(0.0, was_recalled=False, was_clicked=False, days_since_last_event=365)
        assert conf >= 0.0

    def test_confidence_capped_at_1(self) -> None:
        conf, action = update_confidence(0.99, was_recalled=True, was_clicked=True)
        assert conf <= 1.0

    def test_consecutive_misses_penalty(self) -> None:
        """连续 3 次未被点击应触发额外衰减"""
        conf, action = update_confidence(
            0.8, was_recalled=True, was_clicked=False,
            days_since_last_event=1, consecutive_misses=3,
        )
        # 日衰减 + 额外 7 天惩罚
        expected = 0.8 * (0.99 ** 1) * (0.99 ** 7)
        assert conf == pytest.approx(expected)

    def test_consecutive_misses_below_3_no_penalty(self) -> None:
        conf_before = 0.8
        conf, _ = update_confidence(
            conf_before, was_recalled=True, was_clicked=False,
            days_since_last_event=1, consecutive_misses=2,
        )
        expected = 0.8 * (0.99 ** 1)
        assert conf == pytest.approx(expected)

    def test_click_resets_misses(self) -> None:
        conf, _ = update_confidence(
            0.5, was_recalled=True, was_clicked=True,
            consecutive_misses=5,
        )
        assert conf == pytest.approx(0.55)  # 点击升权，无额外衰减


class TestBatchUpdateFromLogs:
    """批量更新测试"""

    def test_empty_logs(self) -> None:
        result = batch_update_from_logs([], {"1": 0.8})
        assert result == {}

    def test_single_log(self) -> None:
        logs = [{"knowledge_id": "1", "recalled": True, "clicked": True, "user_feedback": None, "timestamp": ""}]
        confs = {"1": 0.5}
        result = batch_update_from_logs(logs, confs)
        assert "1" in result
        assert result["1"][0] == pytest.approx(0.55)

    def test_multiple_logs_same_knowledge(self) -> None:
        logs = [
            {"knowledge_id": "1", "recalled": True, "clicked": True, "user_feedback": None, "timestamp": ""},
            {"knowledge_id": "1", "recalled": True, "clicked": False, "user_feedback": None, "timestamp": ""},
        ]
        confs = {"1": 0.8}
        result = batch_update_from_logs(logs, confs)
        assert "1" in result
        # 0.8 → click +0.05 = 0.85 → recall_no_click × 0.99^1 ≈ 0.8415
        assert result["1"][0] == pytest.approx(0.85 * 0.99, rel=1e-3)

    def test_negation_feedback(self) -> None:
        logs = [{"knowledge_id": "1", "recalled": True, "clicked": False,
                 "user_feedback": "不是这个", "timestamp": ""}]
        confs = {"1": 0.8}
        result = batch_update_from_logs(logs, confs)
        assert result["1"][0] == pytest.approx(0.4)  # 0.8 × 0.5

    def test_unknown_knowledge_default(self) -> None:
        """未在 current_confidences 中的知识使用默认值 1.0"""
        logs = [{"knowledge_id": "99", "recalled": True, "clicked": False, "user_feedback": None, "timestamp": ""}]
        result = batch_update_from_logs(logs, {})
        assert "99" in result
        expected = 1.0 * 0.99
        assert result["99"][0] == pytest.approx(expected)
