"""测试 consolidation 模块 — 拆分/合并/评分"""

from knowledge_tree_builder.core.consolidation import ConsolidationEngine


class TestConsolidationEngine:
    """测试纠错回路"""

    def test_empty_subjects(self) -> None:
        """空科目列表不应报错"""
        engine = ConsolidationEngine()
        result = engine.run(subjects=[], dry_run=True)
        assert result["status"] == "no_data"

    def test_no_split_below_threshold(self) -> None:
        """知识点数低于阈值时不触发拆分"""
        engine = ConsolidationEngine()
        result = engine.split_subject(
            1, "小科目",
            ["点A"] * 10,  # 10 条 < 50
            dry_run=True,
            split_threshold=50,
        )
        assert result is None

    def test_split_above_threshold_no_embedding(self) -> None:
        """超过阈值但无 embedding 时跳过"""
        engine = ConsolidationEngine()
        result = engine.split_subject(
            1, "大科目",
            ["点A"] * 60,  # > 50
            embeddings=None,
            dry_run=True,
        )
        assert result is not None
        assert result["status"] == "skipped"

    def test_score_subjects(self) -> None:
        """评分函数应正确计算"""
        engine = ConsolidationEngine()
        subjects = [
            {"id": 1, "placement_delta": 10, "k_vector_change": 0.5, "days_since_review": 7},
            {"id": 2, "placement_delta": 0, "k_vector_change": 0, "days_since_review": 100},
        ]
        scored = engine.score_subjects(subjects)
        assert scored[0]["score"] > scored[1]["score"]

    def test_check_merge_no_suggestion(self) -> None:
        """低共现率不应建议合并"""
        engine = ConsolidationEngine()
        subjects = [
            {"id": 1, "point_count": 5, "last_placement_day": 0},
            {"id": 2, "point_count": 3, "last_placement_day": 30},
        ]
        suggestions = engine.check_merge(subjects, merge_cooccurrence=0.80)
        assert len(suggestions) == 0

    def test_run_with_subjects(self) -> None:
        """完整的 run 流程"""
        engine = ConsolidationEngine()
        subjects = [
            {"id": 1, "name": "小科A", "point_count": 10, "placement_delta": 1,
             "k_vector_change": 0.1, "days_since_review": 5, "last_placement_day": 1,
             "points": [], "embeddings": None},
            {"id": 2, "name": "小科B", "point_count": 8, "placement_delta": 0,
             "k_vector_change": 0, "days_since_review": 60, "last_placement_day": 30,
             "points": [], "embeddings": None},
        ]
        result = engine.run(subjects, dry_run=True, split_threshold=50)
        assert result["status"] == "completed"
        assert len(result["reviews"]) == 2  # top_n=5, 只有2个
        assert result["splits"] == []  # 都没超过 50

    def test_merge_suggestion_high_cooccurrence(self) -> None:
        """真实使用日志中高共现率的小科应建议合并。"""
        engine = ConsolidationEngine()
        subjects = [
            {"id": 1, "name": "CMP 工艺", "point_count": 5},
            {"id": 2, "name": "平坦化技术", "point_count": 3},
        ]

        class FakeAdapter:
            def __init__(self) -> None:
                self.edges: list[tuple[int, int, str]] = []

            def query_cooccurrence(self, subject_id: int) -> dict[int, float]:
                return {2: 0.9} if subject_id == 1 else {}

            def insert_edge(self, from_id: int, to_id: int, relation_type: str) -> None:
                self.edges.append((from_id, to_id, relation_type))

        adapter = FakeAdapter()
        suggestions = engine.check_merge(subjects, merge_cooccurrence=0.80, db_adapter=adapter)
        assert len(suggestions) >= 1
        assert "合并" in suggestions[0]["suggested_action"]
        assert adapter.edges == [(1, 2, "related")]
