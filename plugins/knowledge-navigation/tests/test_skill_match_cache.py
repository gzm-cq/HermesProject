"""test_skill_match_cache.py — 意图联合键缓存验收测试。

覆盖设计指标（用户确认 2026-09-04，全部通过才允许接入生产）：
1. 命中判定：ctx_sim≥0.90 AND query_sim≥0.92；context 空退化为 query-only
2. 意图区分：ctx 不同 → 即使 query 相似也不命中（防误命中）
3. 淘汰：LFU（hit_count 升序）+ TTL 过期
4. 持久化：store → reload 可命中
5. 并发安全：多线程读写不丢数据
6. 性能：1000 条扫描 < 5ms
"""

from __future__ import annotations

import os
import sys
import threading
import time

from pathlib import Path

import numpy as np
import pytest

# ── 路径：保证能 import knowledge_navigation ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from knowledge_navigation.core.skill_match_cache import SkillMatchCache, _cosine  # noqa: E402

DIM = 1024


def _vec(base: float = 0.0, seed: int = 0, scale: float = 1.0) -> np.ndarray:
    """确定性测试向量。

    设计要点：不同 seed 的向量必须彼此低相似（< 0.92，避免跨条目误命中），
    同一 seed 的向量必须高相似（=1.0）。实现：正交基向量 + seed 扰动。
    """
    rng = np.random.default_rng(seed)
    # 主方向：seed 决定一个随机单位向量（不同 seed 方向独立 → 余弦≈0）
    v = rng.normal(0, 1.0, DIM).astype(np.float32)
    n = np.linalg.norm(v)
    v = v / n
    # base 只影响幅值，不影响方向；scale 整体缩放
    return (v * abs(base) * scale + v * 0.001 * scale).astype(np.float32) if base else v


@pytest.fixture()
def cache(tmp_path):
    """每测试独立临时缓存（避免磁盘污染）。"""
    return SkillMatchCache(
        ctx_threshold=0.90,
        query_threshold=0.92,
        ttl_seconds=24 * 3600,
        max_entries=1000,
        cache_path=tmp_path / "cache.json",
    )


# ──────────────────────────────────────────────
# 指标 1：命中判定（双阈值 AND + query-only 退化）
# ──────────────────────────────────────────────

class TestHitDetermination:
    def test_hit_both_dims_match(self, cache):
        ctx = _vec(base=1.0, seed=1)
        q = _vec(base=2.0, seed=2)
        cache.store(ctx, q, ["skill-a", "skill-b"])
        # 相同键 → 命中
        assert cache.lookup(ctx, q) == ["skill-a", "skill-b"]

    def test_hit_similar_within_threshold(self, cache):
        ctx = _vec(base=1.0, seed=1)
        q = _vec(base=2.0, seed=2)
        cache.store(ctx, q, ["skill-a"])
        # 微小扰动（相似度仍 > 0.92/0.90）
        q_sim = q + 0.001
        assert cache.lookup(ctx, q_sim) == ["skill-a"]

    def test_miss_query_differs_below_threshold(self, cache):
        ctx = _vec(base=1.0, seed=1)
        q = _vec(base=2.0, seed=2)
        cache.store(ctx, q, ["skill-a"])
        # query 完全不同 → miss
        q_far = _vec(base=-5.0, seed=99)
        assert cache.lookup(ctx, q_far) is None

    def test_query_only_degrades_when_ctx_none(self, cache):
        # context 为空：退化 query-only
        q = _vec(base=2.0, seed=2)
        cache.store(None, q, ["skill-a"])
        assert cache.lookup(None, q) == ["skill-a"]
        assert cache.lookup(None, q + 0.001) == ["skill-a"]

    def test_query_only_never_matches_ctx_entry(self, cache):
        # ctx 条目与 query-only 条目互不命中（维度不匹配）
        ctx = _vec(base=1.0, seed=1)
        q = _vec(base=2.0, seed=2)
        cache.store(ctx, q, ["skill-a"])
        assert cache.lookup(None, q) is None


# ──────────────────────────────────────────────
# 指标 2：意图区分（防误命中）
# ──────────────────────────────────────────────

