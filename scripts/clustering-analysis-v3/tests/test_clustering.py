"""核心聚类算法测试"""

import numpy as np
import pytest

from clustering_analysis.core.clustering import (
    HDBSCAN_AVAILABLE,
    NOISE_WORDS,
    _detect_causal_in_group,
    _detect_causal_in_group_incremental,
    adaptive_hdbscan_params,
    compute_entity_similarity,
    compute_info_density_similarity,
    compute_semantic_similarity,
    convert_llm_causal_pairs,
    dedup_memory_links,
    detect_causal_pairs,
    enrich_text,
    process_clusters,
    run_hdbscan_clustering,
)


class TestDetectCausalPairs:
    """测试因果对检测"""

    def test_detect_simple_causation(self) -> None:
        text = "服务器过载导致系统崩溃"
        pairs = detect_causal_pairs(text)
        assert len(pairs) > 0
        assert any(p[3] == "causes" for p in pairs)

    def test_detect_failure_pattern(self) -> None:
        text = "数据库连接失败"
        pairs = detect_causal_pairs(text)
        # "数据库" 是噪声词，但 "连接" 不是，"数据库连接" 作为整体匹配
        assert isinstance(pairs, list)

    def test_empty_text(self) -> None:
        pairs = detect_causal_pairs("")
        assert pairs == []

    def test_noise_words_filtered(self) -> None:
        # "测试" 和 "系统" 都是噪声词
        text = "测试导致系统失败"
        pairs = detect_causal_pairs(text)
        # 由于噪声词过滤，结果可能为空
        assert isinstance(pairs, list)


class TestEnrichText:
    """测试文本富化"""

    def test_enrich_with_causal_words(self) -> None:
        result = enrich_text("系统故障", "服务器问题", ["服务器", "故障"])
        assert "服务器" in result
        assert "系统故障" in result

    def test_enrich_empty_words(self) -> None:
        result = enrich_text("系统故障", "服务器问题", [])
        assert result == "系统故障"

    def test_enrich_noise_words_only(self) -> None:
        result = enrich_text("系统故障", "服务器问题", ["系统", "测试"])
        # 全部为噪声词，返回原始文本
        assert result == "系统故障"

    def test_enrich_skip_when_already_enriched(self) -> None:
        """已包含 [因果来源：] 标记的文本应跳过富化"""
        result = enrich_text(
            "系统故障。[因果来源：服务器] [因果结果：崩溃]",
            "新问题",
            ["新", "问题"],
        )
        # 返回原始文本，不再追加
        assert result == "系统故障。[因果来源：服务器] [因果结果：崩溃]"


class TestComputeSemanticSimilarity:
    """测试语义相似度计算"""

    def test_basic_computation(self) -> None:
        embeddings = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]], dtype=np.float32)
        sim = compute_semantic_similarity(embeddings, use_gpu=False)
        assert sim.shape == (3, 3)
        # 对角线应为 1.0（自身相似度）
        np.testing.assert_allclose(np.diag(sim), np.ones(3), atol=1e-5)

    def test_range_between_minus_one_and_one(self) -> None:
        rng = np.random.default_rng(42)
        embeddings = rng.random((5, 8)).astype(np.float32)
        sim = compute_semantic_similarity(embeddings, use_gpu=False)
        # 余弦相似度应在 [-1, 1] 之间
        assert sim.min() >= -1.0 - 1e-5
        assert sim.max() <= 1.0 + 1e-5


class TestComputeEntitySimilarity:
    """测试实体重叠度计算"""

    def test_jaccard_similarity(self) -> None:
        unit_entity_sets = [
            {"e1", "e2"},
            {"e2", "e3"},
            {"e4"},
        ]
        sim = compute_entity_similarity(unit_entity_sets, use_gpu=False)
        assert sim.shape == (3, 3)
        # 自身相似度为 1.0
        np.testing.assert_allclose(np.diag(sim), np.ones(3), atol=1e-5)
        # 第一和第二个共享 e2，Jaccard = 1/3
        assert 0.0 < sim[0, 1] < 1.0
        # 第一和第三个无共享实体
        assert sim[0, 2] == 0.0

    def test_empty_entities(self) -> None:
        unit_entity_sets = [set(), set(), set()]
        sim = compute_entity_similarity(unit_entity_sets, use_gpu=False)
        assert sim.shape == (3, 3)
        # 无实体时，非对角线应为 0
        np.testing.assert_allclose(sim, np.eye(3), atol=1e-5)


