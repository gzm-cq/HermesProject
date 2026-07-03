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

    def test_returns_dict_with_four_float_keys(self):
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.85,),   # avg_confidence
            (150,),    # total_kps
            (5,),      # fragment_domains
            (12,),     # orphan_kps
        ])

        result = engine.collect_baseline_metrics(adapter)

        assert result is not None
        assert set(result.keys()) == {
            "avg_confidence", "total_kps", "fragment_domains", "orphan_kps",
        }
        assert isinstance(result["avg_confidence"], float)
        assert isinstance(result["total_kps"], float)
        assert isinstance(result["fragment_domains"], float)
        assert isinstance(result["orphan_kps"], float)

    def test_metric_values_are_correct(self):
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.92,),   # avg_confidence
            (200,),    # total_kps
            (3,),      # fragment_domains
            (7,),      # orphan_kps
        ])

        result = engine.collect_baseline_metrics(adapter)

        assert result is not None
        assert result["avg_confidence"] == 0.92
        assert result["total_kps"] == 200.0
        assert result["fragment_domains"] == 3.0
        assert result["orphan_kps"] == 7.0

    def test_cursor_execute_called_four_times(self):
        """Each metric issues a separate SQL query."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.5,), (10,), (1,), (2,),
        ])

        engine.collect_baseline_metrics(adapter)

        assert adapter.cursor.execute.call_count == 4

    def test_each_query_uses_different_sql(self):
        """Verify the 4 execute calls use distinct SQL strings."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.5,), (10,), (1,), (2,),
        ])

        engine.collect_baseline_metrics(adapter)

        sqls = [call.args[0] for call in adapter.cursor.execute.call_args_list]
        assert len(sqls) == 4
        # All 4 SQL strings are distinct
        assert len(set(sqls)) == 4

    def test_fetchone_called_four_times(self):
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.5,), (10,), (1,), (2,),
        ])

        engine.collect_baseline_metrics(adapter)

        assert adapter.cursor.fetchone.call_count == 4

    def test_zero_values_handled_correctly(self):
        """When DB returns 0 / None, metrics should be 0.0 float."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (0.0,),    # avg_confidence
            (0,),      # total_kps
            (0,),      # fragment_domains
            (0,),      # orphan_kps
        ])

        result = engine.collect_baseline_metrics(adapter)

        assert result is not None
        assert result["avg_confidence"] == 0.0
        assert result["total_kps"] == 0.0
        assert result["fragment_domains"] == 0.0
        assert result["orphan_kps"] == 0.0

    def test_none_fetchone_value_handled(self):
        """When fetchone returns (None,), float(None or 0.0) == 0.0."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([
            (None,),   # avg_confidence — COALESCE returns 0.0 but mock simulates None
            (None,),   # total_kps
            (None,),   # fragment_domains
            (None,),   # orphan_kps
        ])

        result = engine.collect_baseline_metrics(adapter)

        assert result is not None
        assert result["avg_confidence"] == 0.0
        assert result["total_kps"] == 0.0
        assert result["fragment_domains"] == 0.0
        assert result["orphan_kps"] == 0.0

    def test_avg_confidence_sql_contains_avg(self):
        """First query should compute AVG(retrieval_confidence)."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([(0.5,), (1,), (1,), (1,)])

        engine.collect_baseline_metrics(adapter)

        first_sql = adapter.cursor.execute.call_args_list[0].args[0]
        assert "AVG" in first_sql.upper()
        assert "retrieval_confidence" in first_sql

    def test_total_kps_sql_contains_count(self):
        """Second query should COUNT(*) knowledge_point rows."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([(0.5,), (1,), (1,), (1,)])

        engine.collect_baseline_metrics(adapter)

        second_sql = adapter.cursor.execute.call_args_list[1].args[0]
        assert "COUNT" in second_sql.upper()

    def test_fragment_domains_sql_contains_recursive(self):
        """Third query should use WITH RECURSIVE for descendant traversal."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([(0.5,), (1,), (1,), (1,)])

        engine.collect_baseline_metrics(adapter)

        third_sql = adapter.cursor.execute.call_args_list[2].args[0]
        assert "RECURSIVE" in third_sql.upper()

    def test_orphan_kps_sql_references_edges_table(self):
        """Fourth query should check knowledge_tree_edges for orphan detection."""
        engine = ConsolidationEngine()
        adapter = _make_mock_adapter([(0.5,), (1,), (1,), (1,)])

        engine.collect_baseline_metrics(adapter)

        fourth_sql = adapter.cursor.execute.call_args_list[3].args[0]
        assert "knowledge_tree_edges" in fourth_sql