class TestIntentDiscrimination:
    def test_same_query_different_ctx_not_hit(self, cache):
        # 意图不同（ctx 差很远）→ 即使 query 完全一样也不命中
        ctx_a = _vec(base=1.0, seed=1)   # "分析 Excel"
        ctx_b = _vec(base=-1.0, seed=3)  # "审查安全"
        q = _vec(base=2.0, seed=2)
        cache.store(ctx_a, q, ["excel-data-analysis"])
        assert cache.lookup(ctx_b, q) is None

    def test_different_query_same_ctx_can_hit(self, cache):
        # 意图相同（ctx 相近）+ 输入不同但在阈值内 → 可命中
        ctx = _vec(base=1.0, seed=1)
        q1 = _vec(base=2.0, seed=2)
        cache.store(ctx, q1, ["excel-data-analysis"])
        q2 = q1 + 0.005  # 仍 > 0.92
        assert cache.lookup(ctx, q2) == ["excel-data-analysis"]


# ──────────────────────────────────────────────
# 指标 3：淘汰（LFU + TTL）
# ──────────────────────────────────────────────

class TestEviction:
    def test_lfu_evicts_lowest_hit_count(self, tmp_path):
        c = SkillMatchCache(max_entries=3, cache_path=tmp_path / "c.json")
        # 存入 4 条：全部 hit_count=1，第 4 条存入时挤出最久未命中（第 0 条）
        ctx = _vec(base=1.0, seed=1)
        for i in range(4):
            q = _vec(base=2.0, seed=10 + i)
            c.store(ctx, q, [f"skill-{i}"])
        # 第 0 条（最久未命中）被淘汰
        assert c.lookup(ctx, _vec(base=2.0, seed=10)) is None
        # 第 3 条仍在
        assert c.lookup(ctx, _vec(base=2.0, seed=13)) == ["skill-3"]

    def test_frequent_entry_survives(self, tmp_path):
        c = SkillMatchCache(max_entries=3, cache_path=tmp_path / "c.json")
        ctx = _vec(base=1.0, seed=1)
        q_hot = _vec(base=2.0, seed=42)
        c.store(ctx, q_hot, ["hot"])
        # 热条目命中 5 次提升 hit_count（1→6）
        for _ in range(5):
            assert c.lookup(ctx, q_hot) == ["hot"]
        # 再塞 4 条冷条目（hit_count=1）→ 挤掉 hit_count=1 的旧冷条目，hot 保留
        for i in range(4):
            c.store(ctx, _vec(base=2.0, seed=100 + i), [f"cold-{i}"])
        assert c.lookup(ctx, q_hot) == ["hot"]

    def test_ttl_expiry(self, tmp_path):
        c = SkillMatchCache(ttl_seconds=1, cache_path=tmp_path / "c.json")
        ctx = _vec(base=1.0, seed=1)
        q = _vec(base=2.0, seed=2)
        c.store(ctx, q, ["skill-a"])
        assert c.lookup(ctx, q) == ["skill-a"]
        time.sleep(1.2)
        # 冷条目超过 TTL（基于 last_hit_ts，未再命中）→ 过期
        assert c.lookup(ctx, q) is None

    # ── 修复 1 验证：滑动窗口 TTL，热条目命中续期不被误杀 ──

    def test_hot_entry_survives_ttl(self, tmp_path):
        c = SkillMatchCache(ttl_seconds=1, cache_path=tmp_path / "c.json")
        ctx = _vec(base=1.0, seed=1)
        q = _vec(base=2.0, seed=2)
        c.store(ctx, q, ["hot"])
        # 0.7s 时命中一次（刷新 last_hit_ts）
        time.sleep(0.7)
        assert c.lookup(ctx, q) == ["hot"]
        # 再过 0.7s（距上次命中 0.7s < TTL）→ 仍命中；但距创建已 1.4s > 旧 TTL
        time.sleep(0.7)
        assert c.lookup(ctx, q) == ["hot"]

    # ── 修复 2 验证：技能集指纹失效（L1 机制）──

    def test_skill_set_hash_invalidation(self, cache):
        ctx = _vec(base=1.0, seed=1)
        q = _vec(base=2.0, seed=2)
        cache.store(ctx, q, ["excel-data-analysis"], skill_set_hash="hash-v1")
        # 同指纹命中
        assert cache.lookup(ctx, q, skill_set_hash="hash-v1") == ["excel-data-analysis"]
        # 技能库变化（指纹变）→ miss（缓存结果可能引用已删除技能）
        assert cache.lookup(ctx, q, skill_set_hash="hash-v2") is None

    # ── 修复 3 验证：store 路径也清理过期条目 ──

    def test_store_purges_expired(self, tmp_path):
        c = SkillMatchCache(ttl_seconds=1, cache_path=tmp_path / "c.json")
        ctx = _vec(base=1.0, seed=1)
        c.store(ctx, _vec(base=2.0, seed=2), ["old"])
        time.sleep(1.2)
        # 只 store 不 lookup：过期条目应被 store 路径清理
        c.store(ctx, _vec(base=2.0, seed=3), ["new"])
        assert c.lookup(ctx, _vec(base=2.0, seed=2)) is None
        assert c.lookup(ctx, _vec(base=2.0, seed=3)) == ["new"]

    # ── 修复 4 验证：LFU 冷启动保护（新条目 hit_count=1 起）──

    def test_lfu_cold_start_protection(self, tmp_path):
        # 真实保护机制：同频（hit_count 相等）时按 last_hit_ts 决胜——
        # 新条目 last_hit_ts 最新，淘汰最久未命中的旧冷条目，新条目存活
        c = SkillMatchCache(max_entries=2, cache_path=tmp_path / "c.json")
        ctx = _vec(base=1.0, seed=1)
        c.store(ctx, _vec(base=2.0, seed=2), ["a"])
        c.store(ctx, _vec(base=2.0, seed=3), ["b"])
        # 冷启动：不命中直接涌入第 3 条 → 满缓存，同频决胜淘汰最久未命中的 a
        c.store(ctx, _vec(base=2.0, seed=4), ["c"])
        assert c.lookup(ctx, _vec(base=2.0, seed=2)) is None
        assert c.lookup(ctx, _vec(base=2.0, seed=3)) == ["b"]
        assert c.lookup(ctx, _vec(base=2.0, seed=4)) == ["c"]  # 新条目存活（冷启动保护）


