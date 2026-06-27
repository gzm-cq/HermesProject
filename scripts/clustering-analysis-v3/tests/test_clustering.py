"""核心聚类算法测试"""

import numpy as np
import pytest

from clustering_analysis.core.clustering import (
    HDBSCAN_AVAILABLE,
    NOISE_WORDS,
    compute_entity_similarity,
    compute_info_density_similarity,
    compute_semantic_similarity,
    convert_llm_causal_pairs,
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
