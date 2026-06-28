"""dataset.py 单元测试 — 数据集管理。"""

import json
from pathlib import Path

import pytest

from recall_eval.core.dataset import EvalDataset, EvalQuery, generate_eval_samples, load_dataset


class TestEvalQuery:
    """EvalQuery 数据类测试。"""

    def test_create_from_dict(self) -> None:
        data = {
            "query_id": "test_01",
            "query": "测试查询",
            "category": "test",
            "expected_context": "预期上下文",
            "expected_answer": "预期回答",
        }
        q = EvalQuery.from_dict(data)
        assert q.query_id == "test_01"
        assert q.query == "测试查询"
        assert q.category == "test"
        assert q.expected_context == "预期上下文"
        assert q.expected_answer == "预期回答"

    def test_to_dict(self) -> None:
        q = EvalQuery(
            query_id="test_01",
            query="测试查询",
            category="test",
            expected_context="上下文",
            expected_answer="回答",
        )
        d = q.to_dict()
        assert d["query_id"] == "test_01"
        assert d["query"] == "测试查询"
        assert d["category"] == "test"

    def test_partial_fields(self) -> None:
        q = EvalQuery.from_dict({"query_id": "q1", "query": "hello"})
        assert q.query_id == "q1"
        assert q.query == "hello"
        assert q.category == ""
        assert q.expected_context == ""


class TestEvalDataset:
    """EvalDataset 数据集测试。"""

    def test_empty_dataset(self) -> None:
        ds = EvalDataset()
        assert len(ds) == 0
        assert ds.name == "default"

    def test_with_queries(self, sample_eval_queries: list[EvalQuery]) -> None:
        ds = EvalDataset(name="test", queries=sample_eval_queries)
        assert len(ds) == 3
        assert ds.name == "test"

    def test_iteration(self, sample_dataset: EvalDataset) -> None:
        ids = [q.query_id for q in sample_dataset]
        assert "test_01" in ids
        assert "test_02" in ids
        assert "test_03" in ids

    def test_indexing(self, sample_dataset: EvalDataset) -> None:
        assert sample_dataset[0].query_id == "test_01"

    def test_by_category(self, sample_dataset: EvalDataset) -> None:
        categories = sample_dataset.by_category()
        assert "semantic" in categories
        assert "debug" in categories
        assert "api" in categories
        assert len(categories["semantic"]) == 1

    def test_categories(self, sample_dataset: EvalDataset) -> None:
        cats = sample_dataset.categories()
        assert set(cats) == {"semantic", "debug", "api"}

    def test_filter_by_category(self, sample_dataset: EvalDataset) -> None:
        filtered = sample_dataset.filter_by_category("debug")
        assert len(filtered) == 1
        assert filtered[0].query_id == "test_02"

    def test_save_and_load(self, tmp_path: Path, sample_dataset: EvalDataset) -> None:
        output_path = tmp_path / "output.json"
        sample_dataset.save(str(output_path))
        assert output_path.exists()

        loaded = EvalDataset.load(str(output_path))
        assert loaded.name == sample_dataset.name
        assert len(loaded) == len(sample_dataset)

    def test_load_array_format(self, temp_dataset_file: Path) -> None:
        ds = EvalDataset.load(str(temp_dataset_file))
        assert len(ds) == 3
        assert ds.name == temp_dataset_file.stem

    def test_load_nonexistent_file(self) -> None:
        ds = EvalDataset.load("/nonexistent/path.json")
        assert len(ds) == 0


class TestLoadDataset:
    """load_dataset 便捷函数测试。"""

    def test_load_dataset(self, temp_dataset_file: Path) -> None:
        ds = load_dataset(str(temp_dataset_file))
        assert isinstance(ds, EvalDataset)
        assert len(ds) == 3


class TestGenerateEvalSamples:
    """generate_eval_samples 测试。"""

    def test_basic_generation(self, sample_dataset: EvalDataset) -> None:
        samples = generate_eval_samples(sample_dataset)
        assert len(samples) == len(sample_dataset)
        assert samples[0]["query_id"] == "test_01"
        assert "query" in samples[0]
        assert "context" in samples[0]
        assert "answer" in samples[0]

    def test_with_context_provider(self, sample_dataset: EvalDataset) -> None:
        def provider(query: str) -> str:
            return f"context for: {query}"

        samples = generate_eval_samples(sample_dataset, context_provider=provider)
        assert "context for:" in samples[0]["context"]

    def test_with_answer_provider(self, sample_dataset: EvalDataset) -> None:
        def answer_provider(query: str, context: str) -> str:
            return f"answer for: {query}"

        samples = generate_eval_samples(sample_dataset, answer_provider=answer_provider)
        assert "answer for:" in samples[0]["answer"]

    def test_context_provider_error(self, sample_dataset: EvalDataset) -> None:
        def bad_provider(query: str) -> str:
            raise RuntimeError("test error")

        samples = generate_eval_samples(sample_dataset, context_provider=bad_provider)
        assert samples[0]["context"] == ""
        assert "context_error" in samples[0]
