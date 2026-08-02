"""Unit tests for _post_process_recall extraction."""
import pytest
from unittest.mock import MagicMock, patch
import knowledge_navigation.core.hooks as h
from knowledge_navigation.core.hooks import router as kn_router


class TestPostProcessRecall:
    def test_returns_none_when_hindsight_fail_and_kt_empty(self):
        from knowledge_navigation.core.hooks import _post_process_recall
        result, meta = _post_process_recall(None, [], True, False, "", "sid", "msg", "msg", 0.0, None)
        assert result is None

    def test_returns_none_when_no_kept_and_no_summary(self):
        from knowledge_navigation.core.hooks import _post_process_recall
        with patch.object(kn_router, "exclude_marked", return_value=([], 0)), \
             patch.object(kn_router, "extract_rerank_scores", return_value={}), \
             patch.object(kn_router, "_hit_counter"), \
             patch.object(kn_router, "_compaction", get_effective_max_results=MagicMock(return_value=10)), \
             patch.object(kn_router, "_build_mentioned_at_map", return_value={}), \
             patch.object(kn_router, "filter_by_score", return_value=([], [], {})), \
             patch.object(kn_router, "_task_tracker", get_summary_prompt=MagicMock(return_value=None)):
            result, meta = _post_process_recall(
                {"results": [], "trace": {}}, [], False, False, "", "sid", "msg", "msg", 0.0, None
            )
        assert result is None

    def test_keeps_results_when_kept_and_no_summary(self):
        from knowledge_navigation.core.hooks import _post_process_recall
        kept_mock = [{"id": "1", "text": "test", "score": 0.9}]
        with patch.object(kn_router, "exclude_marked", return_value=(kept_mock, 0)), \
             patch.object(kn_router, "extract_rerank_scores", return_value={"1": 0.9}), \
             patch.object(kn_router, "_hit_counter"), \
             patch.object(kn_router, "_compaction", get_effective_max_results=MagicMock(return_value=10)), \
             patch.object(kn_router, "_build_mentioned_at_map", return_value={}), \
             patch.object(kn_router, "filter_by_score", return_value=(kept_mock, [], {})), \
             patch.object(kn_router, "_task_tracker", get_summary_prompt=MagicMock(return_value=None)):
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
        with patch.object(kn_router, "CONFIG", turn_to_turn_dedup_mode="demote", enable_token_budget=False):
            with patch.object(kn_router, "_touch_injected_session"):
                result, ctx = _dedup_and_budget(kept, "test-dedup", "")
        assert len(result) == 2
        for r in result:
            if r["id"] == "1":
                assert r.get("final_score", 1.0) < 0.2

    def test_turn_dedup_remove_mode(self):
        from knowledge_navigation.core.hooks import _dedup_and_budget
        h._injected_ids["test-remove"] = {"1": 0.0}
        kept = [{"id": "1", "text": "old", "score": 0.9}, {"id": "2", "text": "new", "score": 0.8}]
        with patch.object(kn_router, "CONFIG", turn_to_turn_dedup_mode="remove", enable_token_budget=False):
            with patch.object(kn_router, "_touch_injected_session"):
                result, ctx = _dedup_and_budget(kept, "test-remove", "")
        assert len(result) == 1
        assert result[0]["id"] == "2"

    def test_sag_pointer_not_deduped(self):
        """SAG 指针模式文本结构相似但内容不同，不应被 dedup_by_text 误杀。"""
        from knowledge_navigation.core.filtering import dedup_by_text

        sag_results = [
            {
                "id": f"sag_{i}",
                "text": f"[SAG 指针] heading: 章节{i} | score: 0.50 | preview: 内容{i}... | 如需完整内容，使用 sag_search 工具查询: 章节{i}",
                "source": "sag",
                "final_score": 0.5,
            }
            for i in range(10)
        ]
        deduped = dedup_by_text(sag_results)
        assert len(deduped) == 10, f"SAG 指针候选被误杀: {len(sag_results)} → {len(deduped)}"

    def test_hindsight_still_deduped(self):
        """Hindsight 重复记忆仍应被去重。"""
        from knowledge_navigation.core.filtering import dedup_by_text

        results = [
            {"id": "1", "text": "系统配置文件在 /etc/hermes/config.yaml", "source": "hindsight"},
            {"id": "2", "text": "系统配置文件在 /etc/hermes/config.yaml", "source": "hindsight"},
        ]
        deduped = dedup_by_text(results)
        assert len(deduped) == 1