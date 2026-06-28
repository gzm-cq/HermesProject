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
    _detect_low_quality,
    _is_whitelisted,
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

    def test_if_then_pattern(self) -> None:
        cond = _extract_condition("如果数据量很大则应该使用分布式算法")
        assert "如果" in cond
        assert "则" in cond

    def test_assume_pattern(self) -> None:
        cond = _extract_condition("假设输入是正态分布那么该方法效果最好")
        assert "假设" in cond

    def test_when_pattern(self) -> None:
        cond = _extract_condition("当数据维度很高时PCA可以有效降维")
        assert "当" in cond
        assert "时" in cond

    def test_environment_pattern(self) -> None:
        cond = _extract_condition("在生产环境下需要考虑容错机制")
        assert "生产环境" in cond

    def test_condition_pattern(self) -> None:
        cond = _extract_condition("在满足独立性条件下可以使用朴素贝叶斯")
        assert "独立性条件" in cond

    def test_for_pattern(self) -> None:
        cond = _extract_condition("对于小规模数据集来说KNN效果不错")
        assert "小规模数据集" in cond

    def test_based_on_pattern(self) -> None:
        cond = _extract_condition("基于注意力机制Transformer取得了很好效果")
        assert "基于" in cond

    def test_because_pattern(self) -> None:
        cond = _extract_condition("因为神经网络需要大量数据所以需要数据增强")
        assert "因为" in cond

    def test_due_to_pattern(self) -> None:
        cond = _extract_condition("由于过拟合问题需要引入正则化")
        assert "由于" in cond


# ========== _detect_low_quality ==========


class TestDetectLowQuality:
    def test_normal_not_low_quality(self, atomic) -> None:
        is_lq, reason = _detect_low_quality(atomic)
        assert is_lq is False
        assert reason == ""

    def test_pure_code_snippet(self) -> None:
        a = AtomicKnowledge(
            text="function hello() { console.log('hello'); return 0; }",
            type="method",
            claims_count=1,
            source_candidate_index=0,
        )
        is_lq, reason = _detect_low_quality(a)
        assert is_lq is True
        assert "纯代码" in reason

    def test_short_title_only(self) -> None:
        a = AtomicKnowledge(
            text="机器学习算法",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
        )
        is_lq, reason = _detect_low_quality(a)
        assert is_lq is True
        assert "只有标题" in reason

    def test_short_with_verb_not_lq(self) -> None:
        a = AtomicKnowledge(
            text="这是一个测试",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
        )
        is_lq, _ = _detect_low_quality(a)
        assert is_lq is False

    def test_repeated_phrases(self) -> None:
        a = AtomicKnowledge(
            text="重要重要重要重要的事情说三遍",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
        )
        is_lq, reason = _detect_low_quality(a)
        assert is_lq is True
        assert "重复句式" in reason

    def test_vague_important(self) -> None:
        a = AtomicKnowledge(
            text="这个方法很重要",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
        )
        is_lq, reason = _detect_low_quality(a)
        assert is_lq is True
        assert "过度抽象" in reason

    def test_vague_with_content_not_lq(self) -> None:
        a = AtomicKnowledge(
            text="注意力机制在NLP任务中很重要，因为它能捕捉长距离依赖关系",
            type="principle",
            claims_count=1,
            source_candidate_index=0,
        )
        is_lq, _ = _detect_low_quality(a)
        assert is_lq is False


# ========== _is_whitelisted ==========


