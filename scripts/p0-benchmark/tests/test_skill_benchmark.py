"""Skill Matcher Benchmark 单元测试。"""

from __future__ import annotations

from p0_benchmark.core.skill_benchmark import (
    SAMPLE_QUERIES,
    generate_test_queries,
    run_skill_matcher_benchmark,
)


class TestGenerateTestQueries:
    """测试 query 生成。"""

    def test_less_than_sample_size(self):
        """请求数小于样本数时不重复。"""
        queries = generate_test_queries(5)
        assert len(queries) == 5
        assert len(set(queries)) == 5

    def test_more_than_sample_size(self):
        """请求数大于样本数时允许重复。"""
        queries = generate_test_queries(len(SAMPLE_QUERIES) + 10)
        assert len(queries) == len(SAMPLE_QUERIES) + 10

    def test_zero_queries(self):
        """0 条返回空列表。"""
        queries = generate_test_queries(0)
        assert queries == []

    def test_exact_sample_size(self):
        """正好等于样本数。"""
        queries = generate_test_queries(len(SAMPLE_QUERIES))
        assert len(queries) == len(SAMPLE_QUERIES)
        assert len(set(queries)) == len(SAMPLE_QUERIES)


class TestMockMode:
    """模拟模式测试（模块不可用时）。"""

    def test_mock_returns_expected_keys(self):
        """模拟模式返回正确的键结构。"""
        result = run_skill_matcher_benchmark(
            num_queries=10,
            prescreen_top_k=20,
            accuracy_threshold=0.9,
            random_seed=42,
        )
        # 模块不可用时返回 mock 数据
        assert "total_queries" in result
        assert "avg_latency_with_ms" in result
        assert "avg_latency_without_ms" in result
        assert "latency_reduction_pct" in result
        assert "token_savings_pct" in result
        assert "accuracy" in result
        assert "passed" in result
        assert result["mock_data"] is True

    def test_mock_reproducible_with_seed(self):
        """相同种子下 mock 结果可复现。"""
        r1 = run_skill_matcher_benchmark(num_queries=10, random_seed=42)
        r2 = run_skill_matcher_benchmark(num_queries=10, random_seed=42)
        assert r1["accuracy"] == r2["accuracy"]
        assert r1["token_savings_pct"] == r2["token_savings_pct"]