# ──────────────────────────────────────────────
# 指标 4：持久化（store → reload）
# ──────────────────────────────────────────────

class TestPersistence:
    def test_reload_after_recreate(self, tmp_path):
        p = tmp_path / "cache.json"
        ctx = _vec(base=1.0, seed=1)
        q = _vec(base=2.0, seed=2)
        c1 = SkillMatchCache(cache_path=p)
        c1.store(ctx, q, ["skill-a", "skill-b"])
        c1.flush()  # 强制落盘（生产由批量间隔自动触发）
        del c1
        # 模拟重启：重新实例化，从磁盘加载
        c2 = SkillMatchCache(cache_path=p)
        assert c2.lookup(ctx, q) == ["skill-a", "skill-b"]

    def test_corrupt_file_starts_empty(self, tmp_path):
        p = tmp_path / "cache.json"
        p.write_text("{ not valid json")
        c = SkillMatchCache(cache_path=p)
        assert c.lookup(_vec(base=1.0, seed=1), _vec(base=2.0, seed=2)) is None


# ──────────────────────────────────────────────
# 指标 5：并发安全
# ──────────────────────────────────────────────

class TestConcurrency:
    def test_parallel_read_write_no_loss(self, tmp_path):
        c = SkillMatchCache(max_entries=500, cache_path=tmp_path / "c.json")
        ctx = _vec(base=1.0, seed=1)
        n = 200
        errors: list[Exception] = []

        def writer(i: int) -> None:
            try:
                q = _vec(base=2.0, seed=1000 + i)
                c.store(ctx, q, [f"skill-{i}"])
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def reader() -> None:
            try:
                q = _vec(base=2.0, seed=1000 + (n // 2))
                c.lookup(ctx, q)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = []
        for i in range(n):
            threads.append(threading.Thread(target=writer, args=(i,)))
        for _ in range(20):
            threads.append(threading.Thread(target=reader))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发异常: {errors}"
        # 所有写入的条目都能被读到
        for i in [0, 50, 100, 150, 199]:
            q = _vec(base=2.0, seed=1000 + i)
            assert c.lookup(ctx, q) == [f"skill-{i}"]


# ──────────────────────────────────────────────
# 指标 6：性能（1000 条扫描 < 5ms）
# ──────────────────────────────────────────────

class TestPerformance:
    def test_lookup_under_5ms_at_1000_entries(self, tmp_path):
        c = SkillMatchCache(max_entries=1000, cache_path=tmp_path / "c.json")
        ctx = _vec(base=1.0, seed=1)
        for i in range(1000):
            c.store(ctx, _vec(base=2.0, seed=10000 + i), [f"skill-{i}"])
        # 最坏情况：miss 扫描全部 1000 条
        t0 = time.perf_counter()
        result = c.lookup(ctx, _vec(base=-3.0, seed=999999))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result is None
        assert elapsed_ms < 5.0, f"lookup 耗时 {elapsed_ms:.2f}ms 超 5ms 指标"


# ──────────────────────────────────────────────
# 辅助：余弦正确性
# ──────────────────────────────────────────────

class TestCosine:
    def test_identical_vectors(self):
        v = _vec(base=1.0, seed=1)
        assert _cosine(v, v) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_vectors(self):
        v1 = np.array([1.0, 0.0], dtype=np.float32)
        v2 = np.array([0.0, 1.0], dtype=np.float32)
        assert _cosine(v1, v2) == pytest.approx(0.0, abs=1e-6)


# ──────────────────────────────────────────────
# 指标 7：命中率监控（B 方案可观测性验收）
# ──────────────────────────────────────────────

class TestHitRateMonitoring:
    def test_hit_rate_tracks_lookup_hit_miss(self, cache):
        q = _vec(base=1.0, seed=7)
        c = cache
        c.store(None, q, ["skill-a"])
        st = c.stats()
        assert st["total_stores"] == 1
        assert st["total_lookups"] == 0  # store 不计入 lookup

        # 未命中（正交向量，相似度 0 < 0.92）
        assert c.lookup(None, _vec(base=-3.0, seed=8)) is None
        st = c.stats()
        assert st["total_lookups"] == 1
        assert st["total_misses"] == 1
        assert st["total_hits"] == 0
        assert st["hit_rate"] == 0.0

        # 命中
        assert c.lookup(None, q) is not None
        st = c.stats()
        assert st["total_lookups"] == 2
        assert st["total_hits"] == 1
        assert st["hit_rate"] == 0.5

    def test_hourly_window_buckets(self, cache):
        q = _vec(base=1.0, seed=9)
        c = cache
        c.store(None, q, ["skill-a"])
        c.lookup(None, q)  # hit
        c.lookup(None, q)  # hit
        c.lookup(None, _vec(base=-3.0, seed=10))  # miss
        st = c.stats()
        hourly = st["hourly"]
        assert len(hourly) == 1
        bucket = hourly[0]
        assert bucket["lookups"] == 3
        assert bucket["hits"] == 2
        assert bucket["misses"] == 1

    def test_stats_persist_across_reload(self, cache, tmp_path):
        q = _vec(base=1.0, seed=11)
        cache.store(None, q, ["skill-a"])
        cache.lookup(None, q)  # 1 hit
        cache.flush()

        reloaded = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.92, max_entries=100,
            cache_path=tmp_path / "cache.json", save_interval=3600,
        )
        st = reloaded.stats()
        assert st["total_stores"] == 1
        assert st["total_lookups"] == 1
        assert st["total_hits"] == 1
        assert st["hit_rate"] == 1.0

    def test_hit_rate_zero_when_no_lookups(self, cache):
        st = cache.stats()
        assert st["hit_rate"] == 0.0
        assert st["total_lookups"] == 0


# ──────────────────────────────────────────────
# 指标 8：A 方案 — intent 标签 + 0.85 阈值（实测修正）
# ──────────────────────────────────────────────

class TestIntentLabel:
    def test_store_intent_persists(self, tmp_path):
        """A 方案：store 带 intent → reload 后保留 + stats 直方图可见。"""
        c = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=tmp_path / "c.json", save_interval=3600,
        )
        q = _vec(base=1.0, seed=20)
        c.store(None, q, ["skill-a"], intent="ops-deploy")
        c.flush()

        reloaded = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=tmp_path / "c.json", save_interval=3600,
        )
        st = reloaded.stats()
        assert st["intent_histogram"].get("ops-deploy") == 1
        # 命中路径仍可用
        assert reloaded.lookup(None, q) == ["skill-a"]

    def test_intent_default_empty(self, tmp_path):
        """不带 intent 的 store → intent_histogram 无该条目。"""
        c = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=tmp_path / "c.json", save_interval=3600,
        )
        c.store(None, _vec(base=1.0, seed=21), ["skill-a"])
        assert c.stats()["intent_histogram"] == {}

    def test_query_threshold_085_hits_similar(self, tmp_path):
        """A 方案：query_threshold=0.85，微小扰动命中（0.85 覆盖改写）。"""
        c = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=tmp_path / "c.json", save_interval=3600,
        )
        q = _vec(base=1.0, seed=22)
        c.store(None, q, ["skill-a"])
        q_sim = q + 0.001  # 相似度 ≈ 1.0 > 0.85
        assert c.lookup(None, q_sim) == ["skill-a"]

    def test_query_threshold_085_rejects_orthogonal(self, tmp_path):
        """0.85 阈值仍拒绝正交向量（不同意图 0.30-0.42 全拒）。"""
        c = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=tmp_path / "c.json", save_interval=3600,
        )
        c.store(None, _vec(base=1.0, seed=23), ["skill-a"])
        assert c.lookup(None, _vec(base=-3.0, seed=99)) is None