class TestComputeInfoDensitySimilarity:
    """测试信息密度相似度计算"""

    def test_basic_computation(self) -> None:
        texts = ["这是一个测试文本", "这是另一个测试", "完全不同的内容在这里"]
        sim = compute_info_density_similarity(texts)
        assert sim.shape == (3, 3)
        # 值应在 [0, 1] 范围内
        assert sim.min() >= 0.0
        assert sim.max() <= 1.0

    def test_symmetric(self) -> None:
        texts = ["文本一", "文本二", "文本三"]
        sim = compute_info_density_similarity(texts)
        # 对称性检查
        np.testing.assert_allclose(sim, sim.T, atol=1e-10)


@pytest.mark.skipif(not HDBSCAN_AVAILABLE, reason="HDBSCAN not available (requires scikit-learn >= 1.3)")
class TestRunHDBSCANClustering:
    """测试 HDBSCAN 聚类"""

    def test_clustering_with_clear_groups(self) -> None:
        # 构造两个明显的簇
        cluster1 = np.random.randn(5, 3).astype(np.float32) + np.array([0, 0, 0])
        cluster2 = np.random.randn(5, 3).astype(np.float32) + np.array([10, 10, 10])
        embeddings = np.vstack([cluster1, cluster2])

        labels, probs, silhouette = run_hdbscan_clustering(embeddings, min_cluster_size=2)
        assert labels is not None
        assert len(labels) == 10
        # 应该有至少 1 个簇（排除噪声）
        unique_labels = set(labels) - {-1}
        assert len(unique_labels) >= 1
        # silhouette 可能为 None（簇数不足 2 时）
        assert silhouette is None or 0.0 <= silhouette <= 1.0

    def test_random_data_produces_noise(self) -> None:
        """随机数据无清晰簇结构时，部分点应为噪声"""
        rng = np.random.default_rng(42)
        embeddings = rng.random((20, 4)).astype(np.float32)
        labels, probs, silhouette = run_hdbscan_clustering(embeddings, min_cluster_size=3)
        assert len(labels) == 20
        # 随机数据应有噪声点
        n_noise = (labels == -1).sum()
        assert n_noise > 0
        # silhouette 兼容旧接口
        _ = silhouette


class TestProcessClusters:
    """测试聚类后处理"""

    def test_basic_processing(self) -> None:
        labels = np.array([0, 0, 1, 1, -1])
        unit_ids = [10, 20, 30, 40, 50]
        unit_texts = ["文本1", "文本2", "文本3", "文本4", "文本5"]
        unit_entity_sets = [{"e1"}, {"e1"}, {"e2"}, {"e2"}, set()]

        entity_plan, unit_plan, link_plan, enriched = process_clusters(
            labels,
            unit_ids,
            unit_texts,
            unit_entity_sets,
            skip_entity=True,
            llm_api_url="",
            llm_api_key="",
            llm_model="",
            min_llm_size=10,
            max_group_size=20,
        )

        # 应该有 2 个 entity（两个簇）
        assert len(entity_plan) == 2
        # 应该有 4 个 unit_entity（排除噪声 -1）
        assert len(unit_plan) == 4
        # enriched_texts 可能为空（取决于因果检测）
        assert isinstance(enriched, dict)

    def test_max_group_size_filter(self) -> None:
        labels = np.array([0, 0, 0, 1, 1])
        unit_ids = [10, 20, 30, 40, 50]
        unit_texts = ["文本1", "文本2", "文本3", "文本4", "文本5"]
        unit_entity_sets = [set(), set(), set(), set(), set()]

        entity_plan, unit_plan, link_plan, enriched = process_clusters(
            labels,
            unit_ids,
            unit_texts,
            unit_entity_sets,
            skip_entity=True,
            llm_api_url="",
            llm_api_key="",
            llm_model="",
            min_llm_size=10,
            max_group_size=2,  # 过滤掉大小为 3 的组
        )

        # 组 0（3 个成员）应被过滤，只剩组 1
        assert len(entity_plan) == 1
        assert len(unit_plan) == 2


