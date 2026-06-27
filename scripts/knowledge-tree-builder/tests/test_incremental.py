"""测试 incremental 模块 — 增量去重 + 矛盾检测 + Q 投影"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from knowledge_tree_builder.core.incremental import (
    compute_subject_offset,
    dedup_before_insert,
    detect_conflict,
    local_q,
)


# ========== Fixtures ==========


@pytest.fixture
def mock_embed_fn() -> Any:
    """返回一个模拟的 embed_fn。

    映射规则:
    - 含「重复+匹配」→ [1.0, 0.0, 0.0]（与 leaf_nodes[0] k_vector 一致）
    - 含「重复+不匹配」→ [0.9, 0.1, 0.0]（与 leaf_nodes[0] 相似度高但不等）
    - 含「优于」→ [1.0, 0.0, 0.0]（与 sibling 201 一致）
    - 含「优于」+「无效」→ [0.95, 0.0, 0.05]（与 sibling 203 一致）
    - 含「无效」→ [0.95, 0.0, 0.05]
    - 含「全新」→ [0.0, 0.0, 1.0]（独立向量）
    - 默认 → [0.5, 0.5, 0.0]
    """
    def _embed(texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for t in texts:
            if "重复" in t and "匹配" in t:
                results.append([1.0, 0.0, 0.0])
            elif "重复" in t and "不匹配" in t:
                results.append([0.9, 0.1, 0.0])
            elif "无效" in t and "优于" in t:
                results.append([0.95, 0.0, 0.05])
            elif "优于" in t:
                results.append([1.0, 0.0, 0.0])
            elif "无效" in t:
                results.append([0.95, 0.0, 0.05])
            elif "全新" in t:
                results.append([0.0, 0.0, 1.0])
            else:
                results.append([0.5, 0.5, 0.0])
        return results
    return _embed


@pytest.fixture
def mock_cosine_sim() -> Any:
    """模拟余弦相似度。"""
    def _sim(a: list[float], b: list[float]) -> float:
        a_np = np.array(a, dtype=np.float32)
        b_np = np.array(b, dtype=np.float32)
        norm_a = np.linalg.norm(a_np)
        norm_b = np.linalg.norm(b_np)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a_np, b_np) / (norm_a * norm_b))
    return _sim


@pytest.fixture
def leaf_nodes() -> list[dict[str, Any]]:
    """已有叶子节点列表。k_vector 与 mock_embed_fn 的向量对齐。"""
    return [
        {"id": "101", "name": "匹配的 HDBSCAN 知识点", "k_vector": [1.0, 0.0, 0.0]},
        {"id": "102", "name": "不匹配的 DBSCAN 知识点", "k_vector": [0.0, 1.0, 0.0]},
        {"id": "103", "name": "模型中心知识点", "k_vector": [1.0, 0.0, 0.0]},  # 与「全新」向量 [0,0,1] 正交 → cos=0
    ]


@pytest.fixture
def sibling_points() -> list[dict[str, Any]]:
    """兄弟节点列表。k_vector 与 mock_embed_fn 对齐。"""
    return [
        {"id": 201, "name": "HDBSCAN 在非均匀密度数据上优于 DBSCAN", "k_vector": [1.0, 0.0, 0.0]},
        {"id": 202, "name": "聚类算法在均匀分布数据上效果更好", "k_vector": [0.0, 1.0, 0.0]},
        {"id": 203, "name": "DBSCAN 在非均匀密度数据上无效", "k_vector": [0.95, 0.0, 0.05]},
    ]


# ========== dedup_before_insert ==========


class TestDedupBeforeInsert:
    """增量去重检查测试"""

    def test_duplicate_found(self, mock_embed_fn, mock_cosine_sim, leaf_nodes) -> None:
        """重复文本应返回匹配的节点 ID。"""
        result = dedup_before_insert(
            "重复匹配的知识点文本",  # → [1.0,0.0,0.0], leaf_nodes[0].k_vector=[1.0,0.0,0.0]
            leaf_nodes,
            mock_embed_fn,
            mock_cosine_sim,
            threshold=0.5,
        )
        assert result == "101"

    def test_no_duplicate(self, mock_embed_fn, mock_cosine_sim, leaf_nodes) -> None:
        """不同文本应返回 None。"""
        result = dedup_before_insert(
            "全新知识点：强化学习通过奖励机制驱动策略优化",  # → [0,0,1.0], 与各节点相似度均 < 0.5
            leaf_nodes,
            mock_embed_fn,
            mock_cosine_sim,
            threshold=0.5,
        )
        assert result is None

    def test_threshold_boundary_above(self, mock_embed_fn, mock_cosine_sim, leaf_nodes) -> None:
        """刚好高于阈值应判重。"""
        # "重复匹配" → [1,0,0], leaf_nodes[0]=[1,0,0], cosine=1
        # threshold=0.99 < 1 → 判重
        result = dedup_before_insert(
            "重复匹配的知识点文本",
            leaf_nodes,
            mock_embed_fn,
            mock_cosine_sim,
            threshold=0.99,
        )
        assert result == "101"

    def test_embedding_failure_returns_none(self, leaf_nodes) -> None:
        """embedding 失败应返回 None。"""
        def _fail(texts: list[str]) -> None:
            return None
        result = dedup_before_insert("任何文本", leaf_nodes, _fail)
        assert result is None

    def test_empty_leaf_nodes(self, mock_embed_fn, mock_cosine_sim) -> None:
        """空叶子节点列表应返回 None。"""
        result = dedup_before_insert("任何文本", [], mock_embed_fn, mock_cosine_sim)
        assert result is None

    def test_no_k_vector_in_nodes(self, mock_embed_fn, mock_cosine_sim) -> None:
        """叶子节点无 k_vector 时应跳过该节点。"""
        nodes = [{"id": "301", "name": "无向量的知识"}]  # 无 k_vector
        result = dedup_before_insert("任何文本", nodes, mock_embed_fn, mock_cosine_sim)
        assert result is None

    def test_first_match_wins(self, mock_embed_fn, mock_cosine_sim) -> None:
        """多个匹配时应返回第一个匹配的 ID。"""
        nodes = [
            {"id": "first", "name": "重复的知识点", "k_vector": [1.0, 0.0, 0.0]},
            {"id": "second", "name": "重复的知识点", "k_vector": [1.0, 0.0, 0.0]},
        ]
        result = dedup_before_insert(
            "重复的知识点文本",
            nodes,
            mock_embed_fn,
            mock_cosine_sim,
            threshold=0.5,
        )
        assert result == "first"

    def test_cosine_sim_fn_default_used(self, mock_embed_fn, leaf_nodes) -> None:
        """未传入 cosine_sim_fn 时应使用默认的 cosine_similarity。"""
        result = dedup_before_insert(
            "重复匹配的知识点文本",  # → [1,0,0], leaf_nodes[0]=[1,0,0], cosine=1 > 0.5
            leaf_nodes,
            mock_embed_fn,
            threshold=0.5,
        )
        assert result == "101"

    def test_low_threshold_catches_all(self, mock_embed_fn, mock_cosine_sim, leaf_nodes) -> None:
        """极低阈值应该匹配第一个有 k_vector 的节点。"""
        result = dedup_before_insert(
            "完全不同的知识点",
            leaf_nodes,
            mock_embed_fn,
            mock_cosine_sim,
            threshold=0.01,
        )
        assert result is not None


# ========== detect_conflict ==========


class TestDetectConflict:
    """矛盾检测测试"""

    def test_conflict_detected(self, mock_embed_fn, mock_cosine_sim, sibling_points) -> None:
        """条件相同+结论对立应检测为矛盾。"""
        # 新知识含「无效」→ [0.95,0,0.05], sibling[2] 相同向量 + 含「优于」→ 结论对立
        conflicts = detect_conflict(
            "DBSCAN 在非均匀密度数据上无效",
            sibling_points,
            mock_embed_fn,
            mock_cosine_sim,
            conflict_threshold=0.5,
        )
        assert len(conflicts) >= 1

    def test_no_conflict_below_threshold(self, mock_embed_fn, mock_cosine_sim, sibling_points) -> None:
        """相似度低于阈值不应判为矛盾。"""
        conflicts = detect_conflict(
            "全新的不相关的知识点内容",
            sibling_points,
            mock_embed_fn,
            mock_cosine_sim,
            conflict_threshold=0.99,
        )
        assert len(conflicts) == 0

    def test_embedding_failure(self, sibling_points) -> None:
        """embedding 失败应返回空列表。"""
        def _fail(texts: list[str]) -> None:
            return None
        conflicts = detect_conflict("任何文本", sibling_points, _fail)
        assert conflicts == []

    def test_with_db_adapter(self, mock_embed_fn, mock_cosine_sim, sibling_points) -> None:
        """有 db_adapter 时应调用 insert_review。"""
        mock_db = MagicMock()
        conflicts = detect_conflict(
            "DBSCAN 在非均匀密度数据上无效",  # → [0.95,0,0.05], sibling[2]=同向量, sibling[2]含「优于」→ 对立
            sibling_points,
            mock_embed_fn,
            mock_cosine_sim,
            conflict_threshold=0.5,
            db_adapter=mock_db,
        )
        assert len(conflicts) >= 1
        assert mock_db.insert_review.called

    def test_without_db_adapter(self, mock_embed_fn, mock_cosine_sim, sibling_points) -> None:
        """无 db_adapter 时不应调用 insert_review。"""
        mock_db = MagicMock()
        detect_conflict(
            "HDBSCAN 在非均匀密度数据上优于 DBSCAN",
            sibling_points[:1],  # 只用一个兄弟节点
            mock_embed_fn,
            mock_cosine_sim,
            conflict_threshold=0.5,
        )
        mock_db.insert_review.assert_not_called()

    def test_empty_sibling_points(self, mock_embed_fn, mock_cosine_sim) -> None:
        """空兄弟节点列表应返回空列表。"""
        conflicts = detect_conflict("任何文本", [], mock_embed_fn, mock_cosine_sim)
        assert conflicts == []

    def test_no_k_vector_skipped(self, mock_embed_fn, mock_cosine_sim) -> None:
        """无 k_vector 的兄弟节点应跳过。"""
        points = [{"id": 301, "name": "无向量节点"}]  # 无 k_vector
        conflicts = detect_conflict("任何文本", points, mock_embed_fn, mock_cosine_sim)
        assert conflicts == []

    def test_conflict_reason_contains_similarity(self, mock_embed_fn, mock_cosine_sim, sibling_points) -> None:
        """矛盾原因应包含相似度信息。"""
        conflicts = detect_conflict(
            "HDBSCAN 在非均匀密度数据上优于 DBSCAN",
            sibling_points,
            mock_embed_fn,
            mock_cosine_sim,
            conflict_threshold=0.5,
        )
        if conflicts:
            assert "语义相似度" in conflicts[0]["reason"]


# ========== local_q ==========


class TestLocalQ:
    """Q 投影函数测试"""

    def test_cold_start_returns_global(self) -> None:
        """冷启动期应返回全局 embedding。"""
        global_emb = [1.0, 2.0, 3.0]
        result = local_q(
            global_emb,
            subject_offset=[0.1, 0.1, 0.1],
            child_count=5,
            cold_start_threshold=20,
        )
        assert result == global_emb

    def test_no_offset_returns_global(self) -> None:
        """无偏移向量时应返回全局 embedding。"""
        global_emb = [1.0, 2.0, 3.0]
        result = local_q(
            global_emb,
            subject_offset=None,
            child_count=30,
            cold_start_threshold=20,
        )
        assert result == global_emb

    def test_normal_projection(self) -> None:
        """正常投影应应用偏移。"""
        global_emb = [1.0, 0.0, 0.0]
        offset = [0.5, 0.5, 0.0]
        result = local_q(
            global_emb,
            offset,
            offset_coefficient=0.3,
            child_count=30,
            cold_start_threshold=20,
        )
        expected = [1.0 + 0.5 * 0.3, 0.0 + 0.5 * 0.3, 0.0 + 0.0 * 0.3]
        assert result == pytest.approx(expected)

    def test_cold_start_edge_threshold(self) -> None:
        """等于冷启动阈值应回退（child_count < threshold 才冷启动）。"""
        global_emb = [1.0, 0.0]
        # child_count=20, threshold=20 → 20 < 20 为 False → warm 路径
        result = local_q(
            global_emb,
            subject_offset=[0.5, 0.5],
            offset_coefficient=0.3,
            child_count=20,
            cold_start_threshold=20,
        )
        # warm 路径: 1.0+0.5*0.3, 0.0+0.5*0.3 = [1.15, 0.15]
        assert result != global_emb

    def test_warm_start_just_above_threshold(self) -> None:
        """刚好高于冷启动阈值应计算投影。"""
        global_emb = [1.0, 0.0]
        offset = [0.5, 0.5]
        result = local_q(
            global_emb,
            offset,
            offset_coefficient=0.3,
            child_count=21,
            cold_start_threshold=20,
        )
        assert result != global_emb

    def test_zero_offset_coefficient(self) -> None:
        """偏移系数为 0 时返回全局 embedding。"""
        global_emb = [1.0, 2.0]
        result = local_q(
            global_emb,
            subject_offset=[10.0, 10.0],
            offset_coefficient=0.0,
            child_count=30,
            cold_start_threshold=20,
        )
        assert result == global_emb

    def test_empty_embedding(self) -> None:
        """空 embedding 列表。"""
        result = local_q(
            [],
            subject_offset=None,
            child_count=30,
            cold_start_threshold=20,
        )
        assert result == []


# ========== compute_subject_offset ==========


class TestComputeSubjectOffset:
    """局部偏移向量计算测试"""

    def test_normal_calculation(self) -> None:
        """正常计算。"""
        subject_embs = [[1.0, 2.0], [3.0, 4.0]]
        sibling_embs = [[5.0, 6.0], [7.0, 8.0]]
        offset = compute_subject_offset(subject_embs, sibling_embs)
        # subject centroid: [2.0, 3.0]
        # sibling centroid: [6.0, 7.0]
        # offset: [-4.0, -4.0]
        assert offset == pytest.approx([-4.0, -4.0])

    def test_empty_subject(self) -> None:
        """空 subject 应返回全零向量。"""
        sibling_embs = [[1.0, 2.0, 3.0]]
        offset = compute_subject_offset([], sibling_embs)
        assert offset == [0.0, 0.0, 0.0]

    def test_empty_sibling(self) -> None:
        """空 sibling 应使用零向量作为 sibling centroid。"""
        subject_embs = [[1.0, 2.0], [3.0, 4.0]]
        offset = compute_subject_offset(subject_embs, [])
        # subject centroid: [2.0, 3.0], sibling centroid: [0.0, 0.0]
        assert offset == pytest.approx([2.0, 3.0])

    def test_both_empty(self) -> None:
        """两者都空应返回 1024 维零向量（默认维度）。"""
        offset = compute_subject_offset([], [])
        assert len(offset) == 1024
        assert all(v == 0.0 for v in offset)

    def test_single_subject(self) -> None:
        """单个 subject 向量。"""
        subject_embs = [[2.0, 4.0]]
        sibling_embs = [[1.0, 1.0]]
        offset = compute_subject_offset(subject_embs, sibling_embs)
        assert offset == pytest.approx([1.0, 3.0])

    def test_single_sibling(self) -> None:
        """单个 sibling 向量。"""
        subject_embs = [[1.0, 1.0], [3.0, 3.0]]
        sibling_embs = [[2.0, 2.0]]
        offset = compute_subject_offset(subject_embs, sibling_embs)
        # subject centroid: [2.0, 2.0], sibling centroid: [2.0, 2.0]
        assert offset == pytest.approx([0.0, 0.0])

    def test_high_dimension(self) -> None:
        """高维向量。"""
        subject_embs = [[float(i) for i in range(10)]]
        sibling_embs = [[float(i * 2) for i in range(10)]]
        offset = compute_subject_offset(subject_embs, sibling_embs)
        assert len(offset) == 10
        # offset[i] = i - 2*i = -i
        for i in range(10):
            assert offset[i] == pytest.approx(-float(i))
