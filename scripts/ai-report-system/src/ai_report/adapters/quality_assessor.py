"""Hermes Quality Assessor — 多维度质量评估器
遵循 Hermes Code Rules 规范

Re-exports:
    HermesQualityAssessor
    CheckItem, DimensionResult, AssessmentResult
    _CHECKLISTS, ChecklistID, ScoreValue
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..core.base import BaseComponent
from ..config import get_config
from ..core.exceptions import QualityError

from .checks import (
    _check_grammar,
    _check_readability,
    _check_structure,
    _check_style,
    _check_technical,
)
from .dimensions import (
    AssessmentResult,
    CheckItem,
    ChecklistID,
    DimensionResult,
    ScoreValue,
    _CHECKLISTS,
)

logger = logging.getLogger(__name__)

# Re-export symbols for backward compatibility
__all__ = [
    "HermesQualityAssessor",
    "CheckItem",
    "DimensionResult",
    "AssessmentResult",
    "_CHECKLISTS",
    "ChecklistID",
    "ScoreValue",
]


class HermesQualityAssessor(BaseComponent):
    """
    多维度质量评估器

    评估维度 (5维):
    - grammar: 语法和拼写 (权重 0.25)
    - structure: 结构和组织 (权重 0.25)
    - style: 风格和语调 (权重 0.15)
    - technical: 技术准确性 (权重 0.20)
    - readability: 可读性 (权重 0.15)

    特性:
    - 层次化检查清单（每个维度4-5个检查项）
    - 优先级加权评分（high/medium/low）
    - 可操作改进建议
    - 置信度评分
    - 质量等级（excellent → poor）
    """

    COMPONENT_NAME = "HermesQualityAssessor"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "多维度质量评估器，5维检查清单"

    # 维度权重
    DIMENSION_WEIGHTS: dict[str, float] = {
        "grammar": 0.25,
        "structure": 0.25,
        "style": 0.15,
        "technical": 0.20,
        "readability": 0.15,
    }

    def __init__(self, config: Any | None = None) -> None:
        super().__init__(config)

    def _initialize_internal(self) -> None:
        """初始化质量评估器"""
        cfg = self._config.report_config
        logger.info(
            "%s 初始化完成, %d个维度, %d个检查项",
            self.COMPONENT_NAME,
            len(_CHECKLISTS),
            sum(len(items) for items in _CHECKLISTS.values()),
        )

    # ── 公开接口 ──────────────────────────────────────────

    def assess(
        self,
        content: str,
        report_id: str | None = None,
        dimensions: list[str] | None = None,
    ) -> AssessmentResult:
        """
        评估内容质量

        Args:
            content: 待评估内容
            report_id: 报告标识
            dimensions: 要评估的维度列表，None为全部

        Returns:
            评估结果

        Raises:
            QualityError: 内容为空或过短时抛出
        """
        start_time = time.time()

        if not content or not content.strip():
            raise QualityError(
                message="评估内容不能为空",
                score=0.0,
            )

        if len(content) < 20:
            raise QualityError(
                message="内容过短（<20字符），无法评估",
                score=0.0,
            )

        dimensions = dimensions or list(self.DIMENSION_WEIGHTS.keys())
        valid_dims = [d for d in dimensions if d in _CHECKLISTS]

        if not valid_dims:
            raise QualityError(
                message=f"无有效评估维度: {dimensions}",
                failed_checks=dimensions,
            )

        # 逐维度评估
        dimension_results: dict[str, DimensionResult] = {}
        all_suggestions: list[str] = []

        for dim in valid_dims:
            dim_result = self._assess_dimension(dim, content)
            dimension_results[dim] = dim_result
            all_suggestions.extend(dim_result.issues[:3])

        # 计算加权总分
        overall_score = self._calculate_overall_score(dimension_results, valid_dims)

        # 置信度
        total_checks = sum(r.total for r in dimension_results.values())
        confidence = min(total_checks / 25.0, 1.0)  # 25个完整检查项=100%

        result = AssessmentResult(
            report_id=report_id or "unknown",
            content_preview=content[:200],
            overall_score=overall_score,
            dimension_scores=dimension_results,
            suggestions=self._deduplicate_suggestions(all_suggestions),
            confidence=confidence,
            assessment_time_ms=(time.time() - start_time) * 1000,
        )

        elapsed = (time.time() - start_time) * 1000
        self._record_performance(start_time, success=True)
        logger.info(
            "评估完成: score=%.4f, grade=%s, %d维, %.0fms",
            overall_score, result.quality_grade, len(valid_dims), elapsed,
        )

        return result

    def assess_dimension(
        self,
        dimension: str,
        content: str,
    ) -> DimensionResult:
        """
        评估单个维度

        Args:
            dimension: 维度名称
            content: 待评估内容

        Returns:
            维度评估结果
        """
        return self._assess_dimension(dimension, content)

    # ── 维度评估 ──────────────────────────────────────────

    def _assess_dimension(
        self,
        dimension: str,
        content: str,
    ) -> DimensionResult:
        """评估指定维度"""
        check_items = _CHECKLISTS.get(dimension, [])
        results: list[CheckItem] = []

        for item_name, item_desc, priority, weight in check_items:
            passed, detail = self._run_check(dimension, item_name, content)
            results.append(CheckItem(
                name=item_name,
                description=item_desc,
                priority=priority,
                weight=weight,
                passed=passed,
                detail=detail,
            ))

        # 计分
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        total = len(results)

        weighted_pass = sum(
            r.weight * r.priority_weight
            for r in results if r.passed
        )
        weighted_total = sum(
            r.weight * r.priority_weight
            for r in results
        ) or 1.0
        score = weighted_pass / weighted_total

        # 收集问题
        issues = [
            f"[{r.priority}] {r.name}: {r.detail or r.description}"
            for r in results if not r.passed
        ]

        return DimensionResult(
            dimension=dimension,
            score=score,
            items=results,
            passed=passed,
            failed=failed,
            total=total,
            issues=issues,
        )

    # ── 检查器 ────────────────────────────────────────────

    @staticmethod
    def _run_check(
        dimension: str,
        check_name: str,
        content: str,
    ) -> tuple[bool, str | None]:
        """运行单个检查"""
        if dimension == "grammar":
            return _check_grammar(content, check_name)
        elif dimension == "structure":
            return _check_structure(content, check_name)
        elif dimension == "style":
            return _check_style(content, check_name)
        elif dimension == "technical":
            return _check_technical(content, check_name)
        elif dimension == "readability":
            return _check_readability(content, check_name)
        return True, None

    # ── 总分计算 ──────────────────────────────────────────

    def _calculate_overall_score(
        self,
        dimension_results: dict[str, DimensionResult],
        dimensions: list[str],
    ) -> float:
        """计算加权总分"""
        total_weight = 0.0
        weighted_score = 0.0

        for dim in dimensions:
            weight = self.DIMENSION_WEIGHTS.get(dim, 0.0)
            result = dimension_results.get(dim)
            if result is not None:
                weighted_score += result.score * weight
                total_weight += weight

        return weighted_score / total_weight if total_weight > 0 else 0.0

    @staticmethod
    def _deduplicate_suggestions(issues: list[str]) -> list[str]:
        """去重并格式化建议"""
        seen: set[str] = set()
        unique: list[str] = []
        for issue in issues:
            if issue not in seen:
                seen.add(issue)
                unique.append(issue)
        return unique

    # ── 执行 ──

    def execute(self, operation: str = "assess", **kwargs: Any) -> Any:
        """执行评估操作"""
        operations = {
            "assess": self.assess,
            "assess_dimension": self.assess_dimension,
        }

        if operation not in operations:
            raise QualityError(f"未知操作: {operation}")

        return operations[operation](**kwargs)