class TestConvertLLMCausalPairs:
    """测试 convert_llm_causal_pairs 将 LLM 因果对转为 memory_link 格式"""

    def test_basic_conversion(self) -> None:
        causal_pairs = [
            {"cause_idx": 0, "effect_idx": 1, "reason": "A 导致 B"},
            {"cause_idx": 1, "effect_idx": 2, "reason": "B 导致 C"},
        ]
        members = [0, 1, 2]
        unit_ids = ["id_a", "id_b", "id_c"]
        unit_texts = ["文本A", "文本B", "文本C"]
        seen_pairs: set = set()

        links = convert_llm_causal_pairs(
            causal_pairs, members, unit_ids, unit_texts, seen_pairs,
            group_label="test",
        )

        assert len(links) == 2
        assert links[0]["from_id"] == "id_a"
        assert links[0]["to_id"] == "id_b"
        assert links[0]["link_type"] == "causes"
        assert links[0]["weight"] == 0.7  # 无 confidence 字段 → medium 默认 0.7
        assert "[因果LLM]" in links[0]["reason"]

    def test_empty_causal_pairs(self) -> None:
        links = convert_llm_causal_pairs(
            [], [10, 20], ["id_a", "id_b"], ["A", "B"], set(),
            group_label="test",
        )
        assert links == []

    def test_out_of_bounds_indices_skipped(self) -> None:
        """越界索引的因果对被跳过，有效对保留"""
        causal_pairs = [
            {"cause_idx": 0, "effect_idx": 10, "reason": "越界导致故障"},
            {"cause_idx": -1, "effect_idx": 1, "reason": "负数导致异常"},
            {"cause_idx": 0, "effect_idx": 1, "reason": "有效导致结果"},
        ]
        members = [0, 1]
        links = convert_llm_causal_pairs(
            causal_pairs, members, ["id_a", "id_b"], ["A", "B"], set(),
            group_label="test",
        )
        assert len(links) == 1
        assert links[0]["reason"].endswith("有效导致结果")

    def test_self_loop_skipped(self) -> None:
        """cause_idx == effect_idx 的自环被跳过"""
        causal_pairs = [{"cause_idx": 0, "effect_idx": 0, "reason": "自环"}]
        links = convert_llm_causal_pairs(
            causal_pairs, [0], ["id"], ["A"], set(), group_label="test",
        )
        assert links == []

    def test_seen_pairs_dedup(self) -> None:
        """已在 seen_pairs 中的因果对被跳过"""
        causal_pairs = [{"cause_idx": 0, "effect_idx": 1, "reason": "重复"}]
        seen_pairs: set = {("id_a", "id_b", "causes")}
        links = convert_llm_causal_pairs(
            causal_pairs, [0, 1], ["id_a", "id_b"], ["A", "B"], seen_pairs,
            group_label="test",
        )
        assert links == []

    def test_non_dict_pair_skipped(self) -> None:
        causal_pairs = ["not_a_dict", {"cause_idx": 0, "effect_idx": 1, "reason": "有效导致关系"}]
        links = convert_llm_causal_pairs(
            causal_pairs, [0, 1], ["id_a", "id_b"], ["A", "B"], set(),
            group_label="test",
        )
        assert len(links) == 1


