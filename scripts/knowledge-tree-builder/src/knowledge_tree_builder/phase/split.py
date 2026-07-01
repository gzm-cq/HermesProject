"""阶段2: 质量评估 + 拆解 — 原子性检查 + 规则修正 + sum 校验 + 自解释"""

from __future__ import annotations

import logging
import re
from typing import Any

from knowledge_tree_builder.config import AppConfig
from knowledge_tree_builder.llm.client import call_llm_json
from knowledge_tree_builder.models import (
    AnalysisReport,
    AtomicKnowledge,
    Candidate,
    KNOWLEDGE_TYPE_NAMES,
    ReviewItem,
    SplitResult,
    adjust_claims_count,
)

logger = logging.getLogger(__name__)


# ========== 自解释检查 ==========

# 指代前文的代词模式
_PRONOUN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"该(模型|方法|算法|系统|框架|机制|技术|方案|架构|策略|协议|工具|模块|组件|流程)"),
    re.compile(r"这(种|个|类|些)(模型|方法|算法|系统|框架|机制|技术|方案|架构|策略|协议|工具|模块|组件|流程)"),
    re.compile(r"上述(模型|方法|算法|系统|框架|机制|技术|方案|架构|策略|结果|数据)"),
    re.compile(r"前述"),
    re.compile(r"上文(提到|所述|介绍)"),
    re.compile(r"前文(提到|所述|介绍)"),
]

# 元引用模式
_META_REFERENCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"如上所述"),
    re.compile(r"如下所示"),
    re.compile(r"下文详述"),
    re.compile(r"前文提到"),
    re.compile(r"如前所述"),
    re.compile(r"本文(介绍|讨论|分析|提出|概述|总结)"),
    re.compile(r"文章(介绍|讨论|分析|提出|概述|总结)"),
    re.compile(r"本研究(介绍|讨论|分析|提出|概述|总结)"),
]

# 域内通用缩写白名单
_DOMAIN_ABBREVIATIONS: frozenset[str] = frozenset({
    # 聚类/ML
    "HDBSCAN", "DBSCAN", "KNN", "SVM", "AUC", "F1", "ROC", "PCA",
    "CNN", "RNN", "LSTM", "GRU", "GAN", "VAE", "RL", "DL", "ML", "NLP", "CV",
    # Transformer
    "GPT", "BERT", "T5", "LLM", "RAG", "LLMs",
    # 数学/算法
    "ANN", "HNSW", "BM25", "TF", "IDF", "SGD", "Adam",
    "ReLU", "GELU", "softmax", "cosine",
    # 工程
    "API", "CLI", "JSON", "YAML", "HTTP", "HTTPS", "REST", "SQL", "PG",
    "GPU", "CPU", "TPU", "SDK", "IDE", "CI", "CD",
    # 向量/embedding
    "embedding", "token", "tokens",
    # 注意力机制
    "Q", "K", "V",
})


def check_self_explanatory(text: str) -> tuple[bool, str]:
    """自解释检查（两项规则）。

    已移除缩写白名单检查——缩写是否通用应由纠错回路判断，
    而非在提取阶段因缩写未知而丢弃有价值的知识。

    Returns:
        (is_self_explanatory, failure_reason)
    """
    reason = _check_pronouns(text)
    if reason:
        return False, f"pronoun: {reason}"

    reason = _check_meta_references(text)
    if reason:
        return False, f"meta_ref: {reason}"

    return True, ""


def _check_pronouns(text: str) -> str | None:
    """检查指代代词。返回命中模式或 None。"""
    for pattern in _PRONOUN_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def _check_meta_references(text: str) -> str | None:
    """检查元引用。返回命中模式或 None。"""
    for pattern in _META_REFERENCE_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def _check_undefined_abbreviations(text: str) -> str | None:
    """检查非域内通用缩写。

    检测大写字母序列(2+)，不在白名单中标为可疑。
    不确定时返回 None（通过，宁过勿拒）。
    """
    # 匹配 2+ 大写字母的缩写（如 SE、KT）
    abbrevs = re.findall(r"\b([A-Z]{2,})\b", text)
    for abbr in abbrevs:
        if abbr not in _DOMAIN_ABBREVIATIONS and abbr.upper() not in _DOMAIN_ABBREVIATIONS:
            return abbr
    return None


