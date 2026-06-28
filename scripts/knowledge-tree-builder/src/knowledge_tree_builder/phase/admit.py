"""阶段3: 准入 + 去重 + 矛盾检测

职责: 规则兜底拦截 + 两段式去重 + 条件化矛盾检测。

与 `core/incremental.py` 的关系:
- `incremental.py` 的 `dedup_before_insert` / `detect_conflict` 是旧版单阈值实现
- 本模块是升级版：两段阈值（0.95 直接判重 + 0.90~0.95 LLM 确认）+ 条件化矛盾比较
- 旧版保留供兼容调用，新版入口统一走本模块
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from knowledge_tree_builder.models import (
    AtomicKnowledge,
    KNOWLEDGE_TYPE_NAMES,
    ReviewItem,
)

logger = logging.getLogger(__name__)


# ========== 常量 ==========

_META_PREFIX_PATTERN: re.Pattern[str] = re.compile(
    r"^(本文|文章|本研究|本篇|该文章|该研究|综述|文章概述)"
    r"(介绍了|讨论了|分析了|探讨了|概述了|总结了|描述了|阐述了)"
)

_EXTRACTION_FAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"无法提取"),
    re.compile(r"已被删除"),
    re.compile(r"无法读取"),
    re.compile(r"请提供"),
]

_DOMAIN_ABBREVIATIONS: frozenset[str] = frozenset({
    "HDBSCAN", "DBSCAN", "KNN", "SVM", "CNN", "RNN", "LSTM",
    "GPT", "BERT", "LLM", "RAG", "API", "CLI", "JSON", "YAML",
    "SQL", "PG", "ANN", "HNSW", "BM25", "PCA",
})

# 建议/意见拦截 — 非知识事实，不应入知识树
_SUGGESTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^建议[：:]?"),       # "建议..." / "建议：..."
    re.compile(r"^改进建议"),         # "改进建议N..."
    re.compile(r"^方案"),             # "方案定稿..." / "方案代码..."
    re.compile(r"^[Nn]ote[：:]"),     # "Note:" / "note："
    re.compile(r"^TODO[：:]"),        # "TODO: ..."
    re.compile(r"^FIXME[：:]"),       # "FIXME: ..."
    re.compile(r"^[\\\(（]待定[\\\)）]"),  # "(待定)" / "（待定）"
    re.compile(r"^注意[：:]"),        # "注意：" / "注意:"
    re.compile(r"^说明[：:]"),        # "说明：" / "说明:"
    re.compile(r"^备注[：:]"),        # "备注：" / "备注:"
    re.compile(r"^示例[：:]"),        # "示例：" / "示例:"
]

# 配置/命令拦截 — 部署命令、端口、路径等非知识
_CONFIG_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"localhost:[0-9]+|127\.0\.0\.1:[0-9]+"),
    re.compile(r"^部署命令"),
    re.compile(r"^健康检查命令"),
    re.compile(r"curl -s.*localhost"),
]

# 矛盾检测否定关键词
_CONFLICT_NEGATION_WORDS: tuple[str, ...] = (
    "不", "不是", "不能", "无效", "不再", "相反", "错误", "不要",
    "而非", "并非", "无需",
)


# ========== 1. 兜底拦截 ==========


def _guard_filter(atomic: AtomicKnowledge) -> tuple[bool, str]:
    """规则兜底拦截。返回 (passed, reason)。

    规则:
    1. 类型不在五类中 → 丢弃（防御性）
    2. length < 10 → 丢弃
    3. 提取失败信号 → 丢弃
    4. 元信息开头 → 丢弃
    """
    text = atomic["text"]
    ktype = atomic["type"]

    if ktype not in KNOWLEDGE_TYPE_NAMES:
        return False, f"未知类型: {ktype}"

    if len(text) < 10:
        return False, f"长度不足: {len(text)}"

    for pat in _EXTRACTION_FAIL_PATTERNS:
        if pat.search(text):
            return False, f"提取失败信号: {pat.pattern}"

    if _META_PREFIX_PATTERN.match(text):
        m = _META_PREFIX_PATTERN.match(text)
        return False, f"元信息开头: {m.group(0) if m else 'unknown'}"

    for pat in _SUGGESTION_PATTERNS:
        if pat.match(text.strip()):
            return False, f"建议/意见类型: {pat.pattern}"

    for pat in _CONFIG_PATTERNS:
        if pat.search(text):
            return False, f"配置/命令类型: {pat.pattern}"

    return True, ""


# ========== 1.5 低质量模式检测 ==========


_LOW_QUALITY_VAGUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"很重要"),
    re.compile(r"很关键"),
    re.compile(r"很有用"),
    re.compile(r"非常重要"),
    re.compile(r"非常关键"),
    re.compile(r"非常有用"),
    re.compile(r"十分重要"),
    re.compile(r"十分关键"),
]


def _detect_low_quality(atomic: AtomicKnowledge) -> tuple[bool, str]:
    """检测低质量知识点。返回 (is_low_quality, reason)。

    检测规则:
    1. 纯代码片段无解释（代码占比 > 80% 且无自然语言解释）
    2. 只有标题无实质内容（长度 < 20 字且是名词短语）
    3. 重复句式（同一短语重复 > 3 次）
    4. 过度抽象（"很重要/很关键/很有用"等无实质内容）

    注意：检测到低质量只标记不丢弃，放入审查队列。
    """
    text = atomic["text"]
    text_len = len(text)

    code_chars = len(re.findall(r"[a-zA-Z0-9{}()\[\];.<>=+\-*/&|!@#$%^_\\]", text))
    code_ratio = code_chars / max(text_len, 1)
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    if code_ratio > 0.8 and chinese_chars < 10:
        return True, f"纯代码片段无解释 (代码占比 {code_ratio:.0%})"

    if text_len < 20:
        has_predicate = bool(re.search(
            r"[是有在为将能会可以应该需要必须通过使让把被给从到向往由于因为所以但是而且并且或者如果虽然即使无论不管只要除非以及还是也都又再还就才已经正在将要曾经一直总是经常偶尔突然渐渐终于最终原来其实当然显然自然确实真正最更加比较非常十分特别极其相当]",
            text,
        ))
        has_punctuation = bool(re.search(r"[，。！？；：、,.!?;:]", text))
        if not has_predicate and not has_punctuation:
            return True, f"只有标题无实质内容 (长度 {text_len} 字)"

    chinese_text = re.findall(r"[\u4e00-\u9fff]", text)
    if len(chinese_text) >= 4:
        from collections import Counter
        two_char_phrases = []
        for i in range(len(chinese_text) - 1):
            two_char_phrases.append(chinese_text[i] + chinese_text[i + 1])
        counter = Counter(two_char_phrases)
        most_common_phrase, most_common_count = counter.most_common(1)[0]
        if most_common_count > 3:
            return True, f"重复句式 (\"{most_common_phrase}\" 重复 {most_common_count} 次)"

    for pat in _LOW_QUALITY_VAGUE_PATTERNS:
        if pat.search(text):
            has_concrete = bool(re.search(r"[0-9%％]|[\u4e00-\u9fff]{4,}", text))
            if not has_concrete or len(text) < 25:
                return True, f"过度抽象 ({pat.pattern})"

    return False, ""


# ========== 1.6 白名单匹配 ==========


def _is_whitelisted(atomic: AtomicKnowledge, whitelist_sources: list[str]) -> bool:
    """检查知识点来源是否在白名单中。

    匹配逻辑：文章标题或 source_title 中包含白名单关键词。
    """
    if not whitelist_sources:
        return False

    source_title = atomic.get("source_title", "")
    entities = atomic.get("entities", [])
    combined_text = source_title
    if entities:
        combined_text += " " + " ".join(entities)

    for keyword in whitelist_sources:
        if keyword and keyword in combined_text:
            return True
    return False


# ========== 2. 两段式去重 ==========


def _dedup_single(
    text: str,
    existing_vectors: list[dict[str, Any]],
    embed_fn: Callable[[list[str]], list[list[float]] | None],
    cosine_sim_fn: Callable[[list[float], list[float]], float],
    threshold_direct: float = 0.95,
    threshold_llm: float = 0.90,
    llm_judge_fn: Callable[[str, str], bool] | None = None,
    db_adapter: Any = None,
) -> tuple[bool, str | None, float]:
    """两段式去重检查。

    优先使用 pgvector 近邻搜索（如果 db_adapter 可用），否则走内存扫描。

    Args:
        text: 新知识点文本
        existing_vectors: 已有知识列表 [{id, name, k_vector}]（内存扫描用）
        embed_fn: embedding 函数
        cosine_sim_fn: 余弦相似度函数
        threshold_direct: 直接判重阈值 (0.95)
        threshold_llm: LLM 确认区间下界 (0.90)
        llm_judge_fn: LLM 确认函数 (new_text, existing_text) → bool (是否等价)
        db_adapter: DatabaseAdapter 实例，提供 pgvector 近邻搜索

    Returns:
        (is_dup, matched_id, max_similarity)
    """
    new_emb = embed_fn([text])
    if not new_emb or new_emb[0] is None:
        return False, None, 0.0

    new_vec = new_emb[0]

    # 优先走 pgvector 近邻搜索（仅查库中已有向量，快速判重）
    if db_adapter is not None:
        try:
            neighbors = db_adapter.find_nearest_neighbors(
                new_vec,
                threshold=threshold_direct,  # 只找 >= threshold_direct 的，直接判重用
                limit=5,
            )
            if neighbors:
                # 有 >= threshold_direct 的近邻，直接判重
                top = neighbors[0]
                return True, str(top.get("id")), top.get("similarity", 0.0)
            # 没有直接命中的，继续走内存扫描（检查批次内去重 + 灰区 LLM 确认）
        except Exception as e:
            logger.debug("pgvector 去重失败，降级为内存扫描: %s", e)

    # 内存扫描：检查库中向量（灰区）+ 批次内向量（全量）
    for existing in existing_vectors:
        k_vec = existing.get("k_vector")
        if k_vec is None:
            continue

        sim = cosine_sim_fn(new_vec, k_vec)

        if sim > threshold_direct:
            return True, existing.get("id"), sim

        if sim >= threshold_llm and llm_judge_fn is not None:
            existing_text = existing.get("name", "")
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(llm_judge_fn, text, existing_text)
                    if future.result(timeout=30):
                        return True, existing.get("id"), sim
            except FuturesTimeout:
                logger.warning("LLM 去重判断超时，跳过本轮")
                continue
            except Exception as e:
                logger.warning("LLM 去重判断异常: %s", e)
                continue

    return False, None, 0.0


def _default_llm_judge(new_text: str, existing_text: str) -> bool:
    """默认 LLM 判断函数（占位，实际由调用时注入）。"""
    # 简化：文本长度差 < 5 且 70% 字符重叠 → 判重
    overlap = len(set(new_text) & set(existing_text)) / max(len(set(new_text) | set(existing_text)), 1)
    return overlap > 0.7


def _add_to_batch_pool(
    atomic: AtomicKnowledge,
    pool: list[dict[str, Any]],
    embed_fn: Callable[[list[str]], list[list[float]] | None],
) -> None:
    """将已通过的 atomic 加入批次内对比池（用于跨文章去重）。"""
    emb = embed_fn([atomic["text"]])
    if emb:
        pool.append({"id": f"batch_{len(pool)}", "name": atomic["text"], "k_vector": emb[0]})


# ========== 3. 条件化矛盾检测 ==========


_CONDITION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"如果([^，。]{2,40})(则|那么)"),
    re.compile(r"假设([^，。]{2,40})(则|那么)"),
    re.compile(r"假如([^，。]{2,40})(则|那么)"),
    re.compile(r"倘若([^，。]{2,40})(则|那么)"),
    re.compile(r"当([^，。]{2,40})(时|情况下)"),
    re.compile(r"在([^，。]{2,40})(环境|条件|前提|基础)下"),
    re.compile(r"在([^，。]{2,40})(上|下|时|后|中)"),
    re.compile(r"对于([^，。]{2,40})(来说|而言)"),
    re.compile(r"基于([^，。]{2,40})"),
    re.compile(r"鉴于([^，。]{2,40})"),
    re.compile(r"由于([^，。]{2,40})"),
    re.compile(r"因为([^，。]{2,40})"),
]


def _extract_condition(text: str) -> str:
    """从知识点文本中提取条件部分。

    策略:
    - 按顺序匹配多种条件模式
    - 返回第一个匹配到的条件短语
    - 如果找不到 → 返回空字符串
    """
    for pat in _CONDITION_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return ""


def _conditions_are_same(cond_a: str, cond_b: str) -> bool:
    """判断两个条件描述是否相同。

    使用关键词重叠度判断。
    """
    if not cond_a or not cond_b:
        return True  # 无明确条件 → 视为条件相同（保守）

    # 抽取汉字关键词
    import re as _re
    def _chars(s: str) -> set[str]:
        return set(_re.findall(r"[\u4e00-\u9fff]{2,}", s))

    chars_a = _chars(cond_a)
    chars_b = _chars(cond_b)

    if not chars_a or not chars_b:
        return True

    intersection = chars_a & chars_b
    overlap = len(intersection) / max(len(chars_a | chars_b), 1)
    return overlap >= 0.3  # 30% 词语重叠视为相同条件


def _has_negation(text: str) -> bool:
    """检查文本是否包含否定关键词。"""
    return any(kw in text for kw in _CONFLICT_NEGATION_WORDS)


def _detect_conflicts(
    new_text: str,
    existing_vectors: list[dict[str, Any]],
    embed_fn: Callable[[list[str]], list[list[float]] | None],
    cosine_sim_fn: Callable[[list[float], list[float]], float],
    conflict_threshold: float = 0.80,
) -> list[dict[str, Any]]:
    """条件化矛盾检测。

    两步判定:
    1. 条件是否相同（关键词重叠 >= 30%）
    2. 结论是否对立（一方有否定词，另一方无）

    Returns:
        [{"existing_id", "existing_text", "similarity", "condition_same", "reason"}]
    """
    new_emb = embed_fn([new_text])
    if not new_emb:
        return []

    new_vec = new_emb[0]
    new_cond = _extract_condition(new_text)
    new_has_neg = _has_negation(new_text)

    conflicts: list[dict[str, Any]] = []
    for existing in existing_vectors:
        k_vec = existing.get("k_vector")
        if k_vec is None:
            continue

        sim = cosine_sim_fn(new_vec, k_vec)
        if sim <= conflict_threshold:
            continue

        existing_text = existing.get("name", "")
        existing_cond = _extract_condition(existing_text)

        # Step 1: 条件是否相同
        cond_same = _conditions_are_same(new_cond, existing_cond)
        if not cond_same:
            # 条件不同 → 不判定矛盾
            continue

        # Step 2: 结论是否对立
        existing_has_neg = _has_negation(existing_text)
        if new_has_neg != existing_has_neg:
            conflicts.append({
                "existing_id": existing.get("id"),
                "existing_text": existing_text,
                "similarity": round(sim, 3),
                "condition_same": True,
                "reason": f"条件相同 + 结论对立 (sim={sim:.3f})",
            })

    return conflicts


# ========== 4. LLM 批量去重确认 ==========


def _embed_with_cache_ordered(
    texts: list[str],
    embed_cache: dict[str, list[float]],
    original_embed: Callable[[list[str]], list[list[float]] | None],
) -> tuple[list[list[float]] | None, bool]:
    """按输入顺序返回 embedding，混合缓存命中/未命中时不能重排。"""
    import hashlib

    result_list: list[list[float] | None] = [None] * len(texts)
    need: list[str] = []
    need_keys: list[str] = []
    need_positions: list[int] = []
    dirty = False

    for i, text in enumerate(texts):
        key = hashlib.md5(text.encode()).hexdigest()
        if key in embed_cache:
            result_list[i] = embed_cache[key]
        else:
            need.append(text)
            need_keys.append(key)
            need_positions.append(i)

    if need:
        fresh = original_embed(need)
        if fresh:
            for pos, emb, key in zip(need_positions, fresh, need_keys):
                if emb is None:
                    continue
                vector = list(emb)
                embed_cache[key] = vector
                result_list[pos] = vector
                dirty = True

    if any(emb is None for emb in result_list):
        return None, dirty
    return [emb for emb in result_list if emb is not None], dirty


def _batch_llm_dedup(
    pairs: list[tuple[str, str]],
    llm_judge_batch_fn: Callable[[list[tuple[str, str]]], list[bool]],
) -> list[bool]:
    """批量 LLM 去重确认。

    Args:
        pairs: [(new_text, existing_text), ...]
        llm_judge_batch_fn: 批量判断函数

    Returns:
        [is_dup, ...] 对应每个 pair
    """
    if not pairs:
        return []
    return llm_judge_batch_fn(pairs)


# ========== 主函数 ==========


@dataclass
class AdmitResult:
    """阶段3 产出"""
    passed: list[AtomicKnowledge] = field(default_factory=list)
    dedup_merged: list[AtomicKnowledge] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    review_items: list[ReviewItem] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=lambda: {
        "total": 0, "guard_dropped": 0, "dedup_merged": 0,
        "conflicts": 0, "passed": 0, "review": 0, "low_quality": 0,
    })


def admit_knowledge(
    atomic_list: list[AtomicKnowledge],
    *,
    existing_vectors: list[dict[str, Any]],
    embed_fn: Callable[[list[str]], list[list[float]] | None],
    cosine_sim_fn: Callable[[list[float], list[float]], float],
    llm_dedup_judge_fn: Callable[[str, str], bool] | None = None,
    llm_batch_judge_fn: Callable[[list[tuple[str, str]]], list[bool]] | None = None,
    batch_size: int = 5,
    threshold_direct: float = 0.95,
    threshold_llm: float = 0.90,
    conflict_threshold: float = 0.80,
    enable_conflict_detection: bool = True,
    cold_start_text_dedup: bool = False,
    db_adapter: Any = None,
    enable_pgvector_dedup: bool = True,
    enhanced_admission: bool = True,
    whitelist_sources: list[str] | None = None,
    enable_low_quality_detection: bool = True,
) -> AdmitResult:
    """阶段3 主函数：准入 + 去重 + 矛盾检测。

    流程:
    [atomic_list] → [兜底拦截] → [低质量检测] → [两段去重] → [矛盾检测] → [passed]

    Args:
        atomic_list: 阶段2 原子知识列表
        existing_vectors: 库中已有知识 embedding 列表
        embed_fn: embedding 函数
        cosine_sim_fn: 余弦相似度函数
        llm_dedup_judge_fn: LLM 单对去重判断
        llm_batch_judge_fn: LLM 批量去重判断
        batch_size: 批量去重批大小
        threshold_direct: 直接判重阈值
        threshold_llm: LLM 确认区间下界
        conflict_threshold: 矛盾检测阈值
        enable_conflict_detection: 是否启用矛盾检测
        cold_start_text_dedup: 冷启动模式（纯文本去重）
        db_adapter: DatabaseAdapter 实例
        enable_pgvector_dedup: 是否启用 pgvector 去重
        enhanced_admission: 是否启用增强门控（Feature Flag）
        whitelist_sources: 白名单来源前缀列表
        enable_low_quality_detection: 是否启用低质量模式检测

    Returns:
        AdmitResult
    """
    result = AdmitResult()

    if whitelist_sources is None:
        whitelist_sources = []

    result.stats["total"] = len(atomic_list)

    # Step 1: 兜底拦截
    passed_guard: list[AtomicKnowledge] = []
    for atomic in atomic_list:
        ok, reason = _guard_filter(atomic)
        if ok:
            passed_guard.append(atomic)
        else:
            logger.debug("兜底拦截丢弃: %s - %s", atomic["text"][:40], reason)
            result.stats["guard_dropped"] += 1

    if not passed_guard:
        return result

    # Step 1.5: 低质量模式检测（仅标记不丢弃，放入审查队列）
    low_quality_atomics: list[tuple[AtomicKnowledge, str]] = []
    if enhanced_admission and enable_low_quality_detection:
        remaining_after_lq: list[AtomicKnowledge] = []
        for atomic in passed_guard:
            is_whitelisted = _is_whitelisted(atomic, whitelist_sources)
            if is_whitelisted:
                remaining_after_lq.append(atomic)
                continue
            is_lq, lq_reason = _detect_low_quality(atomic)
            if is_lq:
                low_quality_atomics.append((atomic, lq_reason))
                result.stats["low_quality"] += 1
                result.review_items.append(ReviewItem(
                    type="low_quality",
                    text=atomic["text"],
                    original_text=atomic["text"],
                    original_claims_count=atomic.get("claims_count", 1),
                    reason=lq_reason,
                ))
            else:
                remaining_after_lq.append(atomic)
        passed_guard = remaining_after_lq

    # Step 1.5: 磁盘级 embedding 缓存 + 批量 pre-embed
    _embed_cache: dict[str, list[float]] = {}
    _cache_path = ".kb_embed_cache.json"

    # 加载已有缓存
    try:
        if os.path.exists(_cache_path):
            with open(_cache_path, encoding="utf-8") as _f:
                _cache_data = json.load(_f)
            for k, v in _cache_data.items():
                _embed_cache[k] = v
    except Exception:
        _embed_cache = {}

    # 只计算未缓存的新文本
    import hashlib
    _all_texts = [a["text"] for a in passed_guard]
    _need_embed: list[str] = []
    _need_keys: list[str] = []
    for t in _all_texts:
        _key = hashlib.md5(t.encode()).hexdigest()
        if _key not in _embed_cache:
            _need_embed.append(t)
            _need_keys.append(_key)

    if _need_embed and len(_need_embed) == len(_all_texts):
        # 全部未命中 → 一次批量调 API
        try:
            _embeddings = embed_fn(_all_texts)
            if _embeddings and len(_embeddings) == len(_all_texts):
                for t, e, k in zip(_all_texts, _embeddings, _need_keys):
                    _embed_cache[k] = list(e)
        except Exception:
            pass
    elif _need_embed:
        # 部分命中 → 只调缺失的
        try:
            _fresh = embed_fn(_need_embed)
            if _fresh and len(_fresh) == len(_need_embed):
                for t, e, k in zip(_need_embed, _fresh, _need_keys):
                    _embed_cache[k] = list(e)
        except Exception:
            pass

    # 写回磁盘缓存（函数结束时一次写入，避免重复写）
    _cache_dirty = False

    # 包装 embed_fn：优先使用缓存（内存 → 磁盘），替换原函数
    def _cached_embed(texts: list[str]) -> list[list[float]] | None:
        result_list, dirty = _embed_with_cache_ordered(texts, _embed_cache, _original_embed)
        if dirty:
            nonlocal _cache_dirty
            _cache_dirty = True
        return result_list

    _original_embed = embed_fn
    embed_fn = _cached_embed  # 替换原函数，后续所有 embed_fn 调用走缓存

    # Step 2: 两段去重（含批次内去重）
    # existing_vectors 和 batch_passed_vectors 共同构成去重对比池
    batch_passed_vectors: list[dict[str, Any]] = []  # 本批次已通过的去重对比向量
    # ID → 文本映射，供批量 LLM 去重查找已有文本
    _existing_text_map: dict[Any, str] = {
        ev["id"]: ev["name"] for ev in existing_vectors if ev.get("name")
    }

    if cold_start_text_dedup:
        # 冷启动：纯文本去重
        seen_texts: set[str] = set()
        for atomic in passed_guard:
            if atomic["text"] not in seen_texts:
                seen_texts.add(atomic["text"])
                result.passed.append(atomic)
                # 加入批次内对比池（后续 atomic 与之比较，实现跨文章去重）
                _add_to_batch_pool(atomic, batch_passed_vectors, embed_fn)
            else:
                result.dedup_merged.append(atomic)
                result.stats["dedup_merged"] += 1
    else:
        # 正常：embedding 两段去重
        llm_judge = llm_dedup_judge_fn or _default_llm_judge
        llm_need_confirm: list[tuple[AtomicKnowledge, str, float]] = []

        # 是否启用 pgvector 全库去重
        use_pgvector = enable_pgvector_dedup and db_adapter is not None

        for atomic in passed_guard:
            # 白名单放宽阈值：直接判重阈值从 0.95 放宽到 0.97
            is_wl = enhanced_admission and _is_whitelisted(atomic, whitelist_sources)
            effective_threshold_direct = threshold_direct
            effective_threshold_llm = threshold_llm
            if is_wl:
                effective_threshold_direct = min(threshold_direct + 0.02, 0.99)
                effective_threshold_llm = min(threshold_llm + 0.02, 0.97)

            # 合并对比池：已有向量 + 本批次已通过的向量（跨文章去重）
            compare_vectors = existing_vectors + batch_passed_vectors
            is_dup, matched_id, sim = _dedup_single(
                atomic["text"],
                compare_vectors,
                embed_fn,
                cosine_sim_fn,
                threshold_direct=effective_threshold_direct,
                threshold_llm=effective_threshold_llm,
                llm_judge_fn=llm_judge if not llm_batch_judge_fn else None,
                db_adapter=db_adapter if use_pgvector else None,
            )
            if is_dup:
                result.dedup_merged.append(atomic)
                result.stats["dedup_merged"] += 1
            elif sim >= effective_threshold_llm and sim <= effective_threshold_direct and llm_batch_judge_fn:
                # 需要批量 LLM 确认
                llm_need_confirm.append((atomic, matched_id or "", sim))
            else:
                result.passed.append(atomic)
                _add_to_batch_pool(atomic, batch_passed_vectors, embed_fn)

        # 批量 LLM 确认
        if llm_need_confirm and llm_batch_judge_fn:
            # 合并 batch pool 的 ID→文本映射
            for bv in batch_passed_vectors:
                _existing_text_map[bv["id"]] = bv["name"]
            pairs = [
                (a["text"], _existing_text_map.get(eid, a["text"]))
                for a, eid, _ in llm_need_confirm
            ]
            judgments = _batch_llm_dedup(pairs, llm_batch_judge_fn)
            for (atomic, _, _), is_dup in zip(llm_need_confirm, judgments):
                if is_dup:
                    result.dedup_merged.append(atomic)
                    result.stats["dedup_merged"] += 1
                else:
                    result.passed.append(atomic)
                    _add_to_batch_pool(atomic, batch_passed_vectors, embed_fn)

    # Step 3: 矛盾检测（在 passed 中检查，不入库但也放入 review）
    if enable_conflict_detection and result.passed and existing_vectors:
        for atomic in result.passed:
            conflicts = _detect_conflicts(
                atomic["text"],
                existing_vectors,
                embed_fn,
                cosine_sim_fn,
                conflict_threshold=conflict_threshold,
            )
            for c in conflicts:
                # 防重叠：已入去重 merged 的不再进入矛盾检测
                if any(m["text"] == atomic["text"] for m in result.dedup_merged):
                    continue
                result.conflicts.append(c)
                result.stats["conflicts"] += 1
                result.review_items.append(ReviewItem(
                    type="contradiction",
                    text=atomic["text"],
                    original_text=c.get("existing_text", ""),
                    original_claims_count=1,
                    reason=c.get("reason", "矛盾检测触发"),
                ))

    result.stats["passed"] = len(result.passed)
    result.stats["review"] = len(result.review_items)

    # 写回磁盘缓存（仅一次）
    if _cache_dirty:
        try:
            with open(_cache_path, "w", encoding="utf-8") as _f:
                json.dump(_embed_cache, _f, ensure_ascii=False)
        except Exception:
            pass

    return result
