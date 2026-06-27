"""记忆过滤器模块。

负责基于 rerank_score 的精度过滤和结果筛选。
"""

import html
import logging
import math
import re

logger = logging.getLogger(__name__)

from knowledge_navigation.config import CONFIG

# 标记正则：匹配 [标记: 错误] 等
# 标记匹配：排除类（错误/作废/可疑/待验证）和降权类（已解决）
_MARK_EXCLUDE = re.compile(r"\[标记: (?:错误|作废|可疑|待验证)\]")
_MARK_DEMOTE = re.compile(r"\[标记: 已解决\]")


def exclude_marked(results: list[dict]) -> tuple[list[dict], int]:
    """排除/降权已被标记的记忆条目。

    标记格式：[标记: 类型]，由聚类项目 mark_memory.py 维护。

    处理策略：
    - [标记: 错误/作废/可疑/待验证] → 完全排除（信息已过时/不可信）
    - [标记: 已解决] → 保留（解决方案仍有参考价值）
    - 标记在文本中间而非末尾（讨论标记机制的假阳性）→ 不处理

    Returns:
        (过滤后的列表, 被排除的数量)
    """
    excluded = 0
    demoted = 0
    kept: list[dict] = []
    for r in results:
        text = r.get("text", "") or ""
        # P0: 标记在末尾才视为真实标记（mark_memory 通过 SQL 追加到文本末尾）
        tail = text[-100:]  # 标记最多 ~50 字符，取 100 足够
        if _MARK_EXCLUDE.search(tail):
            excluded += 1
            continue
        if _MARK_DEMOTE.search(tail):
            # [标记: 已解决] → 降权而非排除（经验：已修好的反面案例仍有参考价值）
            r["rerank_score"] = r.get("rerank_score", 1.0) * 0.3
            demoted += 1
        kept.append(r)

    if demoted:
        logger.info("标记降权: %d 条 [已解决] 记忆降权 0.3x", demoted)
    return kept, excluded


def calculate_time_score(mentioned_at_str: str | None) -> float:
    """基于时间衰减计算时效分数。

    使用指数衰减：score = exp(-days / halflife)
    最新记忆（0 天前）→ 1.0
    halflife 天后 → ~0.37
    3×halflife 天后 → ~0.05
    halflife 通过 CONFIG.temporal_halflife_days 配置（默认 30）。
    """
    from knowledge_navigation.config import CONFIG
    halflife = CONFIG.temporal_halflife_days
    if not mentioned_at_str:
        return 0.5  # 无时间戳时取中性值

    try:
        from datetime import datetime, timezone

        mentioned_at = datetime.fromisoformat(mentioned_at_str)
        if mentioned_at.tzinfo is None:
            mentioned_at = mentioned_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = (now - mentioned_at).total_seconds() / 86400.0
        if days < 0:
            days = 0.0
        return math.exp(-days / halflife)
    except (ValueError, TypeError):
        return 0.5


def extract_rerank_scores(trace_data: dict) -> dict[str, float]:
    """从 trace 数据中提取 rerank_score 映射。"""
    rerank_map: dict[str, float] = {}
    reranked = trace_data.get("reranked", [])
    for r in reranked:
        node_id = r.get("node_id", "")
        score = r.get("rerank_score", 0.0)
        if node_id:
            rerank_map[node_id] = float(score)
    return rerank_map


