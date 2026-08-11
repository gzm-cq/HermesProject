"""过滤逻辑测试。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from knowledge_navigation.config import CONFIG
from knowledge_navigation.core.filtering import (
    _char_ngram_jaccard,
    _cosine_similarity_vec,
    calculate_score_stats,
    calculate_time_score,
    cross_domain_dedup,
    estimate_tokens,
    exclude_marked,
    extract_rerank_scores,
    filter_by_score,
    format_context_lines,
)


class TestExtractRerankScores:
    """测试 extract_rerank_scores 函数。"""

    def test_extract_basic(self) -> None:
        """测试基本提取功能。"""
        trace_data = {
            "reranked": [
                {"node_id": "a", "rerank_score": 0.9},
                {"node_id": "b", "rerank_score": 0.7},
            ]
        }
        result = extract_rerank_scores(trace_data)
        assert result == {"a": 0.9, "b": 0.7}

    def test_extract_empty(self) -> None:
        """测试空 trace 数据。"""
        assert extract_rerank_scores({}) == {}
        assert extract_rerank_scores({"reranked": []}) == {}

    def test_extract_missing_node_id(self) -> None:
        """测试缺少 node_id 的条目被忽略。"""
        trace_data = {
            "reranked": [
                {"node_id": "a", "rerank_score": 0.9},
                {"rerank_score": 0.7},
            ]
        }
        result = extract_rerank_scores(trace_data)
        assert result == {"a": 0.9}

    def test_extract_non_numeric_score(self) -> None:
        """测试分数被转换为 float。"""
        trace_data = {
            "reranked": [
                {"node_id": "a", "rerank_score": "0.85"},
            ]
        }
        result = extract_rerank_scores(trace_data)
        assert result == {"a": 0.85}


class TestFilterByScore:
    """测试 filter_by_score 函数。"""

    def test_filter_basic(
        self,
        sample_raw_results: list[dict],
        sample_rerank_map: dict[str, float],
    ) -> None:
        """测试基本分数过滤。"""
        kept, all_scores, comparison = filter_by_score(
            sample_raw_results, sample_rerank_map, min_score=0.6, max_results=10
        )
        assert len(kept) == 3
        assert [r["id"] for r in kept] == ["node1", "node5", "node2"]
        assert len(all_scores) == 5
        # 双分对比数据应与 kept 一一对应
        assert len(comparison) == len(kept)
        for item in comparison:
            assert "node_id" in item
            assert "base_score" in item
            assert "temporal_score" in item

    def test_filter_with_max_results(
        self,
        sample_raw_results: list[dict],
        sample_rerank_map: dict[str, float],
    ) -> None:
        """测试 max_results 限制。"""
        kept, _, comparison = filter_by_score(
            sample_raw_results, sample_rerank_map, min_score=0.0, max_results=2
        )
        assert len(kept) == 2
        assert len(comparison) == 2

    def test_filter_high_threshold(
        self,
        sample_raw_results: list[dict],
        sample_rerank_map: dict[str, float],
    ) -> None:
        """测试高阈值过滤掉所有结果。"""
        kept, all_scores, comparison = filter_by_score(
            sample_raw_results, sample_rerank_map, min_score=1.0, max_results=10
        )
        assert len(kept) == 0
        assert len(all_scores) == 5
        assert comparison == []  # 无保留结果时比较数据应为空

    def test_filter_missing_id_gets_zero(
        self,
        sample_raw_results: list[dict],
        sample_rerank_map: dict[str, float],
    ) -> None:
        """测试缺少 ID 的结果默认分数为 0。"""
        # node4 has no score in sample_rerank_map
        kept, all_scores, _ = filter_by_score(
            sample_raw_results, sample_rerank_map, min_score=0.0, max_results=10
        )
        assert all_scores[3] == 0.0

    def test_filter_empty_input(self) -> None:
        """测试空输入。"""
        kept, all_scores, comparison = filter_by_score([], {}, min_score=0.5, max_results=3)
        assert kept == []
        assert all_scores == []
        assert comparison == []


class TestFormatContextLines:
    """测试 format_context_lines 函数。"""

    def test_format_basic(self) -> None:
        """测试基本格式化（XML 包裹格式）。"""
        results = [
            {"id": "a", "text": "  Hello world  "},
            {"id": "b", "text": "Another text"},
        ]
        lines = format_context_lines(results, max_text_length=100)
        assert lines == [
            '<memory-context source="vector">',
            '  <memory source="hindsight" node_id="a">Hello world</memory>',
            '  <memory source="hindsight" node_id="b">Another text</memory>',
            "</memory-context>",
        ]

    def test_format_truncate(self) -> None:
        """测试文本截断（XML 包裹格式）。"""
        results = [{"id": "a", "text": "A" * 50}]
        lines = format_context_lines(results, max_text_length=10)
        assert lines == [
            '<memory-context source="vector">',
            '  <memory source="hindsight" node_id="a">' + "A" * 10 + "</memory>",
            "</memory-context>",
        ]

    def test_format_skip_empty(self) -> None:
        """测试跳过空文本（XML 包裹格式）。"""
        results = [
            {"id": "a", "text": "Valid"},
            {"id": "b", "text": ""},
            {"id": "c", "text": "   "},
        ]
        lines = format_context_lines(results, max_text_length=100)
        assert lines == [
            '<memory-context source="vector">',
            '  <memory source="hindsight" node_id="a">Valid</memory>',
            "</memory-context>",
        ]

    def test_format_escapes_xml_text_and_attributes(self) -> None:
        """测试 XML 文本和属性转义，防止召回内容破坏注入结构。"""
        results = [
            {"id": 'node"1', "source": 'hindsight&kt', "text": "A <tag> & B </memory>"},
        ]
        lines = format_context_lines(results, max_text_length=100)
        assert lines == [
            '<memory-context source="vector">',
            '  <memory source="hindsight&amp;kt" node_id="node&quot;1">A &lt;tag&gt; &amp; B &lt;/memory&gt;</memory>',
            "</memory-context>",
        ]

    def test_format_multi_hop_separate_block(self) -> None:
        """多跳结果单独成块，source 为 multi-hop。"""
        results = [
            {"id": "v1", "text": "vector result"},
            {"id": "m1", "text": "multi-hop result", "source": "multi-hop"},
        ]
        lines = format_context_lines(results, max_text_length=100)
        assert lines == [
            '<memory-context source="vector">',
            '  <memory source="hindsight" node_id="v1">vector result</memory>',
            "</memory-context>",
            '<memory-context source="multi-hop">',
            '  <memory source="multi-hop" node_id="m1">multi-hop result</memory>',
            "</memory-context>",
        ]

    def test_format_empty(self) -> None:
        """测试空结果列表。"""
        assert format_context_lines([]) == []


class TestCalculateScoreStats:
    """测试 calculate_score_stats 函数。"""

    def test_stats_basic(self) -> None:
        """测试基本统计计算。"""
        stats = calculate_score_stats([0.5, 0.8, 0.9])
        assert stats["min"] == 0.5
        assert stats["max"] == 0.9
        assert stats["avg"] == pytest.approx(0.733, abs=0.01)
        assert stats["count"] == 3

    def test_stats_empty(self) -> None:
        """测试空列表返回零值。"""
        stats = calculate_score_stats([])
        assert stats == {"min": 0.0, "max": 0.0, "avg": 0.0, "count": 0}

    def test_stats_single(self) -> None:
        """测试单元素列表。"""
        stats = calculate_score_stats([0.75])
        assert stats["min"] == 0.75
        assert stats["max"] == 0.75
        assert stats["avg"] == 0.75
        assert stats["count"] == 1


class TestExcludeMarked:
    """测试 exclude_marked 函数。"""

    def test_exclude_basic(self) -> None:
        """测试基本排除功能。"""
        results = [
            {"id": "a", "text": "Normal memory"},
            {"id": "b", "text": "Bad memory [标记: 错误]"},
            {"id": "c", "text": "Marked [标记: 作废] as invalid"},
        ]
        kept, excluded = exclude_marked(results)
        assert len(kept) == 1
        assert kept[0]["id"] == "a"
        assert excluded == 2

    def test_exclude_all_marked(self) -> None:
        """测试全部被标记。"""
        results = [
            {"id": "a", "text": "[标记: 可疑] memory"},
        ]
        kept, excluded = exclude_marked(results)
        assert kept == []
        assert excluded == 1

    def test_exclude_empty(self) -> None:
        """测试空列表。"""
        kept, excluded = exclude_marked([])
        assert kept == []
        assert excluded == 0

    def test_exclude_no_mark(self) -> None:
        """测试没有标记。"""
        results = [
            {"id": "a", "text": "Normal 1"},
            {"id": "b", "text": "Normal 2"},
        ]
        kept, excluded = exclude_marked(results)
        assert len(kept) == 2
        assert excluded == 0

    def test_exclude_without_text_field(self) -> None:
        """测试缺少 text 字段的条目。"""
        results = [
            {"id": "a"},
            {"id": "b", "text": "[标记: 错误] something"},
        ]
        kept, excluded = exclude_marked(results)
        assert len(kept) == 1
        assert kept[0]["id"] == "a"
        assert excluded == 1

    def test_exclude_and_sort(self, sample_raw_results: list[dict], sample_rerank_map: dict[str, float]) -> None:
        """测试排除标记后排序仍正确。"""
        # 显式固定 lambda_mrr=0.5，避免环境变量 KN_LAMBDA_MRR 覆盖导致 MMR 排序断言失效
        with patch.object(CONFIG, "lambda_mrr", 0.5):
            # 给 node1 加标记，验证它被排除
            sample_raw_results[0]["text"] = "[标记: 错误] " + sample_raw_results[0]["text"]
            filtered, excluded = exclude_marked(sample_raw_results)
            assert excluded == 1
            assert len(filtered) == 4
            # MMR 多样性重排后：node5(0.88) > node2(0.75) — 前两个按分数
            # node4(0.0) 与 node3(0.55) 在 MMR 下 node4 因多样性得分更高排第三
            kept, _, _ = filter_by_score(filtered, sample_rerank_map, min_score=0.0, max_results=10)
            assert [r["id"] for r in kept] == ["node5", "node2", "node4", "node3"]


class TestCalculateTimeScore:
    """测试 calculate_time_score 函数。"""

    def test_recent_memory_high_score(self) -> None:
        """测试最新记忆分数接近 1.0。"""
        recent = datetime.now(timezone.utc).isoformat()
        score = calculate_time_score(recent)
        assert score > 0.95

    def test_old_memory_low_score(self) -> None:
        """测试 90 天前记忆分数接近 0。"""
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        score = calculate_time_score(old)
        assert score < 0.1

    def test_medium_memory(self) -> None:
            """测试 30 天前记忆分数约 exp(-30/30) ≈ 0.37（halflife 默认 30 天）。"""
            from knowledge_navigation.config import CONFIG
            from unittest.mock import patch
            medium = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            with patch.object(CONFIG, "temporal_halflife_days", 30):
                score = calculate_time_score(medium)
            assert 0.35 < score < 0.38

    def test_none_returns_mid(self) -> None:
        """测试 None 返回中性值。"""
        assert calculate_time_score(None) == 0.5

    def test_empty_string_returns_mid(self) -> None:
        """测试空字符串返回中性值。"""
        assert calculate_time_score("") == 0.5

    def test_future_date_returns_high(self) -> None:
        """测试未来日期返回高分。"""
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        score = calculate_time_score(future)
        assert score > 0.95

    def test_invalid_string_returns_mid(self) -> None:
        """测试无效日期返回中性值。"""
        assert calculate_time_score("not-a-date") == 0.5

    def test_temporal_fusion_order(
        self,
        sample_raw_results_with_time: list[dict],
    ) -> None:
        """测试时态融合改变排序。"""
        rerank_map = {
            "node1": 0.9,   # recent → 高 time_score, fused ≈ 0.95
            "node2": 0.9,   # 60天前 → 低 time_score, fused ≈ 0.48
            "node3": 0.9,   # 15天前 → 中 time_score, fused ≈ 0.79
        }
        mentioned_at_map = {
            r["id"]: r["mentioned_at"] for r in sample_raw_results_with_time if r.get("mentioned_at")
        }
        # 无时态：分数相同，保持原始顺序
        # 有时态：node1 > node3 > node2
        kept_no_temporal, _, comparison_no = filter_by_score(
            sample_raw_results_with_time, rerank_map,
            min_score=0.0, max_results=10, enable_temporal=False,
            mentioned_at_map=mentioned_at_map,
        )
        kept_temporal, _, comparison_yes = filter_by_score(
            sample_raw_results_with_time, rerank_map,
            min_score=0.0, max_results=10, enable_temporal=True,
            mentioned_at_map=mentioned_at_map,
        )
        assert [r["id"] for r in kept_no_temporal] == ["node1", "node3", "node2"]
        assert [r["id"] for r in kept_temporal] == ["node1", "node3", "node2"]
        assert kept_temporal[0]["id"] == "node1"
        # 双分对比数据在两种模式下都应存在
        assert len(comparison_no) == 3
        assert len(comparison_yes) == 3
        # temporal_score 在两种模式下应一致（按 node_id 索引，不受排序影响）
        node_map_no = {item["node_id"]: item["temporal_score"] for item in comparison_no}
        node_map_yes = {item["node_id"]: item["temporal_score"] for item in comparison_yes}
        assert node_map_no == node_map_yes


class TestCharNgramJaccard:
    """测试字符 n-gram Jaccard 相似度。"""

    def test_identical_texts(self) -> None:
        """完全相同的文本相似度为 1.0。"""
        assert _char_ngram_jaccard("知识管理系统", "知识管理系统") == 1.0

    def test_completely_different(self) -> None:
        """完全不同的文本相似度为 0.0。"""
        assert _char_ngram_jaccard("abc", "xyz") == 0.0

    def test_similar_texts(self) -> None:
        """近似文本相似度在 0~1 之间。"""
        sim = _char_ngram_jaccard(
            "DBSCAN 算法用于向量聚类",
            "DBSCAN 聚类算法应用于向量数据",
        )
        assert 0.3 < sim < 0.9

    def test_empty_text(self) -> None:
        """空文本返回 0.0。"""
        assert _char_ngram_jaccard("", "something") == 0.0
        assert _char_ngram_jaccard("something", "") == 0.0

    def test_short_texts(self) -> None:
        """短文本回退到更小的 n。"""
        sim = _char_ngram_jaccard("ab", "ab")
        assert sim == 1.0


class TestCosineSimilarityVec:
    """测试向量余弦相似度。"""

    def test_identical_vectors(self) -> None:
        """相同向量相似度为 1.0。"""
        assert _cosine_similarity_vec([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        """正交向量相似度为 0.0。"""
        assert _cosine_similarity_vec([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector(self) -> None:
        """零向量返回 0.0。"""
        assert _cosine_similarity_vec([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestCrossDomainDedup:
    """测试跨域语义去重。"""

    def test_no_overlap_keeps_all(self) -> None:
        """无重复时保留所有知识树结果。"""
        hs = [{"id": "h1", "text": "DBSCAN 聚类算法"}]
        kt = [
            {"id": 1, "name": "A", "text": "强化学习在机器人中的应用", "score": 0.8},
            {"id": 2, "name": "B", "text": "微服务架构设计原则", "score": 0.7},
        ]
        deduped, removed = cross_domain_dedup(hs, kt, action="remove")
        assert len(deduped) == 2
        assert removed == 0

    def test_exact_duplicate_removed(self) -> None:
        """完全重复的知识被去除。"""
        hs = [{"id": "h1", "text": "DBSCAN 是一种基于密度的聚类算法"}]
        kt = [
            {"id": 1, "name": "A", "text": "DBSCAN 是一种基于密度的聚类算法", "score": 0.8},
            {"id": 2, "name": "B", "text": "微服务架构设计原则", "score": 0.7},
        ]
        deduped, removed = cross_domain_dedup(hs, kt, threshold=0.85, action="remove")
        assert removed == 1
        assert len(deduped) == 1
        assert deduped[0]["id"] == 2

    def test_empty_hindsight_returns_all(self) -> None:
        """Hindsight 为空时返回所有知识树结果。"""
        kt = [{"id": 1, "name": "A", "text": "测试文本", "score": 0.8}]
        deduped, removed = cross_domain_dedup([], kt, action="remove")
        assert deduped == kt
        assert removed == 0

    def test_empty_kt_returns_empty(self) -> None:
        """知识树为空时返回空列表。"""
        hs = [{"id": "h1", "text": "测试文本"}]
        deduped, removed = cross_domain_dedup(hs, [], action="remove")
        assert deduped == []
        assert removed == 0

    def test_with_embed_fn_uses_cosine(self) -> None:
        """提供 embed_fn 时使用余弦相似度。"""
        hs = [{"id": "h1", "text": "机器学习基础"}]
        kt = [
            {"id": 1, "name": "A", "text": "深度学习神经网络", "score": 0.8},
        ]
        # embed_fn 返回相同向量 → 相似度 1.0 → 被去重
        def fake_embed(texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0]] * len(texts)

        deduped, removed = cross_domain_dedup(hs, kt, embed_fn=fake_embed, action="remove")
        assert removed == 1
        assert len(deduped) == 0

    def test_with_embed_fn_orthogonal_keeps(self) -> None:
        """embed_fn 返回正交向量时保留。"""
        hs = [{"id": "h1", "text": "文本A"}]
        kt = [{"id": 1, "name": "A", "text": "文本B", "score": 0.8}]

        call_count = 0
        def fake_embed(texts: list[str]) -> list[list[float]]:
            nonlocal call_count
            call_count += 1
            # hs 和 kt 向量正交
            return [[1.0, 0.0], [0.0, 1.0]]

        deduped, removed = cross_domain_dedup(hs, kt, embed_fn=fake_embed, action="remove")
        assert removed == 0
        assert len(deduped) == 1

    def test_embed_fn_failure_falls_back(self) -> None:
        """embed_fn 异常时回退到 Jaccard。"""
        hs = [{"id": "h1", "text": "完全不同的话题"}]
        kt = [{"id": 1, "name": "A", "text": "毫无关联的内容", "score": 0.8}]

        def bad_embed(texts: list[str]) -> list[list[float]]:
            raise RuntimeError("API 失败")

        deduped, removed = cross_domain_dedup(hs, kt, embed_fn=bad_embed, action="remove")
        assert removed == 0
        assert len(deduped) == 1

    def test_demote_mode_keeps_duplicates(self) -> None:
        """demote 模式：重复项保留但分数降低。"""
        hs = [{"id": "h1", "text": "DBSCAN 是一种基于密度的聚类算法"}]
        kt = [
            {"id": 1, "name": "A", "text": "DBSCAN 是一种基于密度的聚类算法", "score": 0.8},
            {"id": 2, "name": "B", "text": "微服务架构设计原则", "score": 0.7},
        ]
        deduped, demoted = cross_domain_dedup(hs, kt, threshold=0.85, action="demote", demote_factor=0.5)
        assert demoted == 1
        assert len(deduped) == 2
        dup_item = next(r for r in deduped if r["id"] == 1)
        assert dup_item["final_score"] == 0.4
        assert dup_item["score"] == 0.4

    def test_demote_mode_sorts_after_demote(self) -> None:
        """demote 模式：降权后按 final_score 降序重新排序。"""
        hs = [{"id": "h1", "text": "重复的知识内容"}]
        kt = [
            {"id": 1, "name": "A", "text": "重复的知识内容", "score": 0.9},
            {"id": 2, "name": "B", "text": "不重复的内容", "score": 0.7},
            {"id": 3, "name": "C", "text": "其他内容", "score": 0.5},
        ]
        deduped, demoted = cross_domain_dedup(hs, kt, threshold=0.85, action="demote", demote_factor=0.5)
        assert demoted == 1
        assert len(deduped) == 3
        assert [r["id"] for r in deduped] == [2, 3, 1]
        assert deduped[0]["id"] == 2
        assert deduped[1]["id"] == 3
        assert deduped[2]["id"] == 1
        assert deduped[2]["final_score"] == 0.45

    def test_demote_mode_with_embed_fn(self) -> None:
        """demote 模式 + embed_fn：重复项降权但保留。"""
        hs = [{"id": "h1", "text": "机器学习基础"}]
        kt = [
            {"id": 1, "name": "A", "text": "深度学习神经网络", "score": 0.8},
            {"id": 2, "name": "B", "text": "不相关内容", "score": 0.6},
        ]
        def fake_embed(texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0]] * len(texts)

        deduped, demoted = cross_domain_dedup(hs, kt, embed_fn=fake_embed, action="demote", demote_factor=0.3)
        assert demoted == 2
        assert len(deduped) == 2
        assert deduped[0]["final_score"] == pytest.approx(0.8 * 0.3)
        assert deduped[1]["final_score"] == pytest.approx(0.6 * 0.3)
        for r in deduped:
            assert "final_score" in r
            assert r["final_score"] == r["score"]

    def test_remove_mode_backward_compatible(self) -> None:
        """remove 模式：行为与旧版本完全一致。"""
        hs = [{"id": "h1", "text": "DBSCAN 是一种基于密度的聚类算法"}]
        kt = [
            {"id": 1, "name": "A", "text": "DBSCAN 是一种基于密度的聚类算法", "score": 0.8},
            {"id": 2, "name": "B", "text": "微服务架构设计原则", "score": 0.7},
        ]
        deduped, removed = cross_domain_dedup(hs, kt, threshold=0.85, action="remove")
        assert removed == 1
        assert len(deduped) == 1
        assert deduped[0]["id"] == 2

    def test_demote_mode_default_action(self) -> None:
        """默认 action 为 demote。"""
        hs = [{"id": "h1", "text": "重复文本"}]
        kt = [
            {"id": 1, "name": "A", "text": "重复文本", "score": 0.8},
        ]
        deduped, count = cross_domain_dedup(hs, kt, threshold=0.85)
        assert count == 1
        assert len(deduped) == 1
        assert deduped[0]["final_score"] == 0.4


class TestEstimateTokens:
    """测试 token 估算函数。"""

    def test_empty_text(self) -> None:
        """空文本返回 0。"""
        assert estimate_tokens("") == 0

    def test_chinese_only(self) -> None:
        """纯中文文本估算。"""
        tokens = estimate_tokens("知识管理系统")
        assert tokens > 0
        assert tokens == int(6 * 1.5)

    def test_english_only(self) -> None:
        """纯英文文本估算。"""
        tokens = estimate_tokens("hello world")
        assert tokens > 0
        assert tokens == int(2 * 1.3)

    def test_mixed_text(self) -> None:
        """中英文混合文本估算。"""
        tokens = estimate_tokens("知识 management 系统")
        cjk = 4
        english = 1
        expected = int(cjk * 1.5 + english * 1.3)
        assert tokens == expected

    def test_special_chars_ignored(self) -> None:
        """特殊字符和数字被忽略。"""
        tokens1 = estimate_tokens("测试")
        tokens2 = estimate_tokens("测!@#$%^&*()试")
        assert tokens1 == tokens2

    def test_english_with_numbers(self) -> None:
        """英文单词包含数字、下划线、连字符算一个词。"""
        tokens = estimate_tokens("test_var hello-world")
        assert tokens == int(2 * 1.3)


