"""共享数据结构 — 知识点类型枚举、claims_count 规则修正、各阶段 TypedDict"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, NotRequired, TypedDict


# ========== 1. 知识点类型枚举 ==========


class KnowledgeType(str, Enum):
    """五类知识点。继承 str 以支持 JSON 值直接比较。"""

    PRINCIPLE = "principle"   # 原理: 因果/机制关系
    FORMULA = "formula"       # 公式: 可计算形式化表述
    KEY_POINT = "key_point"   # 要点: 事实/分类/结构
    CONCLUSION = "conclusion" # 结论: 有条件对比
    METHOD = "method"         # 方法/流程: 可复现步骤


KNOWLEDGE_TYPE_NAMES: frozenset[str] = frozenset(e.value for e in KnowledgeType)

KNOWLEDGE_TYPE_LABELS: dict[KnowledgeType, str] = {
    KnowledgeType.PRINCIPLE: "原理",
    KnowledgeType.FORMULA: "公式",
    KnowledgeType.KEY_POINT: "要点",
    KnowledgeType.CONCLUSION: "结论",
    KnowledgeType.METHOD: "方法/流程",
}


# ========== 2. claims_count 规则修正 ==========

# 连词正则: 且、并、同时、分别、一方面…另一方面、但是、然而
_CONJUNCTION_PATTERN: re.Pattern[str] = re.compile(
    r"且|并且|同时|分别|一方面|另一方面|但是|然而|而且"
)

# 中英文分号
_SEMICOLON_PATTERN: re.Pattern[str] = re.compile(r"[;；]")

# 因果动词（用于多主谓结构检测中的谓语标志）
_CAUSAL_VERBS: tuple[str, ...] = (
    "是", "有", "通过", "使", "使得", "导致", "基于", "依赖", "遵循",
    "实现", "包含", "提供", "支持", "需要", "使用", "采用", "应用",
    "优化", "改进", "提升", "降低", "覆盖", "处理", "计算", "分析",
)


def adjust_claims_count(text: str, llm_claimed: int) -> int:
    """规则级硬边界修正 claims_count。只向上调整，不降低 LLM 值。

    修正规则:
    1. 连词(且/并/同时/分别/但是/然而) → max(llm_claimed, 2)
    2. 分号(;/；) → max(llm_claimed, 分号数+1)
    3. 多独立主谓结构 → max(llm_claimed, 2)

    Args:
        text: 知识点文本
        llm_claimed: LLM 输出的 claims_count

    Returns:
        修正后的 claims_count（始终 >= llm_claimed）
    """
    if not text or not text.strip():
        return max(llm_claimed, 0)

    result = max(llm_claimed, 0)

    # 规则 1: 连词检测
    if _CONJUNCTION_PATTERN.search(text):
        result = max(result, 2)

    # 规则 2: 分号检测
    semicolons = _SEMICOLON_PATTERN.findall(text)
    if semicolons:
        result = max(result, len(semicolons) + 1)

    # 规则 3: 多独立主谓结构
    if _has_multiple_clauses(text):
        result = max(result, 2)

    return result


def _has_multiple_clauses(text: str) -> bool:
    """启发式检测多主谓结构。

    策略: 按逗号分割，统计含谓语动词标志的子句数。
    子句长度 > 6 字且含谓语动词标志词 → 计为独立子句。
    独立子句数 >= 2 → True。
    """
    parts = re.split(r"[,，]", text)
    independent_clauses = 0
    for part in parts:
        part = part.strip()
        if len(part) < 6:
            continue
        if any(verb in part for verb in _CAUSAL_VERBS):
            independent_clauses += 1
            if independent_clauses >= 2:
                return True
    return False


# ========== 3. 各阶段产物的 TypedDict ==========


class AdmittedFile(TypedDict):
    """Pre-phase 入列文件"""
    path: str
    title: str


class SkippedFile(TypedDict):
    """Pre-phase 跳过文件"""
    path: str
    reason: str


class ScanResult(TypedDict):
    """Pre-phase 扫描结果"""
    source_dir: str
    admitted_files: list[AdmittedFile]
    skipped: list[SkippedFile]
    empty_dir: bool


class Candidate(TypedDict):
    """阶段1 候选知识点"""
    text: str
    type: str               # KnowledgeType.value
    claims_count: int
    claim_list: list[str]   # 独立 claim 列表（可审计，由解析函数保证始终存在）


class AnalysisReport(TypedDict):
    """阶段1 分析产物"""
    article_title: str
    article_path: str       # 文章文件路径，用于 Phase 4 缓存 key
    analysis: dict[str, Any]   # {"content_summary": str, "empty_article": bool}
    candidates: list[Candidate]


class AtomicKnowledge(TypedDict):
    """阶段2 原子知识点"""
    text: str
    type: str           # KnowledgeType.value
    claims_count: int   # 始终 == 1
    source_candidate_index: int
    source_title: str    # 来源文章标题，Phase 4 按此过滤
    entities: list[str]  # 命名实体列表（入库时写入 kt_entity_links）
    valid_from: NotRequired[str | None]   # P3-9: 有效起始时间（ISO 格式）
    valid_until: NotRequired[str | None]  # P3-9: 有效截止时间（ISO 格式）


class ReviewItem(TypedDict):
    """审查队列条目"""
    type: str           # "incomplete_split" | "consistency_warning"
    text: str
    original_text: str
    original_claims_count: int
    reason: str


class SplitResult(TypedDict):
    """阶段2 拆解产物"""
    atomic_knowledge: list[AtomicKnowledge]
    review_queue_items: list[ReviewItem]
    stats: dict[str, int]  # {"total", "passed", "split", "dropped", "review"}