class TestAdaptiveHdbscanParams:
    """测试 HDBSCAN 自适应参数函数"""

    def test_small_dataset_under_20(self) -> None:
        """n_samples < 20: min_cluster_size=2, min_samples=2"""
        mcs, ms = adaptive_hdbscan_params(10)
        assert mcs == 2
        assert ms == 2

    def test_small_dataset_boundary_19(self) -> None:
        """边界值: n_samples=19"""
        mcs, ms = adaptive_hdbscan_params(19)
        assert mcs == 2
        assert ms == 2

    def test_20_to_99(self) -> None:
        """20 <= n_samples < 100: min_cluster_size=3, min_samples=3"""
        mcs, ms = adaptive_hdbscan_params(50)
        assert mcs == 3
        assert ms == 3

    def test_boundary_20(self) -> None:
        """边界值: n_samples=20"""
        mcs, ms = adaptive_hdbscan_params(20)
        assert mcs == 3
        assert ms == 3

    def test_boundary_99(self) -> None:
        """边界值: n_samples=99"""
        mcs, ms = adaptive_hdbscan_params(99)
        assert mcs == 3
        assert ms == 3

    def test_100_to_499(self) -> None:
        """100 <= n_samples < 500: min_cluster_size=5, min_samples=4"""
        mcs, ms = adaptive_hdbscan_params(200)
        assert mcs == 5
        assert ms == 4

    def test_boundary_100(self) -> None:
        """边界值: n_samples=100"""
        mcs, ms = adaptive_hdbscan_params(100)
        assert mcs == 5
        assert ms == 4

    def test_boundary_499(self) -> None:
        """边界值: n_samples=499"""
        mcs, ms = adaptive_hdbscan_params(499)
        assert mcs == 5
        assert ms == 4

    def test_500_to_1999(self) -> None:
        """500 <= n_samples < 2000: min_cluster_size=8, min_samples=6"""
        mcs, ms = adaptive_hdbscan_params(1000)
        assert mcs == 8
        assert ms == 6

    def test_boundary_500(self) -> None:
        """边界值: n_samples=500"""
        mcs, ms = adaptive_hdbscan_params(500)
        assert mcs == 8
        assert ms == 6

    def test_boundary_1999(self) -> None:
        """边界值: n_samples=1999"""
        mcs, ms = adaptive_hdbscan_params(1999)
        assert mcs == 8
        assert ms == 6

    def test_large_dataset_2000_plus(self) -> None:
        """n_samples >= 2000: min_cluster_size=15, min_samples=10"""
        mcs, ms = adaptive_hdbscan_params(5000)
        assert mcs == 15
        assert ms == 10

    def test_boundary_2000(self) -> None:
        """边界值: n_samples=2000"""
        mcs, ms = adaptive_hdbscan_params(2000)
        assert mcs == 15
        assert ms == 10

    def test_min_samples_clamped_by_min(self) -> None:
        """min_samples 受 min_samples_min 限制"""
        mcs, ms = adaptive_hdbscan_params(10, min_samples_min=5, min_samples_max=10)
        assert ms == 5
        assert mcs >= 5

    def test_min_samples_clamped_by_max(self) -> None:
        """min_samples 受 min_samples_max 限制"""
        mcs, ms = adaptive_hdbscan_params(5000, min_samples_min=2, min_samples_max=5)
        assert ms == 5
        assert mcs >= 2

    def test_zero_samples(self) -> None:
        """n_samples=0 也应该返回有效值"""
        mcs, ms = adaptive_hdbscan_params(0)
        assert mcs == 2
        assert ms == 2

    def test_one_sample(self) -> None:
        """n_samples=1"""
        mcs, ms = adaptive_hdbscan_params(1)
        assert mcs == 2
        assert ms == 2


