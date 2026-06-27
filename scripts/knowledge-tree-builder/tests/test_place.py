"""测试 place.py — 领域匹配 + 科目匹配 + PG 写入"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from knowledge_tree_builder.models import AtomicKnowledge
from knowledge_tree_builder.place import (
    PlacementResult,
    _derive_domain_from_title,
    _match_domain_via_llm,
    _match_or_create_subject,
    place_knowledge,
)


class TestDeriveDomainFromTitle:
    def test_english_title(self) -> None:
        assert _derive_domain_from_title("MLOps Clustering Guide") == "mlops"

    def test_chinese_title(self) -> None:
        domain = _derive_domain_from_title("自进化智能体技术综述")
        assert domain == "自进化智能体技术综述"

    def test_mixed_title(self) -> None:
        domain = _derive_domain_from_title("SE-Agent 自进化智能体")
        assert domain == "se-agent"

    def test_empty_title(self) -> None:
        assert _derive_domain_from_title("") == "general"

    def test_special_chars(self) -> None:
        assert _derive_domain_from_title("!!!") == "general"


class TestMatchDomainViaLlm:
    def test_with_llm_fn(self) -> None:
        def llm_fn(ts: str, ed: list[str]) -> str:
            return "mlops/clustering"
        assert _match_domain_via_llm("title", "summary", [], llm_fn) == "mlops/clustering"

    def test_without_llm_fn(self) -> None:
        domain = _match_domain_via_llm("Clustering Guide", "content", [], None)
        assert domain == "clustering"

    def test_llm_fn_receives_combined(self) -> None:
        captured: list[str] = []
        def llm_fn(ts: str, ed: list[str]) -> str:
            captured.append(ts)
            return "test"
        _match_domain_via_llm("Title", "Summary", ["existing"], llm_fn)
        assert "Title Summary" in captured[0]


class TestMatchOrCreateSubject:
    def test_match_existing(self) -> None:
        subjects = [{"id": "1", "name": "聚类算法", "k_vector": [1.0, 0.0]}]
        name, is_new = _match_or_create_subject(
            "HDBSCAN 聚类",
            "test",
            subjects,
            embed_fn=lambda texts: [[1.0, 0.0]],
            cosine_sim_fn=lambda a, b: 0.95,
            threshold=0.7,
        )
        assert name == "聚类算法"
        assert is_new is False

    def test_create_new(self) -> None:
        subjects = [{"id": "1", "name": "聚类算法", "k_vector": [1.0, 0.0]}]
        name, is_new = _match_or_create_subject(
            "完全不同的话题",
            "test",
            subjects,
            embed_fn=lambda texts: [[0.0, 1.0]],
            cosine_sim_fn=lambda a, b: 0.1,
            threshold=0.7,
        )
        assert is_new is True

    def test_embedding_failure(self) -> None:
        name, is_new = _match_or_create_subject(
            "知识文本", "test", [],
            embed_fn=lambda texts: None,
            cosine_sim_fn=lambda a, b: 0.0,
        )
        assert name == "其他"
        assert is_new is True

    def test_empty_subjects(self) -> None:
        name, is_new = _match_or_create_subject(
            "知识文本", "test", [],
            embed_fn=lambda texts: [[1.0, 0.0]],
            cosine_sim_fn=lambda a, b: 0.0,
        )
        assert is_new is True


class TestPlaceKnowledge:
    @pytest.fixture
    def atomics(self) -> list[AtomicKnowledge]:
        return [
            AtomicKnowledge(text="HDBSCAN 通过层次聚类覆盖非均匀密度簇", type="principle", claims_count=1, source_candidate_index=0),
            AtomicKnowledge(text="RAG 通过外部知识库增强 LLM 生成质量", type="principle", claims_count=1, source_candidate_index=1),
        ]

    def test_empty_list(self) -> None:
        result = place_knowledge([], "title", "summary")
        assert result.stats["total"] == 0
        assert len(result.records) == 0

    def test_no_db_adapter(self, atomics) -> None:
        result = place_knowledge(atomics, "Test Title", "summary content")
        assert result.stats["placed"] == 2
        assert result.stats["errors"] == 0
        assert result.records[0]["domain"] == "test"
        assert result.records[1]["domain"] == "test"

    def test_with_db_adapter(self, atomics) -> None:
        mock_db = MagicMock()
        mock_db.get_all_domains.return_value = ["mlops"]
        mock_db.get_subjects_by_domain.return_value = []
        mock_db.find_or_create_subject.side_effect = [1, 2, 3, 4]

        result = place_knowledge(
            atomics, "ML Clustering", "clustering methods",
            db_adapter=mock_db,
            embed_fn=lambda texts: [[1.0, 0.0], [0.0, 1.0]],
            cosine_sim_fn=lambda a, b: 0.5,
        )
        assert result.stats["placed"] == 2
        assert mock_db.find_or_create_subject.called

    def test_adapter_error_does_not_block(self, atomics) -> None:
        mock_db = MagicMock()
        mock_db.get_all_domains.side_effect = Exception("DB down")

        result = place_knowledge(
            atomics, "Title", "summary",
            db_adapter=mock_db,
            embed_fn=lambda texts: [[1.0, 0.0], [0.0, 1.0]],
        )
        # DB 失败不应阻断领域匹配和记录生成
        assert result.stats["placed"] == 2

    def test_cold_start_uses_root_subject(self, atomics) -> None:
        """冷启动时所有知识应挂在 domain/root 下。"""
        mock_db = MagicMock()
        mock_db.get_subjects_by_domain.return_value = []  # 0 subjects → cold start

        result = place_knowledge(
            atomics, "Title", "summary",
            db_adapter=mock_db,
            embed_fn=lambda texts: [[1.0, 0.0], [0.0, 1.0]],
        )

        assert "/root" in result.records[0]["subject"]
        assert "/root" in result.records[1]["subject"]

    def test_warm_start(self, atomics) -> None:
        """足够子节点时不走冷启动。"""
        mock_db = MagicMock()
        mock_db.get_subjects_by_domain.return_value = [
            {"id": 10, "name": "聚类", "k_vector": [1.0, 0.0]},
            {"id": 11, "name": "RAG", "k_vector": [0.0, 1.0]},
            {"id": 12, "name": "Agent", "k_vector": [0.5, 0.5]},
        ]

        result = place_knowledge(
            atomics, "Title", "summary",
            db_adapter=mock_db,
            embed_fn=lambda texts: [[1.0, 0.0], [0.0, 1.0]],
            cosine_sim_fn=lambda a, b: 0.95,  # 高相似度 → 匹配已有
        )

        assert "general/root" not in result.records[0]["subject"]

    def test_warm_start_reuses_batch_embeddings_for_subject_matching(self, atomics) -> None:
        """warm start 下科目匹配应复用已批量计算的 embedding，不再逐条调用 embed_fn。"""
        mock_db = MagicMock()
        mock_db.get_subjects_by_domain.return_value = [
            {"id": 10, "name": "聚类", "k_vector": [1.0, 0.0]},
            {"id": 11, "name": "RAG", "k_vector": [0.0, 1.0]},
            {"id": 12, "name": "Agent", "k_vector": [0.5, 0.5]},
        ]
        calls: list[list[str]] = []

        def embed_fn(texts: list[str]) -> list[list[float]]:
            calls.append(list(texts))
            return [[1.0, 0.0], [0.0, 1.0]][: len(texts)]

        place_knowledge(
            atomics, "Title", "summary",
            db_adapter=mock_db,
            embed_fn=embed_fn,
            cosine_sim_fn=lambda a, b: 0.95,
        )

        assert calls == [[a["text"] for a in atomics]]
