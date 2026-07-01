"""P0-2: pgvector 去重 Benchmark。

验证指标：
- 1000 条知识库时，去重速度提升 10x+
- 10000 条知识库时，去重速度提升 100x+
- 去重结果与内存扫描一致性 ≥ 99%

注意：
- 一致性阈值设为 99% 而非 100%，因为 HNSW 是近似最近邻搜索，
  允许极少量边界情况下的微小差异。这是工程上的合理妥协。
- 真实 pgvector 环境下，如果 ef_search 参数足够大，一致性可接近 100%。
- 模拟模式下（无真实 DB），一致性取决于采样覆盖率，不代表真实性能。
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

logger = logging.getLogger(__name__)

# 验收标准（按知识库规模）
SPEEDUP_THRESHOLDS = {
    1000: 10.0,   # 10x+
    5000: 50.0,   # 50x+
    10000: 100.0, # 100x+
}


def _generate_random_vector(dim: int = 1024) -> list[float]:
    """生成随机向量。"""
    return [random.random() for _ in range(dim)]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _dedup_memory_scan(
    new_vector: list[float],
    existing_vectors: list[dict[str, Any]],
    threshold: float = 0.95,
) -> tuple[bool, int | None, float]:
    """内存扫描去重（O(N) 线性扫描）。

    Returns:
        (is_dup, matched_id, max_similarity)
    """
    max_sim = 0.0
    matched_id = None

    for item in existing_vectors:
        vec = item.get("k_vector")
        if vec is None:
            continue
        sim = _cosine_similarity(new_vector, vec)
        if sim > max_sim:
            max_sim = sim
            matched_id = item.get("id")
        if sim > threshold:
            return True, item.get("id"), sim

    return False, matched_id, max_sim


def _dedup_pgvector_simulated(
    new_vector: list[float],
    existing_vectors: list[dict[str, Any]],
    threshold: float = 0.95,
) -> tuple[bool, int | None, float]:
    """模拟 pgvector 近邻搜索（无真实 DB 时使用）。

    注意：这是模拟实现，性能和精度仅供参考。
    真实环境下应使用 DatabaseAdapter.find_nearest_neighbors()。
    """
    if not existing_vectors:
        return False, None, 0.0

    import math
    sample_size = min(int(math.log2(len(existing_vectors)) * 10) + 1, len(existing_vectors))

    sampled = random.sample(existing_vectors, sample_size)
    max_sim = 0.0
    matched_id = None

    for item in sampled:
        vec = item.get("k_vector")
        if vec is None:
            continue
        sim = _cosine_similarity(new_vector, vec)
        if sim > max_sim:
            max_sim = sim
            matched_id = item.get("id")

    is_dup = max_sim > threshold
    return is_dup, matched_id, max_sim


def run_dedup_benchmark(
    sizes: list[int] | None = None,
    threshold: float = 0.95,
    repeat: int = 3,
    db_url: str = "",
    random_seed: int = 42,
) -> dict[str, Any]:
    """运行去重 Benchmark。

    优先使用真实 pgvector（通过 DatabaseAdapter），不可用时回退到模拟模式。

    Args:
        sizes: 知识库规模列表
        threshold: 去重阈值
        repeat: 每种规模重复次数
        db_url: 数据库连接 URL（为空时使用模拟模式）
        random_seed: 随机种子，确保可复现

    Returns:
        Benchmark 结果 dict
    """
    if sizes is None:
        sizes = [1000, 5000, 10000]

    # 固定随机种子，确保结果可复现
    random.seed(random_seed)

    logger.info("P0-2 pgvector 去重 Benchmark 开始")
    logger.info(f"  知识库规模: {sizes}")
    logger.info(f"  重复次数: {repeat}")
    logger.info(f"  随机种子: {random_seed}")

    # 尝试使用真实 pgvector
    db_adapter = None
    use_real_pgvector = False
    try:
        from knowledge_tree_builder.adapters.database import DatabaseAdapter
        if db_url:
            db_adapter = DatabaseAdapter(db_url)
            use_real_pgvector = True
            logger.info("  使用真实 pgvector 数据库")
        else:
            logger.info("  未提供 db_url，使用模拟模式")
    except ImportError as e:
        logger.warning(f"  DatabaseAdapter 不可用: {e}，使用模拟模式")

    results_by_size = []
    all_passed = True

    for size in sizes:
        logger.info(f"\n  知识库规模: {size}")

        # 生成测试数据（同一 seed 下可复现）
        existing_vectors = [
            {"id": i, "name": f"kp_{i}", "k_vector": _generate_random_vector()}
            for i in range(size)
        ]

        # 生成待去重的新知识点
        new_points_count = min(100, size // 10)
        new_vectors = [_generate_random_vector() for _ in range(new_points_count)]

        # 内存扫描 benchmark（baseline）
        memory_times = []
        memory_results = []
        for r in range(repeat):
            t_start = time.perf_counter()
            results = []
            for nv in new_vectors:
                is_dup, matched_id, sim = _dedup_memory_scan(nv, existing_vectors, threshold)
                results.append((is_dup, matched_id, sim))
            t_elapsed = (time.perf_counter() - t_start) * 1000  # ms
            memory_times.append(t_elapsed)
            if r == repeat - 1:
                memory_results = results
        avg_memory_time = sum(memory_times) / len(memory_times)

        # pgvector benchmark（真实或模拟）
        pgvector_times = []
        pgvector_results = []
        for r in range(repeat):
            t_start = time.perf_counter()
            results = []
            if use_real_pgvector and db_adapter is not None:
                # 真实 pgvector 搜索
                for nv in new_vectors:
                    neighbors = db_adapter.find_nearest_neighbors(
                        nv, threshold=threshold, limit=1
                    )
                    if neighbors:
                        top = neighbors[0]
                        results.append((True, top.get("id"), top.get("similarity", 0.0)))
                    else:
                        # 没找到阈值以上的，找最相似的
                        all_neighbors = db_adapter.find_nearest_neighbors(
                            nv, threshold=0.0, limit=1
                        )
                        if all_neighbors:
                            top = all_neighbors[0]
                            results.append((False, top.get("id"), top.get("similarity", 0.0)))
                        else:
                            results.append((False, None, 0.0))
            else:
                # 模拟模式
                for nv in new_vectors:
                    is_dup, matched_id, sim = _dedup_pgvector_simulated(
                        nv, existing_vectors, threshold
                    )
                    results.append((is_dup, matched_id, sim))
            t_elapsed = (time.perf_counter() - t_start) * 1000  # ms
            pgvector_times.append(t_elapsed)
            if r == repeat - 1:
                pgvector_results = results
        avg_pgvector_time = sum(pgvector_times) / len(pgvector_times)

        # 计算加速比
        speedup = avg_memory_time / avg_pgvector_time if avg_pgvector_time > 0 else 0

        # 计算一致性（内存扫描 vs pgvector 结果对比）
        # 比较 is_dup 布尔判断是否一致
        dup_matches = 0
        # 比较 matched_id 是否一致（仅当两边都判断为重复时）
        id_matches = 0
        total_comparisons = len(memory_results)
        for mem_res, pg_res in zip(memory_results, pgvector_results):
            md, mp, ms = mem_res
            pd, pp, ps = pg_res
            if md == pd:
                dup_matches += 1
            if md and pd and mp == pp:
                id_matches += 1
        consistency = dup_matches / total_comparisons if total_comparisons else 0.0

        # 验收标准
        expected_speedup = SPEEDUP_THRESHOLDS.get(size, 10.0)
        speedup_passed = speedup >= expected_speedup
        consistency_passed = consistency >= 0.99  # 99% 一致性
        passed = speedup_passed and consistency_passed
        if not passed:
            all_passed = False

        result = {
            "size": size,
            "memory_time_ms": round(avg_memory_time, 2),
            "pgvector_time_ms": round(avg_pgvector_time, 2),
            "speedup": round(speedup, 1),
            "expected_speedup": expected_speedup,
            "consistency": round(consistency, 4),
            "speedup_passed": speedup_passed,
            "consistency_passed": consistency_passed,
            "passed": passed,
            "real_pgvector": use_real_pgvector,
        }

        results_by_size.append(result)

        mode_label = "真实 pgvector" if use_real_pgvector else "模拟模式"
        logger.info(f"    模式: {mode_label}")
        logger.info(f"    内存扫描耗时: {avg_memory_time:.1f}ms")
        logger.info(f"    pgvector 耗时: {avg_pgvector_time:.1f}ms")
        logger.info(f"    加速比: {speedup:.1f}x (期望 {expected_speedup}x)")
        logger.info(f"    一致性: {consistency:.1%}")
        logger.info(f"    验收通过: {'✅' if passed else '❌'}")

    logger.info(f"\n  总体验收通过: {'✅' if all_passed else '❌'}")

    return {
        "results_by_size": results_by_size,
        "all_passed": all_passed,
        "real_pgvector": use_real_pgvector,
    }