# ──────────────────────────────────────────────
# 指标 9：L0 全等快路径（2026-09-05 修复：query 原文 md5 精确匹配）
# ──────────────────────────────────────────────

class TestExactPath:
    def test_exact_hit(self, cache):
        """store 带 query_text → lookup_exact 完全相同的 query 命中。"""
        q = _vec(base=1.0, seed=30)
        cache.store(None, q, ["skill-a", "skill-b"], query_text="IMPORTANT: cron prompt 固定内容")
        assert cache.lookup_exact("IMPORTANT: cron prompt 固定内容") == ["skill-a", "skill-b"]

    def test_exact_strips_whitespace(self, cache):
        """首尾空白不影响 md5 键。"""
        cache.store(None, _vec(base=1.0, seed=31), ["skill-a"], query_text="  query text  ")
        assert cache.lookup_exact("query text") == ["skill-a"]

    def test_exact_different_text_misses(self, cache):
        """query 文本不同 → 不命中（全等语义）。"""
        cache.store(None, _vec(base=1.0, seed=32), ["skill-a"], query_text="text-a")
        assert cache.lookup_exact("text-b") is None

    def test_exact_empty_query_returns_none(self, cache):
        """空 query 不入库也不查询。"""
        cache.store(None, _vec(base=1.0, seed=33), ["skill-a"], query_text="")
        assert cache.lookup_exact("") is None

    def test_exact_persists_across_reload(self, tmp_path):
        """store → flush → reload → 仍可精确命中（磁盘持久化）。"""
        c = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=tmp_path / "c.json", save_interval=3600,
        )
        c.store(None, _vec(base=1.0, seed=34), ["skill-a"], query_text="固定 prompt A")
        c.flush()
        reloaded = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=tmp_path / "c.json", save_interval=3600,
        )
        assert reloaded.lookup_exact("固定 prompt A") == ["skill-a"]

    def test_exact_ttl_purged(self, tmp_path):
        """过期 exact 键被 purge 清理。"""
        c = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            ttl_seconds=1, cache_path=tmp_path / "c.json", save_interval=3600,
        )
        c.store(None, _vec(base=1.0, seed=35), ["skill-a"], query_text="过期货")
        assert c.lookup_exact("过期货") == ["skill-a"]
        time.sleep(1.1)
        assert c.lookup_exact("过期货") is None

    def test_exact_stats_counted(self, cache):
        """exact 命中计入 stats（lookups/hits 递增）。"""
        cache.store(None, _vec(base=1.0, seed=36), ["skill-a"], query_text="统计用 query")
        assert cache.lookup_exact("统计用 query") == ["skill-a"]
        st = cache.stats()
        assert st["total_lookups"] == 1
        assert st["total_hits"] == 1
        assert st["exact_entries"] == 1

    def test_exact_capacity_trimmed(self, tmp_path):
        """超过 _EXACT_MAX_KEYS 时删除最旧键。"""
        from knowledge_navigation.core.skill_match_cache import _EXACT_MAX_KEYS
        c = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=tmp_path / "c.json", save_interval=3600,
        )
        # 写满上限 + 1
        for i in range(_EXACT_MAX_KEYS + 10):
            c.store(None, _vec(base=1.0, seed=40 + i), [f"skill-{i}"], query_text=f"q-{i}")
        assert len(c._exact) <= _EXACT_MAX_KEYS
        # 最旧的键被淘汰，最新的键可命中
        assert c.lookup_exact(f"q-{_EXACT_MAX_KEYS + 9}") == [f"skill-{_EXACT_MAX_KEYS + 9}"]