# ========== 格式合法性检查 ==========

# 各类型的格式检测关键词
_PRINCIPLE_VERBS: tuple[str, ...] = (
    "因为", "通过", "使", "使得", "遵循", "基于", "依赖于",
    "导致", "从而", "因此", "所以", "实现", "产生", "带来",
)

_FORMULA_SIGNALS: tuple[str, ...] = (
    "=", "≈", "≡", "∝", "∑", "Σ", "∫",
    "softmax", "cosine", "log", "exp", "sqrt", "sigmoid",
    "dot(", "norm(", "sum(",
)

_CONCLUSION_SIGNALS: tuple[str, ...] = (
    "优于", "高于", "低于", "比", "更好", "更差", "更有效",
    "提升", "降低", "验证", "发现", "条件", "在", "下",
    "超过", "不及", "相比",
)

_METHOD_SIGNALS: tuple[str, ...] = (
    "步骤", "首先", "其次", "最后", "分", "步", "→", "->",
    "应该", "必须", "需要", "建议", "执行", "运行", "配置",
    "部署", "流程", "操作", "阶段",
)


def _check_format_valid(text: str, knowledge_type: str) -> tuple[bool, str]:
    """各类型的合法格式检查。

    Returns:
        (is_valid, failure_reason)
    """
    if knowledge_type == "principle":
        if not any(v in text for v in _PRINCIPLE_VERBS):
            return False, "原理缺少因果/机制动词"
        return True, ""

    if knowledge_type == "formula":
        if not any(s in text for s in _FORMULA_SIGNALS):
            return False, "公式缺少数学符号或函数名"
        return True, ""

    if knowledge_type == "key_point":
        if len(text) < 10:
            return False, "要点长度不足 10 字"
        return True, ""

    if knowledge_type == "conclusion":
        if not any(s in text for s in _CONCLUSION_SIGNALS):
            return False, "结论缺少条件/比较词"
        return True, ""

    if knowledge_type == "method":
        if not any(s in text for s in _METHOD_SIGNALS):
            return False, "方法/流程缺少步骤标志或规范词"
        return True, ""

    return True, ""


# ========== 类型匹配检查 ==========


def _check_type_match(text: str, claimed_type: str) -> str:
    """类型匹配检查。可能返回修正后的类型。"""
    if claimed_type == "principle":
        # 标为原理但无因果动词 → 更像要点
        if not any(v in text for v in _PRINCIPLE_VERBS):
            return "key_point"

    if claimed_type == "conclusion":
        # 标为结论但无条件/比较词 → 更像要点
        if not any(s in text for s in _CONCLUSION_SIGNALS):
            return "key_point"

    if claimed_type == "formula":
        # 标为公式但无数学符号 → 更像要点
        if not any(s in text for s in _FORMULA_SIGNALS):
            return "key_point"

    return claimed_type


# ========== 拆解 Prompt ==========

_SPLIT_PROMPT_TEMPLATE: str = """请将以下知识点拆分为多条独立的原子知识点。

原文本: "{text}"
标注类型: {knowledge_type}
预估 claims_count: {claims_count}

要求:
1. 每条拆分后的知识点必须独立可理解
2. 每条的 claims_count 应为 1
3. 每条标注正确的类型（principle/formula/key_point/conclusion/method）

输出 JSON:
```json
{{
  "split_items": [
    {{"text": "拆分后的知识点", "type": "principle", "claims_count": 1}},
    {{"text": "拆分后的知识点", "type": "key_point", "claims_count": 1}}
  ]
}}
```"""


# ========== 主函数 ==========


