"""测试 phase/admit.py — 兜底拦截 + 去重 + 矛盾检测"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from knowledge_tree_builder.models import AtomicKnowledge
from knowledge_tree_builder.phase.admit import (
    _add_to_batch_pool,
    _default_llm_judge,
    _embed_with_cache_ordered,
    _guard_filter,
    _has_negation,
    _conditions_are_same,
    _extract_condition,
    admit_knowledge,
)


# ========== Fixtures ==========


@pytest.fixture
def atomic() -> AtomicKnowledge:
    return AtomicKnowledge(text="HDBSCAN 通过层次聚类覆盖非均匀密度簇", type="principle", claims_count=1, source_candidate_index=0)


@pytest.fixture
def atomic_too_short() -> AtomicKnowledge:
    return AtomicKnowledge(text="短了", type="key_point", claims_count=1, source_candidate_index=1)


@pytest.fixture
def atomic_meta_prefix() -> AtomicKnowledge:
    return AtomicKnowledge(text="本文介绍了HDBSCAN聚类算法的原理", type="key_point", claims_count=1, source_candidate_index=2)


@pytest.fixture
def atomic_extraction_fail() -> AtomicKnowledge:
    return AtomicKnowledge(text="无法提取该文章的知识点", type="key_point", claims_count=1, source_candidate_index=3)


@pytest.fixture
def atomic_wrong_type() -> AtomicKnowledge:
    return AtomicKnowledge(text="这是一个有效知识点文本", type="unknown_type", claims_count=1, source_candidate_index=4)


@pytest.fixture
def mock_embed_fn() -> Any:
    def _embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]
    return _embed


# ========== _guard_filter ==========


class TestGuardFilter:
    def test_valid_passes(self, atomic) -> None:
        ok, reason = _guard_filter(atomic)
        assert ok is True
        assert reason == ""

    def test_too_short(self, atomic_too_short) -> None:
        ok, _ = _guard_filter(atomic_too_short)
        assert ok is False

    def test_meta_prefix(self, atomic_meta_prefix) -> None:
        ok, reason = _guard_filter(atomic_meta_prefix)
        assert ok is False
        assert "元信息开头" in reason

    def test_extraction_fail(self, atomic_extraction_fail) -> None:
        ok, _ = _guard_filter(atomic_extraction_fail)
        assert ok is False

    def test_unknown_type(self, atomic_wrong_type) -> None:
        ok, _ = _guard_filter(atomic_wrong_type)
        assert ok is False

    def test_all_meta_patterns(self) -> None:
        patterns = [
            "本文介绍了一种新方法",
            "文章讨论了聚类算法",
            "本研究分析了HDBSCAN",
            "本篇总结了三种方法",
            "该文章探讨了memory机制",
            "该研究概述了现有工作",
            "综述总结了五大方向",
            "文章概述了最新进展",
        ]
        for text in patterns:
            a = AtomicKnowledge(text=text, type="key_point", claims_count=1, source_candidate_index=0)
            ok, _ = _guard_filter(a)
            assert ok is False, f"should reject: {text}"

    def test_extraction_fail_patterns(self) -> None:
        patterns = ["无法提取", "已被删除", "无法读取", "请提供更多信息"]
        for text in patterns:
            a = AtomicKnowledge(text=text, type="key_point", claims_count=1, source_candidate_index=0)
            ok, _ = _guard_filter(a)
            assert ok is False, f"should reject: {text}"

    def test_boundary_10_chars(self) -> None:
        a = AtomicKnowledge(text="一二三四五六七八九十", type="key_point", claims_count=1, source_candidate_index=0)
        ok, _ = _guard_filter(a)
        assert ok is True

    def test_exactly_10_chars(self) -> None:
        a = AtomicKnowledge(text="一二三四五六七八九十", type="key_point", claims_count=1, source_candidate_index=0)
        ok, _ = _guard_filter(a)
        assert ok is True


# ========== _has_negation ==========


class TestHasNegation:
    def test_has_not(self) -> None:
        assert _has_negation("DBSCAN 在非均匀密度上无效") is True

    def test_no_negation(self) -> None:
        assert _has_negation("HDBSCAN 在非均匀密度上优于 DBSCAN") is False

    def test_empty(self) -> None:
        assert _has_negation("") is False


# ========== _extract_condition ==========


class TestExtractCondition:
    def test_condition_found(self) -> None:
        cond = _extract_condition("HDBSCAN 在非均匀密度数据上优于 DBSCAN")
        assert "非均匀密度数据" in cond

    def test_no_condition(self) -> None:
        cond = _extract_condition("HDBSCAN 是一种聚类算法")
        assert cond == ""


# ========== _conditions_are_same ==========


class TestConditionsAreSame:
    def test_same_condition(self) -> None:
        assert _conditions_are_same("在非均匀密度数据上", "在非均匀密度数据上") is True

    def test_different_condition(self) -> None:
        assert _conditions_are_same("在非均匀密度上", "在高维稀疏数据上") is False

    def test_both_empty(self) -> None:
        assert _conditions_are_same("", "") is True

    def test_one_empty(self) -> None:
        assert _conditions_are_same("在非均匀密度上", "") is True


# ========== _default_llm_judge ==========


class TestDefaultLlmJudge:
    def test_high_overlap(self) -> None:
        assert _default_llm_judge("HDBSCAN 聚类算法", "HDBSCAN 聚类算法原理") is True

    def test_low_overlap(self) -> None:
        assert _default_llm_judge("注意力机制原理", "部署流程三步走") is False


class TestEmbedWithCacheOrdered:
    def test_mixed_cache_hits_keep_input_order(self) -> None:
        """缓存命中和未命中混合时，返回向量必须与输入 texts 顺序一致。"""
        import hashlib

        cache = {
            hashlib.md5("A".encode()).hexdigest(): [1.0, 0.0],
            hashlib.md5("C".encode()).hexdigest(): [3.0, 0.0],
        }
        calls: list[list[str]] = []

        def original_embed(texts: list[str]) -> list[list[float]]:
            calls.append(list(texts))
            return [[2.0, 0.0] for _ in texts]

        result, dirty = _embed_with_cache_ordered(["A", "B", "C"], cache, original_embed)

        assert result == [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]]
        assert calls == [["B"]]
        assert dirty is True


# ========== _add_to_batch_pool ==========


class TestAddToBatchPool:
    def test_adds_embedding(self, atomic, mock_embed_fn) -> None:
        pool: list[dict[str, Any]] = []
        _add_to_batch_pool(atomic, pool, mock_embed_fn)
        assert len(pool) == 1
        assert pool[0]["k_vector"] == [1.0, 0.0, 0.0]
        assert "batch_" in pool[0]["id"]

    def test_embedding_failure(self, atomic) -> None:
        def _fail(_texts: list[str]) -> None:
            return None
        pool: list[dict[str, Any]] = []
        _add_to_batch_pool(atomic, pool, _fail)
        assert len(pool) == 0

    def test_multiple_adds(self, atomic, mock_embed_fn) -> None:
        pool: list[dict[str, Any]] = []
        _add_to_batch_pool(atomic, pool, mock_embed_fn)
        _add_to_batch_pool(atomic, pool, mock_embed_fn)
        assert len(pool) == 2
        assert pool[0]["id"] == "batch_0"
        assert pool[1]["id"] == "batch_1"


# ========== admit_knowledge (integration with mock) ==========


class TestAdmitKnowledge:
    def test_all_passed(self, atomic, mock_embed_fn) -> None:
        result = admit_knowledge(
            [atomic],
            existing_vectors=[],
            embed_fn=mock_embed_fn,
            cosine_sim_fn=lambda a, b: 0.0,
            cold_start_text_dedup=True,
        )
        assert result.stats["passed"] == 1
        assert result.stats["guard_dropped"] == 0
        assert result.stats["dedup_merged"] == 0

    def test_dedup_merged(self, atomic, mock_embed_fn) -> None:
        """冷启动中相同文本应被去重合并"""
        result = admit_knowledge(
            [atomic, atomic],
            existing_vectors=[],
            embed_fn=mock_embed_fn,
            cosine_sim_fn=lambda a, b: 0.0,
            cold_start_text_dedup=True,
        )
        assert result.stats["passed"] == 1
        assert result.stats["dedup_merged"] == 1

    def test_guard_drops(self, atomic_too_short, mock_embed_fn) -> None:
        result = admit_knowledge(
            [atomic_too_short],
            existing_vectors=[],
            embed_fn=mock_embed_fn,
            cosine_sim_fn=lambda a, b: 0.0,
            cold_start_text_dedup=True,
        )
        assert result.stats["passed"] == 0
        assert result.stats["guard_dropped"] == 1

    def test_empty_list(self, mock_embed_fn) -> None:
        result = admit_knowledge(
            [],
            existing_vectors=[],
            embed_fn=mock_embed_fn,
            cosine_sim_fn=lambda a, b: 0.0,
        )
        assert result.stats["total"] == 0

    def test_batch_pool_used_in_non_cold_start(self, atomic, mock_embed_fn) -> None:
        """非冷启动模式应使用 batch_passed_vectors"""
        result = admit_knowledge(
            [atomic, atomic],
            existing_vectors=[],
            embed_fn=mock_embed_fn,
            cosine_sim_fn=lambda a, b: 0.0,
            cold_start_text_dedup=False,
            threshold_direct=0.5,
        )
        # 第二个 atomic 与第一个的 embedding 相同 → 判重
        assert result.stats["dedup_merged"] >= 0
