"""质量评估维度定义"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ScoreValue = float
ChecklistID = str


@dataclass
class CheckItem:
    """检查项"""
    name: str
    description: str
    priority: str
    weight: float
    passed: bool = False
    detail: str | None = None

    @property
    def priority_weight(self) -> float:
        """优先级权重"""
        weights = {"high": 1.5, "medium": 1.0, "low": 0.5}
        return weights.get(self.priority, 1.0)


@dataclass
class DimensionResult:
    """维度评估结果"""
    dimension: str
    score: float
    items: list[CheckItem]
    passed: int
    failed: int
    total: int
    issues: list[str]


@dataclass
class AssessmentResult:
    """评估结果"""
    report_id: str
    content_preview: str
    overall_score: float
    dimension_scores: dict[str, DimensionResult]
    suggestions: list[str]
    confidence: float
    assessment_time_ms: float = 0.0
    quality_grade: str = ""

    def __post_init__(self) -> None:
        if not self.quality_grade:
            if self.overall_score >= 0.9:
                self.quality_grade = "excellent"
            elif self.overall_score >= 0.7:
                self.quality_grade = "good"
            elif self.overall_score >= 0.5:
                self.quality_grade = "fair"
            else:
                self.quality_grade = "poor"


# 检查清单定义
_CHECKLISTS: dict[str, list[tuple[str, str, str, float]]] = {
    "grammar": [
        ("spelling", "拼写检查", "high", 1.0),
        ("punctuation", "标点符号", "medium", 0.8),
        ("syntax", "语法结构", "high", 1.0),
        ("tense", "时态一致性", "medium", 0.7),
    ],
    "structure": [
        ("outline", "大纲结构", "high", 1.0),
        ("transitions", "段落过渡", "medium", 0.8),
        ("headings", "标题层级", "medium", 0.7),
        ("conclusion", "结论完整性", "high", 1.0),
    ],
    "style": [
        ("tone", "语气一致性", "medium", 0.8),
        ("formality", "正式程度", "low", 0.5),
        ("clarity", "表达清晰度", "high", 1.0),
    ],
    "technical": [
        ("accuracy", "技术准确性", "high", 1.0),
        ("terminology", "术语使用", "medium", 0.8),
        ("references", "引用规范", "medium", 0.7),
    ],
    "readability": [
        ("sentence_length", "句子长度", "medium", 0.8),
        ("vocabulary", "词汇难度", "low", 0.5),
        ("paragraph_length", "段落长度", "medium", 0.7),
    ],
}