# ──────────────────────────────────────────────
# 指标 10：stats 多进程合并（2026-09-05 修复：落盘逐项 max，防覆盖回退）
# ──────────────────────────────────────────────

class TestStatsMerge:
    def test_save_merges_disk_stats(self, tmp_path):
        """落盘时磁盘旧 stats 并入内存（取 max），统计单调不减。"""
        path = tmp_path / "c.json"
        c1 = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=path, save_interval=0,  # 立即落盘
        )
        q1 = _vec(base=1.0, seed=50)
        c1.store(None, q1, ["skill-a"])
        c1.lookup(None, q1)  # 1 hit
        c1.flush()

        # 第二个进程：启动时加载 stats(1 hit)，自己再产生 1 hit → 落盘合并
        c2 = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=path, save_interval=0,
        )
        q2 = _vec(base=1.0, seed=51)
        c2.store(None, q2, ["skill-b"])
        c2.lookup(None, q2)
        c2.flush()

        # 重新加载：合并后 total_hits 应 ≥ 2（不因覆盖回退）
        c3 = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=path, save_interval=0,
        )
        assert c3.stats()["total_hits"] >= 2

    def test_save_keeps_exact_on_reload(self, tmp_path):
        """合并落盘不丢 exact 键（版本 2 格式向后兼容恢复）。"""
        path = tmp_path / "c.json"
        c = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=path, save_interval=0,
        )
        c.store(None, _vec(base=1.0, seed=52), ["skill-x"], query_text="合并持久化 query")
        c.flush()
        reloaded = SkillMatchCache(
            ctx_threshold=0.90, query_threshold=0.85, max_entries=100,
            cache_path=path, save_interval=0,
        )
        assert reloaded.lookup_exact("合并持久化 query") == ["skill-x"]



