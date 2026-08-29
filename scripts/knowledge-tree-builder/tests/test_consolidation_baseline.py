"""TDD tests for ConsolidationEngine.collect_baseline_metrics()."""

from __future__ import annotations

from unittest.mock import MagicMock

from knowledge_tree_builder.core.consolidation import ConsolidationEngine


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_mock_adapter(fetchone_results: list[tuple]) -> MagicMock:
    """Build a mock db_adapter whose cursor returns sequential fetchone results."""
    cursor = MagicMock()
    cursor.fetchone.side_effect = fetchone_results
    adapter = MagicMock()
    adapter.cursor = cursor
    return adapter


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCollectBaselineMetrics:
    """Tests for ConsolidationEngine.collect_baseline_metrics."""

    def test_returns_none_when_db_adapter_is_none(self):
        engine = ConsolidationEngine()
        assert engine.collect_baseline_metrics(None) is None

    def test_returns_dict_with_seven_float_keys(self):
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.85,),   # avg_confidence
            (150,),    # total_kps
            (12,),     # total_subjects
            (5,),      # fragment_domains
            (3,),      # orphan_kps
            (10,),     # low_conf_kp_count (for low_conf_kp_rate)
            (2,),      # pending_review_count (for pending_conflict_rate)
        ])

        result = engine.collect_baseline_metrics(adapter)

        assert result is not None
        assert set(result.keys()) == {
            "avg_confidence", "total_kps", "total_subjects",
            "fragment_domains", "orphan_kps",
            "low_conf_kp_rate", "pending_conflict_rate",
        }
        assert isinstance(result["avg_confidence"], float)
        assert isinstance(result["total_kps"], float)
        assert isinstance(result["total_subjects"], float)
        assert isinstance(result["fragment_domains"], float)
        assert isinstance(result["orphan_kps"], float)
        assert isinstance(result["low_conf_kp_rate"], float)
        assert isinstance(result["pending_conflict_rate"], float)

    def test_metric_values_are_correct(self):
        engine = ConsolidationEngine()
        # low_conf_kp_rate = 20/200 = 0.1
        # pending_conflict_rate = 5/200 = 0.025
        adapter = _make_mock_adapter([
            (0.92,),   # avg_confidence
            (200,),    # total_kps
            (15,),     # total_subjects
            (3,),      # fragment_domains
            (7,),      # orphan_kps
            (20,),     # low_conf_kp_count
            (5,),      # pending_review_count
        ])

        result = engine.collect_baseline_metrics(adapter)

        assert result is not None
        assert result["avg_confidence"] == 0.92
        assert result["total_kps"] == 200.0
        assert result["total_subjects"] == 15.0
        assert result["fragment_domains"] == 3.0
        assert result["orphan_kps"] == 7.0
        assert result["low_conf_kp_rate"] == 0.1
        assert result["pending_conflict_rate"] == 0.025

    def test_cursor_execute_called_seven_times(self):
        """Each metric issues a separate SQL query."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.5,), (10,), (2,), (1,), (2,), (1,), (0,),
        ])

        engine.collect_baseline_metrics(adapter)

        assert adapter.cursor.execute.call_count == 7

    def test_each_query_uses_different_sql(self):
        """Verify the 7 execute calls use distinct SQL strings."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.5,), (10,), (2,), (1,), (2,), (1,), (0,),
        ])

        engine.collect_baseline_metrics(adapter)

        sqls = [call.args[0] for call in adapter.cursor.execute.call_args_list]
        assert len(sqls) == 7
        assert len(set(sqls)) == 7

    def test_fetchone_called_seven_times(self):
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.5,), (10,), (2,), (1,), (2,), (1,), (0,),
        ])

        engine.collect_baseline_metrics(adapter)

        assert adapter.cursor.fetchone.call_count == 7

    def test_zero_values_handled_correctly(self):
        """When DB returns 0 / None, metrics should be 0.0 float."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.0,),    # avg_confidence
            (0,),      # total_kps
            (0,),      # total_subjects
            (0,),      # fragment_domains
            (0,),      # orphan_kps
            (0,),      # low_conf_kp_count
            (0,),      # pending_review_count
        ])

        result = engine.collect_baseline_metrics(adapter)

        assert result is not None
        assert result["avg_confidence"] == 0.0
        assert result["total_kps"] == 0.0
        assert result["total_subjects"] == 0.0
        assert result["fragment_domains"] == 0.0
        assert result["orphan_kps"] == 0.0
        assert result["low_conf_kp_rate"] == 0.0
        assert result["pending_conflict_rate"] == 0.0

    def test_none_fetchone_value_handled(self):
        """When fetchone returns (None,), float(None or 0.0) == 0.0."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (None,),   # avg_confidence
            (None,),   # total_kps
            (None,),   # total_subjects
            (None,),   # fragment_domains
            (None,),   # orphan_kps
            (None,),   # low_conf_kp_count
            (None,),   # pending_review_count
        ])

        result = engine.collect_baseline_metrics(adapter)

        assert result is not None
        assert result["avg_confidence"] == 0.0
        assert result["total_kps"] == 0.0
        assert result["total_subjects"] == 0.0
        assert result["fragment_domains"] == 0.0
        assert result["orphan_kps"] == 0.0
        assert result["low_conf_kp_rate"] == 0.0
        assert result["pending_conflict_rate"] == 0.0

    def test_avg_confidence_sql_contains_avg(self):
        """First query should compute AVG(retrieval_confidence)."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([(0.5,), (1,), (1,), (1,), (1,), (1,), (1,)])

        engine.collect_baseline_metrics(adapter)

        first_sql = adapter.cursor.execute.call_args_list[0].args[0]
        assert "AVG" in first_sql.upper()
        assert "retrieval_confidence" in first_sql

    def test_total_kps_sql_contains_count(self):
        """Second query should COUNT(*) knowledge_point rows."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([(0.5,), (1,), (1,), (1,), (1,), (1,), (1,)])

        engine.collect_baseline_metrics(adapter)

        second_sql = adapter.cursor.execute.call_args_list[1].args[0]
        assert "COUNT" in second_sql.upper()

    def test_fragment_domains_sql_contains_recursive(self):
        """Third query is for total_subjects; fragment_domains is fourth."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([(0.5,), (1,), (1,), (1,), (1,), (1,), (1,)])

        engine.collect_baseline_metrics(adapter)

        fourth_sql = adapter.cursor.execute.call_args_list[3].args[0]
        assert "RECURSIVE" in fourth_sql.upper()

    def test_orphan_kps_sql_references_edges_table(self):
        """Fifth query should check knowledge_tree_edges for orphan detection."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([(0.5,), (1,), (1,), (1,), (1,), (1,), (1,)])

        engine.collect_baseline_metrics(adapter)

        fifth_sql = adapter.cursor.execute.call_args_list[4].args[0]
        assert "knowledge_tree_edges" in fifth_sql