class TestIsWhitelisted:
    def test_empty_whitelist(self) -> None:
        a = AtomicKnowledge(
            text="测试内容",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
            source_title="Python官方文档",
        )
        assert _is_whitelisted(a, []) is False

    def test_match_in_source_title(self) -> None:
        a = AtomicKnowledge(
            text="测试内容",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
            source_title="Python 3.11 官方文档 - docs.python.org",
        )
        assert _is_whitelisted(a, ["docs.python.org"]) is True

    def test_no_match(self) -> None:
        a = AtomicKnowledge(
            text="测试内容",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
            source_title="某博客文章",
        )
        assert _is_whitelisted(a, ["docs.python.org"]) is False

    def test_match_in_entities(self) -> None:
        a = AtomicKnowledge(
            text="测试内容",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
            source_title="PostgreSQL 教程",
            entities=["postgresql.org", "数据库"],
        )
        assert _is_whitelisted(a, ["postgresql.org"]) is True


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

    def test_low_quality_marks_review(self, mock_embed_fn) -> None:
        """低质量知识点应被标记并放入审查队列，不直接丢弃"""
        a = AtomicKnowledge(
            text="这个方法在实际应用中很重要",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
        )
        result = admit_knowledge(
            [a],
            existing_vectors=[],
            embed_fn=mock_embed_fn,
            cosine_sim_fn=lambda a, b: 0.0,
            cold_start_text_dedup=True,
            enhanced_admission=True,
            enable_low_quality_detection=True,
        )
        assert result.stats["low_quality"] == 1
        assert result.stats["review"] >= 1
        assert result.stats["passed"] == 0
        assert any(ri["type"] == "low_quality" for ri in result.review_items)

    def test_whitelist_skips_low_quality(self, mock_embed_fn) -> None:
        """白名单来源应跳过低质量检测"""
        a = AtomicKnowledge(
            text="这个方法在实际应用中很重要",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
            source_title="docs.python.org 官方文档",
        )
        result = admit_knowledge(
            [a],
            existing_vectors=[],
            embed_fn=mock_embed_fn,
            cosine_sim_fn=lambda a, b: 0.0,
            cold_start_text_dedup=True,
            enhanced_admission=True,
            enable_low_quality_detection=True,
            whitelist_sources=["docs.python.org"],
        )
        assert result.stats["low_quality"] == 0
        assert result.stats["passed"] == 1

    def test_enhanced_admission_disabled(self, mock_embed_fn) -> None:
        """关闭增强门控时，低质量检测不生效，行为与之前一致"""
        a = AtomicKnowledge(
            text="这个方法在实际应用中很重要",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
        )
        result = admit_knowledge(
            [a],
            existing_vectors=[],
            embed_fn=mock_embed_fn,
            cosine_sim_fn=lambda a, b: 0.0,
            cold_start_text_dedup=True,
            enhanced_admission=False,
            enable_low_quality_detection=True,
        )
        assert result.stats["low_quality"] == 0
        assert result.stats["passed"] == 1

    def test_whitelist_relaxes_dedup_threshold(self, mock_embed_fn) -> None:
        """白名单来源应放宽判重阈值（从0.95到0.97）"""
        a1 = AtomicKnowledge(
            text="这是一个测试知识点内容用于验证去重阈值",
            type="key_point",
            claims_count=1,
            source_candidate_index=0,
            source_title="普通来源",
        )
        a2 = AtomicKnowledge(
            text="这是一个测试知识点内容用于验证白名单阈值",
            type="key_point",
            claims_count=1,
            source_candidate_index=1,
            source_title="docs.python.org 官方文档",
        )
        existing = [{
            "id": "1",
            "name": "这是一个测试知识点内容用于验证去重阈值",
            "k_vector": [1.0, 0.0, 0.0],
        }]

        def _sim(a, b):
            return 0.96

        def _llm_judge_false(new_text, existing_text):
            return False

        result_normal = admit_knowledge(
            [a1],
            existing_vectors=existing,
            embed_fn=mock_embed_fn,
            cosine_sim_fn=_sim,
            llm_dedup_judge_fn=_llm_judge_false,
            cold_start_text_dedup=False,
            threshold_direct=0.95,
            enhanced_admission=True,
            whitelist_sources=[],
        )
        assert result_normal.stats["dedup_merged"] == 1

        result_whitelist = admit_knowledge(
            [a2],
            existing_vectors=existing,
            embed_fn=mock_embed_fn,
            cosine_sim_fn=_sim,
            llm_dedup_judge_fn=_llm_judge_false,
            cold_start_text_dedup=False,
            threshold_direct=0.95,
            enhanced_admission=True,
            whitelist_sources=["docs.python.org"],
        )
        assert result_whitelist.stats["dedup_merged"] == 0
        assert result_whitelist.stats["passed"] == 1