def process_candidates(
    report: AnalysisReport,
    *,
    config: AppConfig,
) -> SplitResult:
    """阶段2: 原子性检查 + 拆解。

    对每个 candidate:
    1. adjust_claims_count 规则修正
    2. claims_count == 1 → quality_evaluate → 通过则加入 atomic
    3. claims_count > 1 → _split_candidate（上限 split_max_rounds 轮）

    Args:
        report: 阶段1 AnalysisReport
        config: AppConfig

    Returns:
        SplitResult
    """
    atomic_list: list[AtomicKnowledge] = []
    review_items: list[ReviewItem] = []
    stats = {"total": 0, "passed": 0, "split": 0, "dropped": 0, "review": 0}

    for idx, candidate in enumerate(report.get("candidates", [])):
        stats["total"] += 1
        atomics, reviews, was_split = _evaluate_single_candidate(
            candidate, config=config, source_index=idx,
            article_title=report["article_title"],
        )
        if atomics:
            atomic_list.extend(atomics)
            if was_split:
                stats["split"] += len(atomics)
            else:
                stats["passed"] += len(atomics)
        else:
            stats["dropped"] += 1
        if reviews:
            review_items.extend(reviews)
            stats["review"] += len(reviews)

    return SplitResult(
        atomic_knowledge=atomic_list,
        review_queue_items=review_items,
        stats=stats,
    )


def _evaluate_single_candidate(
    candidate: Candidate,
    *,
    config: AppConfig,
    source_index: int,
    article_title: str = "",
) -> tuple[list[AtomicKnowledge], list[ReviewItem], bool]:
    """评估单条候选 → (atomic_list, review_items, was_split)。"""
    text = candidate["text"]
    claimed_type = candidate["type"]
    llm_claims = candidate.get("claims_count", 1)

    # 规则修正
    adjusted_count = adjust_claims_count(text, llm_claims)

    if adjusted_count <= 1:
        # 原子 → 质量评估
        passed, corrected_type, reason = _quality_evaluate(
            text, claimed_type,
            self_explanatory_enabled=config.self_explanatory_rules,
        )
        if passed:
            ak = AtomicKnowledge(
                text=text,
                type=corrected_type,
                claims_count=1,
                source_candidate_index=source_index,
                source_title=article_title,
            )
            # P3-9: 时态信息提取（启发式 fallback）
            if config.enable_temporal_extraction:
                from knowledge_tree_builder.core.temporal import extract_temporal_from_text
                tr = extract_temporal_from_text(text)
                ak["valid_from"] = tr.valid_from
                ak["valid_until"] = tr.valid_until
            return [ak], [], False
        else:
            logger.debug("候选 #%d 质量不通过: %s", source_index, reason)
            return [], [], False

    # 非原子 → 拆解
    atomics, reviews = _split_candidate(
        text=text,
        knowledge_type=claimed_type,
        claims_count=adjusted_count,
        config=config,
        max_rounds=config.split_max_rounds,
        source_index=source_index,
        article_title=article_title,
    )
    return atomics, reviews, True


