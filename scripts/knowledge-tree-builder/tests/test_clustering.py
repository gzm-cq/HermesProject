"""测试 clustering 模块"""

import numpy as np
import pytest

from knowledge_tree_builder.core.clustering import (
    _sub_cluster,
    _generate_report,
    _count_leaves,
    _avg_depth,
)
from knowledge_tree_builder.core.embeddings import cosine_similarity, cosine_similarity_matrix


class TestClustering:
    """测试聚类模块"""

    def test_cosine_similarity(self) -> None:
        """测试余弦相似度计算"""
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        assert abs(cosine_similarity(a, b)) < 0.01

        # 相同向量
        assert abs(cosine_similarity(a, a) - 1.0) < 0.01

    def test_cosine_similarity_matrix(self) -> None:
        """测试批量余弦相似度矩阵"""
        embeddings = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        sim = cosine_similarity_matrix(embeddings)
        assert sim.shape == (3, 3)
        assert abs(sim[0, 0] - 1.0) < 0.01
        assert abs(sim[0, 1]) < 0.01

    def test_generate_report(self) -> None:
        """测试建树报告生成"""
        tree = [
            {"type": "leaf", "points": ["p1", "p2", "p3"]},
            {
                "type": "node",
                "children": [
                    {"type": "leaf", "points": ["p4", "p5"]},
                    {"type": "leaf", "points": ["p6"]},
                ],
            },
        ]
        report = _generate_report(tree, ["p7", "p8"], ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"])
        assert report["total_points"] == 8
        assert report["noise_count"] == 2
        assert report["noise_ratio"] == 0.25
        assert report["cluster_count"] == 3
        assert report["avg_depth"] > 0

    def test_count_leaves(self) -> None:
        """测试叶子计数"""
        tree = [
            {"type": "leaf", "points": ["p1"]},
            {
                "type": "node",
                "children": [
                    {"type": "leaf", "points": ["p2", "p3"]},
                    {"type": "leaf", "points": ["p4"]},
                ],
            },
        ]
        counts = _count_leaves(tree)
        assert len(counts) == 3
        assert counts[0] == 1
        assert counts[1] == 2
        assert counts[2] == 1

    def test_sub_cluster_termination(self) -> None:
        """测试 sub-clustering 终止条件"""
        # 2 个点 <= min_cluster_size=5 => 直接叶子
        embeddings = np.array([[0.1, 0.1], [0.2, 0.2]])
        result = _sub_cluster(embeddings, ["p1", "p2"], depth=0, max_depth=5, min_cluster_size=5,
                              cluster_selection_method="eom")
        assert result["type"] == "leaf"
        assert len(result["points"]) == 2

    def test_auto_dry_run_large_cluster(self, sample_knowledge_points, sample_embeddings) -> None:
        """测试自动干跑（用 sample_embeddings 模拟真实 embedding，min_cluster_size 调低）"""
        from knowledge_tree_builder.core.clustering import auto_dry_run

        points = sample_knowledge_points
        embs = np.array(sample_embeddings, dtype=np.float32)

        result = auto_dry_run(points, embs, max_attempts=1, min_cluster_size=2, max_depth=3)
        assert "report" in result
        # 不做严格断言，保证运行不报错即可