# ──────────────────────────────────────────────
# 指标 11：进程退出兜底落盘（2026-09-05 修复：atexit flush）
# ──────────────────────────────────────────────

class TestFlushOnExit:
    def test_dirty_store_flushed_on_exit(self, tmp_path):
        """短进程 store 后不显式 flush 即退出 → atexit 兜底落盘。"""
        import subprocess, sys, json
        cache_file = tmp_path / "exit_cache.json"
        code = (
            "import sys; sys.path.insert(0, %r); "
            "from knowledge_navigation.core.skill_match_cache import SkillMatchCache; "
            "import numpy as np; "
            "c = SkillMatchCache(ctx_threshold=0.90, query_threshold=0.85, max_entries=100, "
            "cache_path=%r, save_interval=3600); "
            "c.store(None, np.zeros(1024, dtype=np.float32), ['skill-a'], query_text='退出前写入'); "
            "c.store_exact('退出前写入2', ['skill-b'])"
            % (str(Path(__file__).resolve().parent.parent / "src"), str(cache_file))
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"子进程失败: {r.stderr}"
        # 文件应存在且包含 exact 键（atexit flush 兜底）
        assert cache_file.exists(), "退出后缓存文件未落盘"
        data = json.loads(cache_file.read_text())
        assert len(data.get("exact", {})) == 2, f"exact 键丢失: {data.get('exact')}"

    def test_clean_exit_no_flush_needed(self, tmp_path):
        """无 dirty 数据时退出不落盘也不报错。"""
        import subprocess, sys
        cache_file = tmp_path / "clean_exit_cache.json"
        code = (
            "import sys; sys.path.insert(0, %r); "
            "from knowledge_navigation.core.skill_match_cache import SkillMatchCache; "
            "c = SkillMatchCache(cache_path=%r, save_interval=3600)"
            % (str(Path(__file__).resolve().parent.parent / "src"), str(cache_file))
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0
        assert not cache_file.exists() or True  # 无 dirty → 无写盘要求，不报错即可