def _split_candidate(
    text: str,
    knowledge_type: str,
    claims_count: int,
    *,
    config: AppConfig,
    max_rounds: int,
    source_index: int,
    article_title: str = "",
) -> tuple[list[AtomicKnowledge], list[ReviewItem]]:
    """LLM 拆解非原子候选。"""
    atomic_results: list[AtomicKnowledge] = []
    review_items: list[ReviewItem] = []

    remaining = [(text, knowledge_type, claims_count)]

    for round_num in range(max_rounds):
        if not remaining:
            break

        next_remaining: list[tuple[str, str, int]] = []

        for item_text, item_type, item_claims in remaining:
            prompt = _SPLIT_PROMPT_TEMPLATE.format(
                text=item_text,
                knowledge_type=item_type,
                claims_count=item_claims,
            )

            response = call_llm_json(
                prompt=prompt,
                system_prompt="你是知识点拆解专家。请将非原子知识点拆分为原子知识点。",
                temperature=0.0,
                api_url=config.llm_api_url,
                api_key=config.llm_api_key,
                model=config.llm_model,
            )

            if "error" in response:
                logger.warning("LLM 拆解失败 (round %d): %s", round_num, response["error"])
                # 拆解失败，残余入 review
                review_items.append(ReviewItem(
                    type="incomplete_split",
                    text=item_text,
                    original_text=text,
                    original_claims_count=claims_count,
                    reason=f"LLM 拆解失败 (round {round_num}): {response['error']}",
                ))
                continue

            split_items = response.get("split_items", [])
            if not isinstance(split_items, list) or not split_items:
                review_items.append(ReviewItem(
                    type="incomplete_split",
                    text=item_text,
                    original_text=text,
                    original_claims_count=claims_count,
                    reason=f"LLM 返回空 split_items (round {round_num})",
                ))
                continue

            # sum 校验（先于 _process_sub_items，确保能检测到 sum 不匹配）
            if not _sum_check(item_claims, split_items):
                logger.warning(
                    "sum 校验失败 (round %d): 原 %d, 子 sum %d → 重新分析",
                    round_num,
                    item_claims,
                    sum(si.get("claims_count", 1) for si in split_items),
                )
                # 收集非原子子条目用于重试
                non_atomic_for_resplit: list[tuple[str, str, int]] = []
                for si in split_items:
                    si_text = str(si.get("text", "")).strip()
                    si_type = str(si.get("type", item_type)).strip()
                    si_claims = si.get("claims_count", 1)
                    if not isinstance(si_claims, int) or si_claims < 1:
                        si_claims = 1
                    if si_text and len(si_text) >= 10 and si_claims > 1:
                        if si_type not in KNOWLEDGE_TYPE_NAMES:
                            si_type = item_type
                        non_atomic_for_resplit.append((si_text, si_type, si_claims))

                if non_atomic_for_resplit:
                    non_atomic_expected_sum = sum(c for _, _, c in non_atomic_for_resplit)
                    retry_passed, retry_non_atomic, consistency_warnings = _resplit_failed_items(
                        non_atomic_for_resplit, non_atomic_expected_sum, config, source_index,
                    )
                    review_items.extend(consistency_warnings)
                    # 替换 split_items：保留原有原子条目 + resplit 结果
                    new_split_items: list[dict[str, Any]] = []
                    for si in split_items:
                        si_c = si.get("claims_count", 1)
                        if not isinstance(si_c, int) or si_c < 1:
                            si_c = 1
                        if si_c <= 1:
                            new_split_items.append(si)
                    for ak in retry_passed:
                        new_split_items.append({"text": ak["text"], "type": ak["type"], "claims_count": 1})
                    for t, tp, c in retry_non_atomic:
                        new_split_items.append({"text": t, "type": tp, "claims_count": c})
                    split_items = new_split_items
                else:
                    # sum 不匹配但所有子条目都是原子 → 无法通过 resplit 修复
                    # 用规则修正覆盖每个子条目的 claims_count
                    rule_sum = 0
                    for si in split_items:
                        si_text = str(si.get("text", "")).strip()
                        si_llm = si.get("claims_count", 1)
                        if not isinstance(si_llm, int) or si_llm < 1:
                            si_llm = 1
                        si["claims_count"] = adjust_claims_count(si_text, si_llm)
                        rule_sum += si["claims_count"]
                    if rule_sum != item_claims:
                        logger.warning(
                            "sum 校验失败且无非原子条目可重试: 原 %d, 规则 sum %d → consistency_warning",
                            item_claims, rule_sum,
                        )
                        for si in split_items:
                            si_text = str(si.get("text", "")).strip()
                            if si_text:
                                review_items.append(ReviewItem(
                                    type="consistency_warning",
                                    text=si_text,
                                    original_text=text,
                                    original_claims_count=item_claims,
                                    reason=f"sum 校验不一致: 原值={item_claims}, 规则修正后 sum={rule_sum}",
                                ))

            # 处理拆出的子条目（或 resplit 后的结果）
            passed_items, non_atomic_items = _process_sub_items(
                split_items, item_type, source_index, config,
                article_title=article_title,
            )
            atomic_results.extend(passed_items)

            # 下一轮处理仍非原子的条目
            next_remaining.extend(non_atomic_items)

        remaining = next_remaining

    # max_rounds 后仍有残余 → review_queue
    for item_text, item_type, item_claims in remaining:
        review_items.append(ReviewItem(
            type="incomplete_split",
            text=item_text,
            original_text=text,
            original_claims_count=claims_count,
            reason=f"{max_rounds} 轮拆解后仍非原子 (claims_count={item_claims})",
        ))

    return atomic_results, review_items


