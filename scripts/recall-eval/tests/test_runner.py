"""runner.py 单元测试 — 评估运行器。"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from recall_eval.config import AppConfig
from recall_eval.core.dataset import EvalDataset, EvalQuery
from recall_eval.core.runner import EvalReport, EvalResult, EvalRunner, print_report


class TestEvalResult:
    """EvalResult 测试。"""

    def test_to_dict(self) -> None:
        result = EvalResult(
            query_id="test_01",
            query="test query",
            category="test",
            context="context",
            answer="answer",
            faithfulness={"score": 0.9},
            relevance={"score": 0.8},
            coverage={"score": 0.7},
            error="",
        )
        d = result.to_dict()
        assert d["query_id"] == "test_01"
        assert d["faithfulness"]["score"] == 0.9
        assert d["error"] == ""

    def test_with_error(self) -> None:
        result = EvalResult(
            query_id="test_01",
            query="test",
            error="something went wrong",
        )
        d = result.to_dict()
        assert d["error"] == "something went wrong"


class TestEvalReport:
    """EvalReport 测试。"""

    def test_to_dict(self) -> None:
        report = EvalReport(
            dataset_name="test",
            total_queries=10,
            successful_queries=8,
            failed_queries=2,
            avg_faithfulness=0.85,
            avg_relevance=0.8,
            avg_coverage=0.75,
            overall_score=0.8,
            timestamp="2026-01-01T00:00:00",
            duration_seconds=10.5,
        )
        d = report.to_dict()
        assert d["dataset_name"] == "test"
        assert d["total_queries"] == 10
        assert d["overall_score"] == 0.8

    def test_save(self, tmp_path: Path) -> None:
        report = EvalReport(dataset_name="test", total_queries=5)
        output_file = tmp_path / "report.json"
        saved_path = report.save(str(output_file))
        assert saved_path.exists()
        assert saved_path == output_file


class TestEvalRunner:
    """EvalRunner 评估运行器测试。"""

    def test_run_heuristic(self, app_config: AppConfig, sample_dataset: EvalDataset) -> None:
        runner = EvalRunner(config=app_config)
        report = runner.run(sample_dataset)

        assert isinstance(report, EvalReport)
        assert report.total_queries == len(sample_dataset)
        assert report.successful_queries == len(sample_dataset)
        assert report.failed_queries == 0
        assert 0.0 <= report.overall_score <= 1.0
        assert 0.0 <= report.avg_faithfulness <= 1.0
        assert 0.0 <= report.avg_relevance <= 1.0
        assert 0.0 <= report.avg_coverage <= 1.0

    def test_run_with_mock_llm(
        self, app_config: AppConfig, sample_dataset: EvalDataset, mock_llm_client: MagicMock
    ) -> None:
        runner = EvalRunner(config=app_config, llm_client=mock_llm_client)
        report = runner.run(sample_dataset)

        assert report.total_queries == len(sample_dataset)
        assert report.successful_queries == len(sample_dataset)

    def test_run_with_context_provider(
        self, app_config: AppConfig, sample_dataset: EvalDataset
    ) -> None:
        def context_provider(query: str) -> str:
            return f"custom context for {query}"

        runner = EvalRunner(config=app_config, context_provider=context_provider)
        report = runner.run(sample_dataset)

        assert report.successful_queries == len(sample_dataset)
        for result in report.results:
            assert "custom context for" in result.context

    def test_run_with_answer_provider(
        self, app_config: AppConfig, sample_dataset: EvalDataset
    ) -> None:
        def answer_provider(query: str, context: str) -> str:
            return f"custom answer for {query}"

        runner = EvalRunner(config=app_config, answer_provider=answer_provider)
        report = runner.run(sample_dataset)

        assert report.successful_queries == len(sample_dataset)
        for result in report.results:
            assert "custom answer for" in result.answer

    def test_category_scores(self, app_config: AppConfig, sample_dataset: EvalDataset) -> None:
        runner = EvalRunner(config=app_config)
        report = runner.run(sample_dataset)

        assert len(report.category_scores) == 3
        for cat, scores in report.category_scores.items():
            assert "count" in scores
            assert "faithfulness" in scores
            assert "relevance" in scores
            assert "coverage" in scores
            assert "overall" in scores

    def test_empty_dataset(self, app_config: AppConfig) -> None:
        empty_ds = EvalDataset(name="empty", queries=[])
        runner = EvalRunner(config=app_config)
        report = runner.run(empty_ds)

        assert report.total_queries == 0
        assert report.successful_queries == 0
        assert report.overall_score == 0.0

    def test_sequential_mode(self, app_config: AppConfig, sample_dataset: EvalDataset) -> None:
        app_config.max_workers = 1
        runner = EvalRunner(config=app_config)
        report = runner.run(sample_dataset)
        assert report.successful_queries == len(sample_dataset)


class TestPrintReport:
    """print_report 输出测试。"""

    def test_print_report(self, capsys: pytest.CaptureFixture[str]) -> None:
        report = EvalReport(
            dataset_name="test_ds",
            total_queries=5,
            successful_queries=4,
            failed_queries=1,
            avg_faithfulness=0.85,
            avg_relevance=0.8,
            avg_coverage=0.75,
            overall_score= 0.8,
            timestamp="2026-01-01T00:00:00",
            duration_seconds=5.2,
        )
        print_report(report)
        captured = capsys.readouterr()
        assert "Recall 评估报告" in captured.out
        assert "test_ds" in captured.out
        assert "整体得分" in captured.out
