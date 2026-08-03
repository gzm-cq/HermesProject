"""_percentile / _is_test_query 单元测试。"""

from __future__ import annotations

from flywheel_health_report.utils import _percentile, _is_test_query


# ========== _percentile ==========

class TestPercentile:
    """测试 _percentile 线性插值实现。"""

    def test_single_value(self) -> None:
        assert _percentile([42.0], 0.5) == 42.0
        assert _percentile([42.0], 0.95) == 42.0

    def test_p50_median(self) -> None:
        assert _percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5

    def test_p95_high_end(self) -> None:
        result = _percentile([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], 0.95)
        assert 9.0 <= result <= 10.0

    def test_empty_list(self) -> None:
        """空列表应返回 0 不报错。"""
        assert _percentile([], 0.5) == 0


# ========== _is_test_query ==========

class TestIsTestQuery:
    """测试测试查询过滤。"""

    def test_recognizes_test_prefixes(self) -> None:
        for prefix in ("gen_", "eval-", "test_", "test-", "exact_kw_", "semantic_",
                       "entity_", "causal_", "temporal_", "conflict_", "tool_",
                       "debug_", "api_", "compare_", "workflow_", "complex_", "numeric_"):
            assert _is_test_query(prefix + "abc") is True

    def test_rejects_normal_queries(self) -> None:
        assert _is_test_query("如何配置数据库连接") is False
        assert _is_test_query("deploy the service") is False