def _process_sub_items(
    split_items: list[dict[str, Any]],
    parent_type: str,
    source_index: int,
    config: AppConfig,
    article_title: str = "",
) -> tuple[list[AtomicKnowledge], list[tuple[str, str, int]]]:
    """处理 LLM 拆解的子条目。

    Returns:
        (passed_atomics, non_atomic_remaining)
    """
    atomics: list[AtomicKnowledge] = []
    remaining: list[tuple[str, str, int]] = []

    for si in split_items:
        si_text = str(si.get("text", "")).strip()
        si_type = str(si.get("type", parent_type)).strip()
        si_llm = si.get("claims_count", 1)
        if not isinstance(si_llm, int) or si_llm < 1:
            si_llm = 1
        # 规则修正：确保子条目也经过 adjust_claims_count
        si_claims = adjust_claims_count(si_text, si_llm)

        if not si_text or len(si_text) < 10:
            continue

        if si_type not in KNOWLEDGE_TYPE_NAMES:
            si_type = parent_type

        if si_claims <= 1:
            # 原子 → 质量评估
            passed, corrected_type, reason = _quality_evaluate(
                si_text, si_type,
                self_explanatory_enabled=config.self_explanatory_rules,
            )
            if passed:
                ak = AtomicKnowledge(
                    text=si_text,
                    type=corrected_type,
                    claims_count=1,
                    source_candidate_index=source_index,
                    source_title=article_title,
                )
                # P3-9: 时态信息提取（启发式 fallback）
                if config.enable_temporal_extraction:
                    from knowledge_tree_builder.core.temporal import extract_temporal_from_text
                    tr = extract_temporal_from_text(si_text)
                    ak["valid_from"] = tr.valid_from
                    ak["valid_until"] = tr.valid_until
                atomics.append(ak)
            else:
                logger.debug("拆出子条目质量不通过: %s", reason)
        else:
            # 仍非原子
            remaining.append((si_text, si_type, si_claims))

    return atomics, remaining


