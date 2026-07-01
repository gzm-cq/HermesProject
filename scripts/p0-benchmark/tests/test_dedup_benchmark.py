"""去重 Benchmark 单元测试。"""

from __future__ import annotations

import random

from p0_benchmark.core.dedup_benchmark import (
    SPEEDUP_THRESHOLDS,
    _cosine_similarity,
    _dedup_memory_scan,
    _dedup_pgvector_simulated,
    _generate_random_vector,
    run_dedup_benchmark,
)


class TestCosineSimilarity:
    """余弦相似度计算测试。"""

    def test_identical_vectors(self):
        """相同向量相似度为 1.0。"""
        vec = [1.0, 2.0, 3.0]
        assert abs(_cosine_similarity(vec, vec) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        """正交向量相似度为 0。"""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(_cosine_similarity(a, b) - 0.0) < 1e-9

    def test_opposite_vectors(self):
        """相反向量相似度为 -1.0。"""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(_cosine_similarity(a, b) - (-1.0)) < 1e-9

    def test_zero_vector(self):
        """零向量返回 0。"""
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_known_value(self):
        """已知向量验证计算结果。"""
        a = [3.0, 4.0]
        b = [4.0, 3.0]
        expected = (3 * 4 + 4 * 3) / (5 * 5)  # 24/25 = 0.96
        assert abs(_cosine_similarity(a, b) - expected) < 1e-9


class TestGenerateRandomVector:
    """随机向量生成测试。"""

    def test_dimension(self):
        """生成向量维度正确。"""
        vec = _generate_random_vector(128)
        assert len(vec) == 128

    def test_default_dimension(self):
        """默认维度为 1024。"""
        vec = _generate_random_vector()
        assert len(vec) == 1024

    def test_values_in_range(self):
        """值在 [0, 1] 范围内。"""
        random.seed(42)
        vec = _generate_random_vector(100)
        assert all(0.0 <= v <= 1.0 for v in vec)


class TestDedupMemoryScan:
    """内存扫描去重测试。"""

    def test_empty_existing(self):
        """空库不重复。"""
        vec = [1.0, 0.0, 0.0]
        is_dup, matched_id, sim = _dedup_memory_scan(vec, [], threshold=0.9)
        assert is_dup is False
        assert matched_id is None
        assert sim == 0.0

    def test_exact_duplicate(self):
        """完全相同向量判定为重复。"""
        vec = [1.0, 0.0, 0.0]
        existing = [{"id": 1, "k_vector": [1.0, 0.0, 0.0]}]
        is_dup, matched_id, sim = _dedup_memory_scan(vec, existing, threshold=0.95)
        assert is_dup is True
        assert matched_id == 1
        assert abs(sim - 1.0) < 1e-9

    def test_no_duplicate(self):
        """不相似的向量不重复。"""
        vec = [1.0, 0.0, 0.0]
        existing = [{"id": 1, "k_vector": [0.0, 1.0, 0.0]}]
        is_dup, matched_id, sim = _dedup_memory_scan(vec, existing, threshold=0.9)
        assert is_dup is False
        assert sim == 0.0

    def test_none_vector_skipped(self):
        """None 向量被跳过。"""
        vec = [1.0, 0.0, 0.0]
        existing = [
            {"id": 1, "k_vector": None},
            {"id": 2, "k_vector": [0.5, 0.5, 0.0]},
        ]
        is_dup, matched_id, sim = _dedup_memory_scan(vec, existing, threshold=0.9)
        assert is_dup is False
        assert matched_id == 2


class TestDedupPgvectorSimulated:
    """pgvector 模拟去重测试。"""

    def test_empty_existing(self):
        """空库不重复。"""
        random.seed(42)
        vec = [1.0, 0.0, 0.0]
        is_dup, matched_id, sim = _dedup_pgvector_simulated(vec, [], threshold=0.9)
        assert is_dup is False
        assert matched_id is None
        assert sim == 0.0

    def test_returns_results(self):
        """返回结果格式正确。"""
        random.seed(42)
        vec = [random.random() for _ in range(64)]
        existing = [
            {"id": i, "k_vector": [random.random() for _ in range(64)]}
            for i in range(100)
        ]
        is_dup, matched_id, sim = _dedup_pgvector_simulated(vec, existing, threshold=0.95)
        assert isinstance(is_dup, bool)
        assert isinstance(sim, float)
        assert 0.0 <= sim <= 1.0


class TestRandomSeed:
    """随机种子可复现性测试。"""

    def test_dedup_benchmark_reproducible(self):
        """相同种子下结果可复现。"""
        result1 = run_dedup_benchmark(
            sizes=[100],
            threshold=0.95,
            repeat=1,
            random_seed=42,
        )
        result2 = run_dedup_benchmark(
            sizes=[100],
            threshold=0.95,
            repeat=1,
            random_seed=42,
        )
        # 相同种子下模拟结果应该一致（比较计算出的一致性等，不包括时间测量）
        assert len(result1["results_by_size"]) == len(result2["results_by_size"])
        r1 = result1["results_by_size"][0]
        r2 = result2["results_by_size"][0]
        assert r1["consistency"] == r2["consistency"]
        assert r1["size"] == r2["size"]
        assert r1["expected_speedup"] == r2["expected_speedup"]

    def test_different_seeds_different_results(self):
        """不同种子下结果可能不同。"""
        result1 = run_dedup_benchmark(
            sizes=[100],
            threshold=0.95,
            repeat=1,
            random_seed=42,
        )
        result2 = run_dedup_benchmark(
            sizes=[100],
            threshold=0.95,
            repeat=1,
            random_seed=123,
        )
        # 不同种子可能产生不同结果（不强制，但至少不报错）
        assert result1["all_passed"] is not None
        assert result2["all_passed"] is not None


class TestSpeedupThresholds:
    """加速比阈值测试。"""

    def test_thresholds_exist(self):
        """关键规模有阈值。"""
        assert 1000 in SPEEDUP_THRESHOLDS
        assert 10000 in SPEEDUP_THRESHOLDS

    def test_threshold_values(self):
        """阈值随规模增大。"""
        assert SPEEDUP_THRESHOLDS[1000] < SPEEDUP_THRESHOLDS[10000]