def filter_by_score(
    raw_results: list[dict],
    rerank_map: dict[str, float],
    min_score: float = CONFIG.min_score,
    max_results: int = CONFIG.max_results,
    enable_temporal: bool = False,
    mentioned_at_map: dict[str, str] | None = None,
) -> tuple[list[dict], list[float], list[dict]]:
    """基于分数过滤结果。

    默认按 rerank_score 降序排列后截取。
    启用时态融合后，使用乘法融合公式：
        fused = rerank_score × (floor_w + (1 − floor_w) × time_score)
    其中 floor_w 为保底系数（默认 0.3），旧记忆至少保留 floor_w 比例的基础分。
    无论是否启用时态融合，始终同时计算 base_score 和 temporal_score
    用于双分对比日志记录。

    Args:
        raw_results: 原始召回结果列表
        rerank_map: {node_id: rerank_score} 映射
        min_score: 最小分数阈值
        max_results: 最大返回数量
        enable_temporal: 是否启用时态融合排序
        mentioned_at_map: {node_id: mentioned_at} 映射，时态融合时需要

    Returns:
        (kept, all_scores, comparison_data)
        kept: 保留的结果列表
        all_scores: 所有原始 rerank_score（含被过滤的）
        comparison_data: 每个保留结果的双分对比 [{node_id, base_score, temporal_score}]
    """
    if enable_temporal and not mentioned_at_map:
        enable_temporal = False  # 无时间数据时降级为纯分数排序

    all_scores: list[float] = []
    candidates: list[tuple[dict, float]] = []

    for r in raw_results:
        node_id = r.get("id", "")
        score = rerank_map.get(node_id, 0.0)
        all_scores.append(score)
        if score >= min_score:
            if enable_temporal:
                time_score = calculate_time_score(
                    mentioned_at_map.get(node_id) if mentioned_at_map else None
                )
                # 乘法融合：fused = rerank × (floor + (1-floor) × time_score)
                # 新记忆 time_score≈1.0 → fused = rerank（不被拉低）
                # 旧记忆 time_score→0 → fused = rerank × floor（保底）
                floor_w = CONFIG.temporal_floor_weight
                fused = score * (floor_w + (1.0 - floor_w) * time_score)
            else:
                fused = score
            r["base_score"] = score
            r["final_score"] = fused
            r["rerank_score"] = fused
            r.setdefault("score_source", "reranker")
            candidates.append((r, fused))

    # 按融合分数降序排列 → 去重 → MMR 多样性重排
    candidates.sort(key=lambda x: x[1], reverse=True)
    # 去重 + 同步裁剪 scores（_dedup_candidates 只保留首次出现，scores 需一一对应）
    seen_nodes: set[str] = set()
    candidates_deduped: list[dict] = []
    scores_deduped: list[float] = []
    for r, s in candidates:
        nid = r.get("id", "") or r.get("text", "")[:50]
        if nid not in seen_nodes:
            seen_nodes.add(nid)
            candidates_deduped.append(r)
            scores_deduped.append(s)
    # MMR 多样性重排
    kept = _mmr_diversity(candidates_deduped, scores_deduped, max_results, CONFIG.lambda_mrr)

    # 构建双分对比数据（始终计算，不受 enable_temporal 影响）
    comparison_data: list[dict[str, float]] = []
    for r in kept:
        node_id = r.get("id", "")
        base_score = rerank_map.get(node_id, 0.0)
        time_score_val = calculate_time_score(
            mentioned_at_map.get(node_id) if mentioned_at_map else None
        )
        floor_w = CONFIG.temporal_floor_weight
        temporal_score_val = round(base_score * (floor_w + (1.0 - floor_w) * time_score_val), 4)
        comparison_data.append({
            "node_id": node_id,
            "base_score": round(base_score, 4),
            "temporal_score": temporal_score_val,
        })

    return kept, all_scores, comparison_data


def dedup_by_text(
    results: list[dict],
    threshold: float = 0.8,
) -> list[dict]:
    """文本相似去重：同一轮 recall 中 Jaccard 相似度 > threshold 的只保留一条。

    用于 Hindsight 的重复记忆过滤。不依赖 embedding，纯文本 n-gram 比较。
    保留第一个出现的条目，后续相似的跳过。
    """
    if not results:
        return results

    deduped: list[dict] = []
    seen_texts: list[str] = []
    for r in results:
        text = str(r.get("text", "") or r.get("name", "")).strip()
        if not text:
            continue
        is_dup = any(_jaccard(text, seen) > threshold for seen in seen_texts)
        if not is_dup:
            deduped.append(r)
            seen_texts.append(text)

    dropped = len(results) - len(deduped)
    if dropped:
        logger.info("文本去重: 移除 %d 条重复记忆", dropped)
    return deduped


def _jaccard(a: str, b: str) -> float:
    """模块级 Jaccard 相似度（去噪后比较），供 dedup 和 MMR 共用。"""
    import re as _re
    for pat in [
        r"\[因果来源：[^\[\]]*\]\s*\[因果结果：[^\[\]]*\]",  # 因果标签
        r"\| When: \d{4}-\d{2}-\d{2}",                      # 时间戳
        r"\[[\d]{8}_[\d]{6}_[a-z0-9]+\]",                   # 会话ID
        r"^系统存在[^，。]{2,30}[：:]",                        # 前缀短差异
    ]:
        a = _re.sub(pat, "", a).strip()
        b = _re.sub(pat, "", b).strip()
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _mmr_diversity(
    candidates: list[dict],
    scores: list[float],
    max_results: int,
    lambda_mrr: float = 0.6,
) -> list[dict]:
    """MMR 多样性重排。

    MMR = argmax_{d ∈ R\\S} [ λ · score_norm(d) − (1−λ) · max_{s ∈ S} sim(d, s) ]

    在相关性与多样性之间做权衡：
    - λ → 1.0：纯相关性排序（接近原行为）
    - λ → 0.0：纯多样性排序

    Args:
        candidates: 已去重的候选集
        scores: 对应每个候选的 fused_score
        max_results: 最大返回数量
        lambda_mrr: MMR λ，越大越重相关性

    Returns:
        MMR 重排后的 top-k 列表
    """
    if not candidates or max_results <= 0:
        return []

    # 归一化分数到 [0,1]
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-6:
        norms = [0.5] * len(scores)
    else:
        norms = [(s - lo) / (hi - lo) for s in scores]

    selected: list[int] = []
    remaining = set(range(len(candidates)))

    for _ in range(min(max_results, len(candidates))):
        best_idx = None
        best_mmr = -float("inf")
        for i in remaining:
            rel = norms[i]
            if selected:
                c_text = str(candidates[i].get("text", "") or candidates[i].get("name", ""))
                div = max(
                    _jaccard(
                        c_text,
                        str(candidates[j].get("text", "") or candidates[j].get("name", "")),
                    )
                    for j in selected
                )
            else:
                div = 0.0
            mmr = lambda_mrr * rel - (1 - lambda_mrr) * div
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i
        if best_idx is not None:
            selected.append(best_idx)
            remaining.remove(best_idx)

    return [candidates[i] for i in selected]


