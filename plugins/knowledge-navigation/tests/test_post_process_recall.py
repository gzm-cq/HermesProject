"""Unit tests for _post_process_recall extraction."""
import pytest
from unittest.mock import MagicMock, patch
import knowledge_navigation.core.hooks as h


class TestPostProcessRecall:
    def test_returns_none_when_hindsight_fail_and_kt_empty(self):
        from knowledge_navigation.core.hooks import _post_process_recall
        result, meta = _post_process_recall(None, [], True, False, "", "sid", "msg", "msg", 0.0, None)
        assert result is None

    def test_returns_none_when_no_kept_and_no_summary(self):
        from knowledge_navigation.core.hooks import _post_process_recall
        with patch.object(h, "exclude_marked", return_value=([], 0)), \
             patch.object(h, "extract_rerank_scores", return_value={}), \
             patch.object(h, "_hit_counter"), \
             patch.object(h, "_compaction", get_effective_max_results=MagicMock(return_value=10)), \
             patch.object(h, "_build_mentioned_at_map", return_value={}), \
             patch.object(h, "filter_by_score", return_value=([], [], {})), \
             patch.object(h, "_task_tracker", get_summary_prompt=MagicMock(return_value=None)):
            result, meta = _post_process_recall(
                {"results": [], "trace": {}}, [], False, False, "", "sid", "msg", "msg", 0.0, None
            )
        assert result is None

    def test_keeps_results_when_kept_and_no_summary(self):
        from knowledge_navigation.core.hooks import _post_process_recall
        kept_mock = [{"id": "1", "text": "test", "score": 0.9}]
        with patch.object(h, "exclude_marked", return_value=(kept_mock, 0)), \
             patch.object(h, "extract_rerank_scores", return_value={"1": 0.9}), \
             patch.object(h, "_hit_counter"), \
             patch.object(h, "_compaction", get_effective_max_results=MagicMock(return_value=10)), \
             patch.object(h, "_build_mentioned_at_map", return_value={}), \
             patch.object(h, "filter_by_score", return_value=(kept_mock, [], {})), \
             patch.object(h, "_task_tracker", get_summary_prompt=MagicMock(return_value=None)):
            result, meta = _post_process_recall(
                {"results": [{"id": "1", "text": "test", "score": 0.9}], "trace": {}},
                [], True, False, "", "sid", "msg", "msg", 0.0, None
            )
        assert result is not None
        assert len(result) == 1
        assert "latency_ms" in meta
        assert "excluded_count" in meta


class TestDedupAndBudget:
    def test_turn_dedup_demote_mode(self):
        from knowledge_navigation.core.hooks import _dedup_and_budget
        h._injected_ids["test-dedup"] = {"1": 0.0}
        kept = [{"id": "1", "text": "old", "score": 0.9}, {"id": "2", "text": "new", "score": 0.8}]
        with patch.object(h, "CONFIG", turn_to_turn_dedup_mode="demote", enable_token_budget=False):
            with patch.object(h, "_touch_injected_session"):
                result, ctx = _dedup_and_budget(kept, "test-dedup", "")
        assert len(result) == 2
        for r in result:
            if r["id"] == "1":
                assert r.get("final_score", 1.0) < 0.2

    def test_turn_dedup_remove_mode(self):
        from knowledge_navigation.core.hooks import _dedup_and_budget
        h._injected_ids["test-remove"] = {"1": 0.0}
        kept = [{"id": "1", "text": "old", "score": 0.9}, {"id": "2", "text": "new", "score": 0.8}]
        with patch.object(h, "CONFIG", turn_to_turn_dedup_mode="remove", enable_token_budget=False):
            with patch.object(h, "_touch_injected_session"):
                result, ctx = _dedup_and_budget(kept, "test-remove", "")
        assert len(result) == 1
        assert result[0]["id"] == "2"