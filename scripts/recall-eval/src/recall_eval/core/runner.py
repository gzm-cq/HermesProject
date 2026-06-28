"""评估运行器 — 批量运行评估、生成报告。"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from recall_eval.adapters.llm_client import LLMClient
from recall_eval.config import AppConfig
from recall_eval.core.dataset import EvalDataset, generate_eval_samples
from recall_eval.core.metrics import coverage_score, faithfulness_score, relevance_score

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    """单个查询的评估结果。"""

    query_id: str
    query: str
    category: str = ""
    context: str = ""
    answer: str = ""
    faithfulness: dict[str, Any] = field(default_factory=dict)
    relevance: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "query_id": self.query_id,
            "query": self.query,
            "category": self.category,
            "context": self.context,
            "answer": self.answer,
            "faithfulness": self.faithfulness,
            "relevance": self.relevance,
            "coverage": self.coverage,
            "error": self.error,
        }


@dataclass
class EvalReport:
    """完整的评估报告。"""

    dataset_name: str = ""
    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0
    avg_faithfulness: float = 0.0
    avg_relevance: float = 0.0
    avg_coverage: float = 0.0
    overall_score: float = 0.0
    category_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    results: list[EvalResult] = field(default_factory=list)
    timestamp: str = ""
    duration_seconds: float = 0.0
    tokens: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "dataset_name": self.dataset_name,
            "total_queries": self.total_queries,
            "successful_queries": self.successful_queries,
            "failed_queries": self.failed_queries,
            "avg_faithfulness": round(self.avg_faithfulness, 4),
            "avg_relevance": round(self.avg_relevance, 4),
            "avg_coverage": round(self.avg_coverage, 4),
            "overall_score": round(self.overall_score, 4),
            "category_scores": {
                cat: {k: round(v, 4) for k, v in scores.items()}
                for cat, scores in self.category_scores.items()
            },
            "results": [r.to_dict() for r in self.results],
            "timestamp": self.timestamp,
            "duration_seconds": round(self.duration_seconds, 2),
            "tokens": self.tokens,
        }

    def save(self, output_path: str) -> Path:
        """保存报告到 JSON 文件。"""
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        with open(output, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

        logger.info("评估报告已保存到: %s", output)
        return output


class EvalRunner:
    """评估运行器，执行批量评估。"""

    def __init__(
        self,
        config: AppConfig,
        llm_client: LLMClient | None = None,
        context_provider: Callable[[str], str] | None = None,
        answer_provider: Callable[[str, str], str] | None = None,
    ) -> None:
        """初始化评估运行器。

        Args:
            config: 应用配置
            llm_client: LLM 客户端，为 None 时使用启发式评估
            context_provider: 上下文提供函数
            answer_provider: 回答生成函数
        """
        self._config = config
        self._llm_client = llm_client
        self._context_provider = context_provider
        self._answer_provider = answer_provider

    def run(self, dataset: EvalDataset) -> EvalReport:
        """运行完整评估。

        Args:
            dataset: 评估数据集

        Returns:
            评估报告
        """
        start_time = time.monotonic()
        logger.info("开始评估数据集: %s (%d 条查询)", dataset.name, len(dataset))

        samples = generate_eval_samples(
            dataset,
            context_provider=self._context_provider,
            answer_provider=self._answer_provider,
        )

        results: list[EvalResult] = []
        successful = 0
        failed = 0

        if self._config.max_workers > 1:
            results = self._run_parallel(samples)
        else:
            results = self._run_sequential(samples)

        for r in results:
            if r.error:
                failed += 1
            else:
                successful += 1

        report = self._build_report(dataset, results, successful, failed, start_time)

        logger.info(
            "评估完成: 成功 %d, 失败 %d, 总体得分 %.3f",
            successful,
            failed,
            report.overall_score,
        )
        return report

    def _run_sequential(self, samples: list[dict[str, Any]]) -> list[EvalResult]:
        """顺序执行评估。"""
        results: list[EvalResult] = []
        for i, sample in enumerate(samples):
            logger.info(
                "评估进度: %d/%d - %s",
                i + 1,
                len(samples),
                sample.get("query_id", ""),
            )
            result = self._evaluate_one(sample)
            results.append(result)
        return results

    def _run_parallel(self, samples: list[dict[str, Any]]) -> list[EvalResult]:
        """并行执行评估。"""
        results: list[EvalResult] = []
        completed = 0
        total = len(samples)

        with ThreadPoolExecutor(max_workers=self._config.max_workers) as executor:
            futures = {
                executor.submit(self._evaluate_one, sample): sample for sample in samples
            }

            for future in as_completed(futures):
                completed += 1
                sample = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    if completed % self._config.batch_size == 0 or completed == total:
                        logger.info("评估进度: %d/%d", completed, total)
                except Exception as e:
                    logger.warning(
                        "评估异常 [%s]: %s", sample.get("query_id", "unknown"), e
                    )
                    results.append(
                        EvalResult(
                            query_id=sample.get("query_id", "unknown"),
                            query=sample.get("query", ""),
                            category=sample.get("category", ""),
                            error=str(e),
                        )
                    )

        results.sort(key=lambda r: r.query_id)
        return results

    def _evaluate_one(self, sample: dict[str, Any]) -> EvalResult:
        """评估单个样本。"""
        query_id = sample.get("query_id", "")
        query = sample.get("query", "")
        context = sample.get("context", "")
        answer = sample.get("answer", "")
        category = sample.get("category", "")

        try:
            faith_result = faithfulness_score(query, context, answer, self._llm_client)
            rel_result = relevance_score(query, context, self._llm_client)
            cov_result = coverage_score(query, context, self._llm_client)

            return EvalResult(
                query_id=query_id,
                query=query,
                category=category,
                context=context,
                answer=answer,
                faithfulness=faith_result,
                relevance=rel_result,
                coverage=cov_result,
            )
        except Exception as e:
            logger.warning("评估失败 [%s]: %s", query_id, e)
            return EvalResult(
                query_id=query_id,
                query=query,
                category=category,
                context=context,
                answer=answer,
                error=str(e),
            )

    def _build_report(
        self,
        dataset: EvalDataset,
        results: list[EvalResult],
        successful: int,
        failed: int,
        start_time: float,
    ) -> EvalReport:
        """构建评估报告。"""
        valid_results = [r for r in results if not r.error]

        faith_scores = [r.faithfulness.get("score", 0.0) for r in valid_results]
        rel_scores = [r.relevance.get("score", 0.0) for r in valid_results]
        cov_scores = [r.coverage.get("score", 0.0) for r in valid_results]

        avg_faith = sum(faith_scores) / len(faith_scores) if faith_scores else 0.0
        avg_rel = sum(rel_scores) / len(rel_scores) if rel_scores else 0.0
        avg_cov = sum(cov_scores) / len(cov_scores) if cov_scores else 0.0

        overall = (avg_faith * 0.4 + avg_rel * 0.3 + avg_cov * 0.3) if valid_results else 0.0

        category_scores: dict[str, dict[str, float]] = {}
        by_category: dict[str, list[EvalResult]] = {}
        for r in valid_results:
            cat = r.category or "uncategorized"
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(r)

        for cat, cat_results in by_category.items():
            cat_faith = [r.faithfulness.get("score", 0.0) for r in cat_results]
            cat_rel = [r.relevance.get("score", 0.0) for r in cat_results]
            cat_cov = [r.coverage.get("score", 0.0) for r in cat_results]
            category_scores[cat] = {
                "count": len(cat_results),
                "faithfulness": sum(cat_faith) / len(cat_faith) if cat_faith else 0.0,
                "relevance": sum(cat_rel) / len(cat_rel) if cat_rel else 0.0,
                "coverage": sum(cat_cov) / len(cat_cov) if cat_cov else 0.0,
                "overall": (
                    (sum(cat_faith) / len(cat_faith)) * 0.4
                    + (sum(cat_rel) / len(cat_rel)) * 0.3
                    + (sum(cat_cov) / len(cat_cov)) * 0.3
                )
                if cat_results
                else 0.0,
            }

        tokens: dict[str, int] = {}
        if self._llm_client is not None:
            tokens = {
                "prompt_tokens": self._llm_client.total_prompt_tokens,
                "completion_tokens": self._llm_client.total_completion_tokens,
                "total_tokens": self._llm_client.total_prompt_tokens
                + self._llm_client.total_completion_tokens,
            }

        return EvalReport(
            dataset_name=dataset.name,
            total_queries=len(results),
            successful_queries=successful,
            failed_queries=failed,
            avg_faithfulness=avg_faith,
            avg_relevance=avg_rel,
            avg_coverage=avg_cov,
            overall_score=overall,
            category_scores=category_scores,
            results=results,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            duration_seconds=time.monotonic() - start_time,
            tokens=tokens,
        )


def print_report(report: EvalReport) -> None:
    """以人类可读格式打印报告摘要。"""
    print(f"\n{'=' * 70}")
    print(f"  📊 Recall 评估报告")
    print(f"{'=' * 70}")
    print(f"  数据集: {report.dataset_name}")
    print(f"  时间: {report.timestamp}")
    print(f"  耗时: {report.duration_seconds:.1f}s")
    print(f"{'─' * 70}")
    print(f"  总查询数: {report.total_queries}")
    print(f"  成功: {report.successful_queries}  失败: {report.failed_queries}")
    print(f"{'─' * 70}")
    print(f"  整体得分: {report.overall_score:.3f}")
    print(f"  ├─ 忠实度 (Faithfulness): {report.avg_faithfulness:.3f} (权重 40%)")
    print(f"  ├─ 相关性 (Relevance):    {report.avg_relevance:.3f} (权重 30%)")
    print(f"  └─ 覆盖率 (Coverage):     {report.avg_coverage:.3f} (权重 30%)")

    if report.category_scores:
        print(f"{'─' * 70}")
        print(f"  分类得分:")
        for cat, scores in sorted(report.category_scores.items()):
            print(
                f"  {cat:>20s}: overall={scores['overall']:.3f}  "
                f"faith={scores['faithfulness']:.3f}  "
                f"rel={scores['relevance']:.3f}  "
                f"cov={scores['coverage']:.3f}  "
                f"(n={scores['count']})"
            )

    if report.tokens:
        print(f"{'─' * 70}")
        print(
            f"  Token 消耗: prompt={report.tokens.get('prompt_tokens', 0):,}  "
            f"completion={report.tokens.get('completion_tokens', 0):,}  "
            f"total={report.tokens.get('total_tokens', 0):,}"
        )

    print(f"{'=' * 70}\n")