def extract_ce_raw_scores(trace_data: dict) -> dict[str, float]:
    """从 trace 中提取原始 cross-encoder 分数。

    Hindsight trace 的 score_components 已暴露 cross_encoder_score（原始 CE 分 [0,1]），
    用于 score_span 压缩决策。

    Args:
        trace_data: Hindsight recall trace 数据
    Returns:
        {node_id: cross_encoder_score} 映射
    """
    ce_map: dict[str, float] = {}
    reranked = trace_data.get("reranked", [])
    for r in reranked:
        node_id = r.get("node_id", "")
        sc = r.get("score_components", {}) or {}
        ce_raw = sc.get("cross_encoder_score", 0.0)
        if node_id and ce_raw >= 0.5:  # 过滤掉 0~[0,1) 的无意义低分
            ce_map[node_id] = float(ce_raw)
    return ce_map


def compress_by_score_span(
    kept: list[dict],
    ce_raw_map: dict[str, float],
    max_results: int,
    top3_threshold: float = 0.9,
    half_threshold: float = 0.7,
) -> list[dict]:
    """根据原始 CE 分数 span 动态压缩结果数量。

    如果 top-1 与 bottom-1 的 CE 原始分数差距很大，
    说明底部的记忆质量明显差，可以安全裁切。

    Args:
        kept: filter_by_score + MMR 后的结果
        ce_raw_map: {node_id: cross_encoder_score} 映射
        max_results: 最大结果数
        top3_threshold: 跨度超过此值时压缩到 top-3
        half_threshold: 跨度超过此值时压缩到一半

    Returns:
        压缩后的结果
    """
    if not kept or len(kept) <= 3:
        return kept

    scores = [ce_raw_map.get(r.get("id", ""), 0.0) for r in kept]
    scores = [s for s in scores if s > 0]
    if not scores or len(scores) < 2:
        return kept

    span = max(scores) - min(scores)

    if span > top3_threshold:
        return kept[:3]  # 差距极大，安全地只留 3 条
    elif span > half_threshold:
        new_k = max(3, min(max_results // 2, len(kept)))
        return kept[:min(new_k, max_results)]  # 差距大，砍半但不超 max_results
    else:
        return kept  # 跨度小，全部保留


def format_context_lines(
    results: list[dict],
    max_text_length: int = CONFIG.max_text_length,
) -> list[str]:
    """格式化上下文行，使用 XML 包裹格式。

    支持混合来源：向量匹配结果和多跳展开结果分别用不同的 memory-context 块。

    替代旧的 [Hindsight] 纯文本前缀格式，
    输出 <memory-context> XML 格式使模型能准确区分记忆与用户输入。
    """
    lines: list[str] = []
    if not results:
        return lines

    # 按 source 分组
    vector_results = [r for r in results if r.get("source", "hindsight") != "multi-hop"]
    multi_hop_results = [r for r in results if r.get("source") == "multi-hop"]

    def _format_block(items: list[dict], block_label: str) -> list[str]:
        """格式化一个来源的上下文块"""
        block: list[str] = []
        for r in items:
            text = str(r.get("text", "")).strip()
            if text:
                truncated_text = text[:max_text_length]
                escaped_text = html.escape(truncated_text, quote=False)
                node_id = r.get("id", "")
                escaped_id = html.escape(str(node_id), quote=True)
                source = r.get("source", block_label)
                escaped_source = html.escape(str(source), quote=True)
                block.append(f'  <memory source="{escaped_source}" node_id="{escaped_id}">{escaped_text}</memory>')
        return block

    # 向量匹配结果
    vector_lines = _format_block(vector_results, "hindsight")
    if vector_lines:
        lines.append("<memory-context source=\"vector\">")
        lines.extend(vector_lines)
        lines.append("</memory-context>")

    # 多跳展开结果（单独块，让 LLM 清楚区分）
    mh_lines = _format_block(multi_hop_results, "multi-hop")
    if mh_lines:
        lines.append("<memory-context source=\"multi-hop\">")
        lines.extend(mh_lines)
        lines.append("</memory-context>")

    return lines


def calculate_score_stats(scores: list[float]) -> dict[str, float]:
    """计算分数统计信息。"""
    if not scores:
        return {"min": 0.0, "max": 0.0, "avg": 0.0, "count": 0}

    return {
        "min": min(scores),
        "max": max(scores),
        "avg": sum(scores) / len(scores),
        "count": len(scores),
    }


# ========== 跨域语义去重 ==========


def _char_ngram_jaccard(text_a: str, text_b: str, n: int = 3) -> float:
    """基于字符 n-gram 的 Jaccard 相似度（0~1）。

    适用于跨域文本去重，无需 embedding 即可捕获近似重复内容。
    """
    if not text_a or not text_b:
        return 0.0
    a = text_a.lower().strip()
    b = text_b.lower().strip()
    if len(a) < n or len(b) < n:
        n = max(2, min(n, len(a), len(b)))
    set_a = {a[i:i + n] for i in range(len(a) - n + 1)}
    set_b = {b[i:i + n] for i in range(len(b) - n + 1)}
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _cosine_similarity_vec(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def cross_domain_dedup(
    hindsight_results: list[dict],
    kt_results: list[dict],
    *,
    threshold: float = 0.65,
    embed_fn: "callable | None" = None,
) -> tuple[list[dict], int]:
    """跨域语义去重：知识树结果与 Hindsight 结果比较，去除重复知识。

    优先使用 embedding 余弦相似度（语义级），
    embed_fn 不可用时回退到字符 n-gram Jaccard（文本级）。

    Args:
        hindsight_results: Hindsight 过滤后的结果 [{id, text, ...}]
        kt_results: 知识树结构化结果 [{id, name, text, score}]
        threshold: 去重阈值（相似度 ≥ threshold 视为重复）
        embed_fn: embedding 函数 (texts: list[str]) -> list[list[float]] | None

    Returns:
        (deduped_kt_results, removed_count)
    """
    if not kt_results or not hindsight_results:
        return kt_results, 0

    hs_texts = [str(r.get("text", "")).strip() for r in hindsight_results if r.get("text")]
    if not hs_texts:
        return kt_results, 0

    # 尝试使用 embedding 做语义去重
    if embed_fn is not None:
        all_texts = hs_texts + [
            (kp.get("text", "") or kp.get("name", "")) for kp in kt_results
        ]
        try:
            all_embeddings = embed_fn(all_texts)
            if all_embeddings and len(all_embeddings) == len(all_texts):
                hs_vecs = all_embeddings[:len(hs_texts)]
                kt_vecs = all_embeddings[len(hs_texts):]
                return _dedup_by_cosine(kt_results, kt_vecs, hs_vecs, threshold)
        except Exception:
            pass  # embedding 失败时回退到文本去重

    # 回退：字符 n-gram Jaccard
    return _dedup_by_jaccard(kt_results, hs_texts, threshold)


def _dedup_by_cosine(
    kt_results: list[dict],
    kt_vecs: list[list[float]],
    hs_vecs: list[list[float]],
    threshold: float,
) -> tuple[list[dict], int]:
    """使用 embedding 余弦相似度去重。"""
    kept: list[dict] = []
    removed = 0
    for i, kp in enumerate(kt_results):
        if i >= len(kt_vecs):
            kept.append(kp)
            continue
        is_dup = False
        for hs_vec in hs_vecs:
            if _cosine_similarity_vec(kt_vecs[i], hs_vec) >= threshold:
                is_dup = True
                removed += 1
                break
        if not is_dup:
            kept.append(kp)
    return kept, removed


def _dedup_by_jaccard(
    kt_results: list[dict],
    hs_texts: list[str],
    threshold: float,
) -> tuple[list[dict], int]:
    """使用字符 n-gram Jaccard 相似度去重（embedding 不可用时的回退）。"""
    kept: list[dict] = []
    removed = 0
    for kp in kt_results:
        kt_text = (kp.get("text", "") or kp.get("name", "")).strip()
        is_dup = False
        for hs_text in hs_texts:
            if _char_ngram_jaccard(kt_text, hs_text) >= threshold:
                is_dup = True
                removed += 1
                break
        if not is_dup:
            kept.append(kp)
    return kept, removed