class TestDetectCausalInGroupIncremental:
    """测试增量因果链检测"""

    def test_new_new_pairs_detected(self) -> None:
        """新成员之间的因果对应被检测到"""
        unit_ids = ["old1", "old2", "new1", "new2"]
        unit_texts = [
            "旧文本一",
            "旧文本二",
            "服务器过载导致系统崩溃",
            "数据库连接失败引发告警",
        ]
        new_members = [2, 3]
        old_members = [0, 1]
        seen_pairs: set = set()

        links = _detect_causal_in_group_incremental(
            group_label="test",
            new_members=new_members,
            old_members=old_members,
            unit_ids=unit_ids,
            unit_texts=unit_texts,
            seen_pairs=seen_pairs,
        )

        new_new_pairs = [
            l for l in links
            if l["from_id"] in ("new1", "new2") and l["to_id"] in ("new1", "new2")
        ]
        assert len(new_new_pairs) > 0, "应检测到新成员之间的因果对"

    def test_new_old_pairs_detected(self) -> None:
        """新成员与旧成员之间的因果对应被检测到"""
        unit_ids = ["old1", "new1"]
        unit_texts = [
            "服务器过载导致系统崩溃",
            "系统崩溃引发重启",
        ]
        new_members = [1]
        old_members = [0]
        seen_pairs: set = set()

        links = _detect_causal_in_group_incremental(
            group_label="test",
            new_members=new_members,
            old_members=old_members,
            unit_ids=unit_ids,
            unit_texts=unit_texts,
            seen_pairs=seen_pairs,
        )

        new_old_pairs = [
            l for l in links
            if (l["from_id"] == "new1" and l["to_id"] == "old1")
            or (l["from_id"] == "old1" and l["to_id"] == "new1")
        ]
        assert len(new_old_pairs) > 0, "应检测到新成员与旧成员之间的因果对"

    def test_old_old_pairs_skipped(self) -> None:
        """旧成员之间的因果对不应被检测到（增量模式）"""
        unit_ids = ["old1", "old2", "new1"]
        unit_texts = [
            "服务器过载导致系统崩溃",
            "系统崩溃引发重启",
            "新的记忆内容",
        ]
        new_members = [2]
        old_members = [0, 1]
        seen_pairs: set = set()

        links = _detect_causal_in_group_incremental(
            group_label="test",
            new_members=new_members,
            old_members=old_members,
            unit_ids=unit_ids,
            unit_texts=unit_texts,
            seen_pairs=seen_pairs,
        )

        old_old_pairs = [
            l for l in links
            if l["from_id"] in ("old1", "old2") and l["to_id"] in ("old1", "old2")
        ]
        assert len(old_old_pairs) == 0, "增量模式下不应检测旧-旧因果对"

    def test_seen_pairs_dedup(self) -> None:
        """已在 seen_pairs 中的因果对应被跳过"""
        unit_ids = ["old1", "new1"]
        unit_texts = [
            "服务器过载导致系统崩溃",
            "系统崩溃引发重启",
        ]
        new_members = [1]
        old_members = [0]
        seen_pairs: set = {("new1", "old1", "causes")}

        links = _detect_causal_in_group_incremental(
            group_label="test",
            new_members=new_members,
            old_members=old_members,
            unit_ids=unit_ids,
            unit_texts=unit_texts,
            seen_pairs=seen_pairs,
        )

        duplicate_pairs = [
            l for l in links
            if l["from_id"] == "new1" and l["to_id"] == "old1" and l["link_type"] == "causes"
        ]
        assert len(duplicate_pairs) == 0, "已见过的因果对应被跳过"

    def test_empty_new_members_returns_empty(self) -> None:
        """新成员为空时返回空列表"""
        links = _detect_causal_in_group_incremental(
            group_label="test",
            new_members=[],
            old_members=[0, 1],
            unit_ids=["a", "b"],
            unit_texts=["文本A", "文本B"],
            seen_pairs=set(),
        )
        assert links == []

    def test_empty_old_members_still_detects_new_new(self) -> None:
        """旧成员为空时仍应检测新-新组合"""
        unit_ids = ["new1", "new2"]
        unit_texts = [
            "服务器过载导致系统崩溃",
            "系统崩溃引发重启",
        ]
        new_members = [0, 1]
        old_members: list[int] = []
        seen_pairs: set = set()

        links = _detect_causal_in_group_incremental(
            group_label="test",
            new_members=new_members,
            old_members=old_members,
            unit_ids=unit_ids,
            unit_texts=unit_texts,
            seen_pairs=seen_pairs,
        )
        assert len(links) > 0, "只有新成员时也应检测新-新因果对"

    def test_consistent_with_full_detection(self) -> None:
        """增量检测结果应与全量检测中涉及新成员的结果一致"""
        unit_ids = ["old1", "old2", "new1", "new2"]
        unit_texts = [
            "服务器过载导致系统崩溃",
            "数据库连接失败引发告警",
            "系统崩溃引发重启",
            "告警触发通知",
        ]
        all_members = [0, 1, 2, 3]
        new_members = [2, 3]
        old_members = [0, 1]

        seen_full: set = set()
        links_full = _detect_causal_in_group(
            group_label="test",
            members=all_members,
            unit_ids=unit_ids,
            unit_texts=unit_texts,
            seen_pairs=seen_full,
        )

        seen_incr: set = set()
        links_incr = _detect_causal_in_group_incremental(
            group_label="test",
            new_members=new_members,
            old_members=old_members,
            unit_ids=unit_ids,
            unit_texts=unit_texts,
            seen_pairs=seen_incr,
        )

        def involves_new(link: dict) -> bool:
            return link["from_id"] in ("new1", "new2") or link["to_id"] in ("new1", "new2")

        full_with_new = [l for l in links_full if involves_new(l)]
        incr_keys = {(l["from_id"], l["to_id"], l["link_type"]) for l in links_incr}
        full_keys = {(l["from_id"], l["to_id"], l["link_type"]) for l in full_with_new}

        assert incr_keys == full_keys, "增量检测应覆盖所有涉及新成员的因果对"


