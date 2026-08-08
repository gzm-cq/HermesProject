from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from knowledge_navigation.config import CONFIG
from knowledge_navigation.core.filtering import calculate_score_stats, extract_score
from knowledge_navigation.core.use_log import UseLogger

logger = logging.getLogger(__name__)

RecallSource = str  # "hindsight" | "knowledge_tree" | "skill" | "sag"


@dataclass
class RecallResult:
    """单路召回的统一结果结构，用于日志和 use_log。"""

    source: RecallSource
    results: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str | None = None

    @property
    def count(self) -> int:
        return len(self.results)

    def as_extra(self) -> dict[str, Any]:
        scores = [self._extract_score(r) for r in self.results]
        return {
            "source": self.source,
            "count": self.count,
            "latency_ms": round(self.latency_ms, 1),
            "score_stats": calculate_score_stats(scores) if scores else {},
            "error": self.error,
        }

    def _extract_score(self, result: dict[str, Any]) -> float:
        """从不同格式的结果中统一提取分数（委托给公共实现）。"""
        return extract_score(result)


class RecallLogger:
    """四路召回统一日志框架。

    职责：
    1. 统一每路召回的日志字段和事件命名
    2. 统一接入 use_log
    3. 汇总 recall_success 主日志
    4. 提供计时器上下文管理器
    """

    def __init__(self, use_logger: UseLogger | None = None):
        self._use_logger = use_logger
        self._per_source: dict[RecallSource, RecallResult] = {}

    def record(
        self,
        source: RecallSource,
        results: list[dict[str, Any]],
        latency_ms: float,
        session_id: str = "",
        query: str = "",
        error: str | None = None,
    ) -> None:
        """记录一路召回的结果（成功或失败）。"""
        rr = RecallResult(source=source, results=results, latency_ms=latency_ms, error=error)
        self._per_source[source] = rr

        event = f"recall_{source}"
        extra = rr.as_extra()
        if session_id:
            extra["session_id"] = session_id

        if error:
            logger.warning(
                "%s recall failed: %s (%d results, %.1fms)",
                source, error, rr.count, latency_ms,
                extra=extra | {"event": event},
            )
        else:
            logger.info(
                "%s recall success: %d results, %.1fms",
                source, rr.count, latency_ms,
                extra=extra | {"event": event},
            )

        if self._use_logger is not None and results and not error:
            try:
                self._use_logger.log_recall(
                    query=query,
                    results=results,
                    source=source,
                    session_id=session_id,
                )
            except Exception as e:
                logger.debug("%s use_log record failed silently: %s", source, e)

    def summary(
        self,
        kept_results: list[dict[str, Any]],
        session_id: str,
        query_trunc: str,
        excluded_count: int = 0,
        kt_dedup_removed: int = 0,
        total_chars: int = 0,
        injected_count: int = 0,
        score_comparison: dict[str, Any] | None = None,
        eval_match: dict[str, Any] | None = None,
        total_latency_ms: float = 0.0,
        compressed_from: int | None = None,
        compressed_to: int | None = None,
        task_summary_round: int | None = None,
        has_knowledge_tree: bool = False,
    ) -> dict[str, Any]:
        """输出 recall_success 汇总日志，返回 log_extra 字典。"""
        hs_kept = [r for r in kept_results if r.get("source", "hindsight") == "hindsight"]
        kt_kept = [r for r in kept_results if r.get("source") == "knowledge_tree"]
        sag_kept = [r for r in kept_results if r.get("source") == "sag"]

        recalled_ids = [m.get("id", "") for m in kept_results if m.get("id")]

        # recalled_summaries：给 judge 用的召回内容摘要（前 N 字，避免 trace.log 过大）
        # 最多保留前 8 条（TOP 结果），每条截断到 200 字（约 50 tokens × 8 = 400 tokens 增量）
        _MAX_SUMMARIES = 8
        _MAX_TEXT_PER_ITEM = 200
        recalled_summaries: list[dict[str, Any]] = []
        for m in kept_results[:_MAX_SUMMARIES]:
            text_raw = (
                m.get("text")
                or m.get("content")
                or m.get("body")
                or m.get("title")
                or m.get("name")
                or ""
            )
            title = str(m.get("title") or m.get("name") or "")[:80]
            text = str(text_raw)[:_MAX_TEXT_PER_ITEM]
            if not text and not title:
                continue
            summary_item: dict[str, Any] = {
                "source": m.get("source", ""),
                "score": round(float(m.get("final_score") or m.get("score") or 0.0), 4),
            }
            if title:
                summary_item["title"] = title
            if text:
                summary_item["text"] = text
            recalled_summaries.append(summary_item)

        per_source_detail: dict[str, dict[str, Any]] = {}
        for source in ("hindsight", "knowledge_tree", "skill", "sag"):
            rr = self._per_source.get(source)
            if rr is not None:
                per_source_detail[source] = rr.as_extra()

        log_extra: dict[str, Any] = {
            "session_id": session_id,
            "query_trunc": query_trunc,
            "event": "recall_success",
            "total_results": sum(rr.count for rr in self._per_source.values()),
            "kept_results": len(kept_results),
            "excluded_marked": excluded_count,
            "kt_dedup_removed": kt_dedup_removed,
            "score_stats": calculate_score_stats(
                [self._extract_score(r) for r in kept_results]
            ) if kept_results else {},
            "injected_count": injected_count,
            "total_chars": total_chars,
            "has_knowledge_tree": has_knowledge_tree,
            "latency_ms": total_latency_ms,
            "score_comparison": score_comparison or {},
            "recalled_ids": recalled_ids,
            "recalled_summaries": recalled_summaries,
            "hs_kept": len(hs_kept),
            "kt_kept": len(kt_kept),
            "sag_kept": len(sag_kept),
            "skill_kept": 1 if self._per_source.get("skill", RecallResult(source="skill")).count > 0 else 0,
            "per_source": per_source_detail,
        }

        if compressed_from is not None and compressed_to is not None:
            log_extra["compressed_from"] = compressed_from
            log_extra["compressed_to"] = compressed_to

        if eval_match:
            log_extra["eval_match_method"] = eval_match.get("match_method", "none")
            log_extra["eval_match_confidence"] = round(float(eval_match.get("confidence", 0.0)), 4)
            log_extra["eval_counted"] = bool(eval_match.get("counted"))
            if eval_match.get("counted"):
                log_extra["eval_query_id"] = eval_match["query_id"]
                if eval_match["expected_ids"]:
                    expected_set = set(eval_match["expected_ids"])
                    log_extra["eval_expected_ids"] = eval_match["expected_ids"]
                    log_extra["eval_recall_hit"] = len(expected_set & set(recalled_ids))
                    log_extra["eval_recall_k"] = len(eval_match["expected_ids"])
            else:
                log_extra["eval_candidate_id"] = eval_match["query_id"]

        if task_summary_round is not None:
            log_extra["task_summary_round"] = task_summary_round

        logger.info("recall success", extra=log_extra)
        return log_extra

    def _extract_score(self, result: dict[str, Any]) -> float:
        for key in ("final_score", "score", "rerank_score"):
            v = result.get(key)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return 0.0

    @staticmethod
    def timing() -> "_TimingContext":
        """上下文管理器，用于测量 recall 耗时。"""
        return _TimingContext()


@dataclass
class _TimingContext:
    _start: float = 0.0

    def __enter__(self) -> "_TimingContext":
        self._start = time.time()
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    @property
    def elapsed_ms(self) -> float:
        return (time.time() - self._start) * 1000