def _resplit_failed_items(
    non_atomic_items: list[tuple[str, str, int]],
    expected_sum: int,
    config: AppConfig,
    source_index: int,
) -> tuple[list[AtomicKnowledge], list[tuple[str, str, int]], list[ReviewItem]]:
    """sum 校验失败后重新分析（1 次机会）。

    处理链路:
    1. 对仍非原子的子条目重试 LLM 拆解
    2. 如果重试后 sum 仍不匹配 → 用规则修正值覆盖 claims_count
    3. 规则值仍不匹配 → 写入 review_queue (consistency_warning)

    Args:
        non_atomic_items: 仍非原子的子条目列表 (text, type, claims_count)
        expected_sum: 非原子子条目的预期 claims 之和（不是父候选总值）
        config: AppConfig
        source_index: 源候选索引

    Returns:
        (new_atomics, still_non_atomic, consistency_warnings)
    """
    all_retry_split_items: list[tuple[dict[str, Any], str]] = []  # (item, parent_type)
    consistency_warnings: list[ReviewItem] = []

    for item_text, item_type, item_claims in non_atomic_items:
        prompt = _SPLIT_PROMPT_TEMPLATE.format(
            text=item_text,
            knowledge_type=item_type,
            claims_count=item_claims,
        )
        response = call_llm_json(
            prompt=prompt,
            system_prompt="你是知识点拆解专家。请将非原子知识点拆分为原子知识点。",
            temperature=0.0,
            api_url=config.llm_api_url,
            api_key=config.llm_api_key,
            model=config.llm_model,
        )

        if "error" in response:
            logger.warning("重新分析 LLM 拆解失败: %s", response["error"])
            all_retry_split_items.append(
                ({"text": item_text, "type": item_type, "claims_count": item_claims}, item_type)
            )
            continue

        retry_items = response.get("split_items", [])
        if not isinstance(retry_items, list) or not retry_items:
            all_retry_split_items.append(
                ({"text": item_text, "type": item_type, "claims_count": item_claims}, item_type)
            )
            continue

        for ri in retry_items:
            all_retry_split_items.append((ri, item_type))

    # 二次 sum 校验
    retry_sum = sum(
        si.get("claims_count", 1) if isinstance(si.get("claims_count"), int) else 1
        for si, _ in all_retry_split_items
    )

    if retry_sum != expected_sum:
        # 用规则修正值覆盖 LLM claims_count
        for si, _ in all_retry_split_items:
            si_text = str(si.get("text", "")).strip()
            si_llm_claims = si.get("claims_count", 1)
            if not isinstance(si_llm_claims, int) or si_llm_claims < 1:
                si_llm_claims = 1
            si["claims_count"] = adjust_claims_count(si_text, si_llm_claims)

        # 三次校验
        rule_sum = sum(
            si.get("claims_count", 1) if isinstance(si.get("claims_count"), int) else 1
            for si, _ in all_retry_split_items
        )
        if rule_sum != expected_sum:
            logger.warning(
                "规则修正后 sum 仍不匹配: 预期 %d, 规则 sum %d → consistency_warning",
                expected_sum, rule_sum,
            )
            # 标记一致性警告，但不丢弃
            for si, _ in all_retry_split_items:
                si_text = str(si.get("text", "")).strip()
                if si_text:
                    consistency_warnings.append(ReviewItem(
                        type="consistency_warning",
                        text=si_text,
                        original_text="",
                        original_claims_count=expected_sum,
                        reason=f"sum 校验不一致: 预期={expected_sum}, 规则修正后 sum={rule_sum}",
                    ))

    # 处理重试后的子条目（按每个 item 的 parent_type 分组处理）
    all_passed: list[AtomicKnowledge] = []
    all_still_non_atomic: list[tuple[str, str, int]] = []
    for si, parent_type in all_retry_split_items:
        passed, still_na = _process_sub_items(
            [si], parent_type, source_index, config,
        )
        all_passed.extend(passed)
        all_still_non_atomic.extend(still_na)

    return all_passed, all_still_non_atomic, consistency_warnings


def _sum_check(original_count: int, sub_items: list[dict[str, Any]]) -> bool:
    """sum 校验: sum(子条目 claims_count) == 原值。"""
    sub_sum = sum(
        si.get("claims_count", 1) if isinstance(si.get("claims_count"), int) else 1
        for si in sub_items
    )
    return sub_sum == original_count


def _quality_evaluate(
    text: str,
    knowledge_type: str,
    *,
    self_explanatory_enabled: bool,
) -> tuple[bool, str, str]:
    """原子候选的质量评估（四项检查）。

    Returns:
        (passed, corrected_type, failure_reason)
    """
    # 1. 格式合法性
    format_ok, format_reason = _check_format_valid(text, knowledge_type)
    if not format_ok:
        return False, knowledge_type, format_reason

    # 2. 类型匹配（可能修正）
    corrected_type = _check_type_match(text, knowledge_type)

    # 3. 自解释检查
    if self_explanatory_enabled:
        se_ok, se_reason = check_self_explanatory(text)
        if not se_ok:
            return False, corrected_type, se_reason

    return True, corrected_type, ""