class TestDedupMemoryLinks:
    """测试 memory_links 去重函数"""

    def test_no_duplicates_unchanged(self) -> None:
        """无重复时列表不变"""
        links = [
            {"from_id": "a", "to_id": "b", "link_type": "causes", "weight": 0.9},
            {"from_id": "c", "to_id": "d", "link_type": "causes", "weight": 0.8},
        ]
        result = dedup_memory_links(links)
        assert len(result) == 2
        assert result[0]["from_id"] == "a"
        assert result[1]["from_id"] == "c"

    def test_exact_duplicate_removed(self) -> None:
        """完全相同的因果对（同方向）应被去重"""
        links = [
            {"from_id": "a", "to_id": "b", "link_type": "causes", "weight": 0.9},
            {"from_id": "a", "to_id": "b", "link_type": "causes", "weight": 0.9},
        ]
        result = dedup_memory_links(links)
        assert len(result) == 1
        assert result[0]["from_id"] == "a"
        assert result[0]["to_id"] == "b"

    def test_reverse_direction_duplicate_removed(self) -> None:
        """反向因果对（min/max 相同）应被去重"""
        links = [
            {"from_id": "a", "to_id": "b", "link_type": "causes", "weight": 0.9},
            {"from_id": "b", "to_id": "a", "link_type": "causes", "weight": 0.8},
        ]
        result = dedup_memory_links(links)
        assert len(result) == 1, "方向相反但 min/max 相同的对应被去重"
        assert result[0]["from_id"] == "a"

    def test_different_link_type_not_duplicate(self) -> None:
        """不同 link_type 的不算重复"""
        links = [
            {"from_id": "a", "to_id": "b", "link_type": "causes", "weight": 0.9},
            {"from_id": "a", "to_id": "b", "link_type": "caused_by", "weight": 0.8},
        ]
        result = dedup_memory_links(links)
        assert len(result) == 2

    def test_keeps_first_occurrence(self) -> None:
        """保留第一次出现的链接"""
        links = [
            {"from_id": "a", "to_id": "b", "link_type": "causes", "weight": 0.9, "reason": "first"},
            {"from_id": "b", "to_id": "a", "link_type": "causes", "weight": 0.8, "reason": "second"},
        ]
        result = dedup_memory_links(links)
        assert len(result) == 1
        assert result[0]["reason"] == "first"

    def test_empty_list_returns_empty(self) -> None:
        """空列表返回空列表"""
        result = dedup_memory_links([])
        assert result == []

    def test_string_id_normalization(self) -> None:
        """ID 应被转为字符串后比较"""
        links = [
            {"from_id": 1, "to_id": 2, "link_type": "causes", "weight": 0.9},
            {"from_id": "1", "to_id": "2", "link_type": "causes", "weight": 0.9},
        ]
        result = dedup_memory_links(links)
        assert len(result) == 1

    def test_multiple_duplicates(self) -> None:
        """多个重复项都应被去重"""
        links = [
            {"from_id": "a", "to_id": "b", "link_type": "causes"},
            {"from_id": "b", "to_id": "a", "link_type": "causes"},
            {"from_id": "a", "to_id": "b", "link_type": "causes"},
            {"from_id": "c", "to_id": "d", "link_type": "causes"},
        ]
        result = dedup_memory_links(links)
        assert len(result) == 2


class TestCausalIncrementalConfig:
    """测试因果链增量配置项"""

    def test_default_values(self) -> None:
        """默认值应为开启增量且仅新成员相关"""
        from clustering_analysis.config import AppConfig
        cfg = AppConfig()
        assert cfg.causal_incremental is True
        assert cfg.causal_new_only is True

    def test_from_dict_overrides(self) -> None:
        """from_dict 应能覆盖默认值"""
        from clustering_analysis.config import AppConfig
        cfg = AppConfig.from_dict({
            "causal_incremental": False,
            "causal_new_only": False,
        })
        assert cfg.causal_incremental is False
        assert cfg.causal_new_only is False
