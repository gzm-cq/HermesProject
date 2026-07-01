"""在线知识树召回 — 科目定位 → 注意力筛选 → 格式化注入

本模块实现 pre_llm_call 的核心逻辑：
1. locate_subject: 用户 query → 关键词/实体匹配 → SQL parent_id 下钻 → 定位最优科目
2. attention_filter: 在科目内部用注意力机制筛选知识点（Q×K^T / sqrt(d) → softmax）
3. format_context_lines: 格式化为 <memory source="knowledge_tree"> 注入格式
4. log_use: 回写 knowledge_use_log
"""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np

from knowledge_tree_builder import batch_embed, cosine_similarity
from knowledge_tree_builder import local_q

logger = logging.getLogger(__name__)


# ========== 关键词提取 ==========

_CJK_STOP_CHARS = frozenset(
    "的了在是有和就不人都也到说要去会着这他那她它那些吗吧呢啊哦嗯嘛"
)


def _extract_keywords(text: str) -> list[str]:
    """从文本中提取候选关键词。

    策略：
    - 英文词/标识符（>= 3 字符，转小写）
    - CJK 二字组（如果首字不是停用字）

    Args:
        text: 输入文本

    Returns:
        关键词列表
    """
    keywords: set[str] = set()

    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_\-\.]{2,}", text):
        keywords.add(token.lower())

    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    for i in range(len(cjk_chars) - 1):
        if cjk_chars[i] not in _CJK_STOP_CHARS:
            keywords.add(cjk_chars[i] + cjk_chars[i + 1])

    return list(keywords)


# ========== 科目定位 ==========


def locate_best_subject(
    query: str,
    query_embedding: list[float] | np.ndarray,
    adapter: Any,
) -> dict[str, Any] | None:
    """公共科目定位逻辑：关键词匹配 → embedding 余弦兆底。

    供 recall（locate_subject）和 placement（_locate_parent）共用，
    避免逻辑重复。

    Args:
        query: 用户查询原文
        query_embedding: query 的 embedding（list 或 np.ndarray）
        adapter: PluginDatabaseAdapter 实例

    Returns:
        定位到的科目 dict（含 id, name, children, child_count, depth），
        或 None
    """
    # Phase 1: 关键词/实体匹配
    keywords = _extract_keywords(query)
    if keywords:
        matched = adapter.search_subjects_by_keywords(keywords)
        if matched:
            matched.sort(key=lambda s: s.get("depth", 0), reverse=True)
            best = matched[0]
            children = adapter.get_child_nodes(best["id"])
            best["children"] = children
            best["child_count"] = len(children)
            return best

    # Phase 2: 无关键词匹配 → 全局 embedding 余弦定位
    domains = adapter.get_domain_nodes()
    if not domains:
        return None

    # 计算每个 domain 的 centroid（subject 节点的 k_vector 通常为 NULL，
    # 需要用子 knowledge_point 的 k_vector 实时算均值）
    best_domain: dict[str, Any] | None = None
    best_score = -1.0
    query_vec = np.array(query_embedding, dtype=np.float32)

    for domain in domains:
        k_vec = domain.get("k_vector")
        if k_vec is None:
            # 从子节点实时算 centroid
            children = adapter.get_child_nodes(domain["id"])
            child_vectors = [
                np.array(c["k_vector"], dtype=np.float32)
                for c in children if c.get("k_vector") is not None
            ]
            if child_vectors:
                k_vec = np.mean(child_vectors, axis=0)
            else:
                continue
        score = cosine_similarity(query_vec, k_vec if isinstance(k_vec, np.ndarray) else np.array(k_vec, dtype=np.float32))
        if score > best_score:
            best_score = score
            best_domain = domain

    if best_domain is not None:
        children = adapter.get_child_nodes(best_domain["id"])
        best_domain["children"] = children
        best_domain["child_count"] = len(children)
        best_domain["depth"] = 0
        logger.debug(
            "locate_best_subject: embedding match -> %s (score=%.4f)",
            best_domain.get("name", ""), best_score,
        )
        return best_domain

    return None


def locate_subject(
    query: str,
    query_embedding: list[float],
    adapter: Any,
    *,
    cold_start_threshold: int = 20,
) -> dict[str, Any] | None:
    """科目定位：委托公共逻辑 locate_best_subject，补充日志。

    策略：
    1. 先做关键词匹配：从 query 中提取实体词，ILIKE 模糊匹配 knowledge_tree.name
    2. 如果匹配到多个，取 PG 中路径最深的（最细粒度）
    3. 如果无关键词匹配，用 query embedding 余弦定位最顶层领域节点

    Args:
        query: 用户查询原文
        query_embedding: query 的 bge-m3 embedding
        adapter: PluginDatabaseAdapter 实例
        cold_start_threshold: 冷启动阈值（传给 caller 判断用）

    Returns:
        定位到的科目 dict:
        {id, name, node_type, parent_id, k_vector, local_offset,
         child_count, children, depth}
        或 None
    """
    best = locate_best_subject(query, query_embedding, adapter)
    if best is not None:
        logger.debug(
            "locate_subject: match -> %s (depth=%d)",
            best.get("name", ""), best.get("depth", 0),
        )
    else:
        logger.debug("locate_subject: no match found")
    return best


# ========== 注意力筛选 ==========


def attention_filter(
    query_embedding: list[float],
    child_nodes: list[dict[str, Any]],
    *,
    cold_start: bool = False,
    min_score: float = 0.3,
    max_results: int = 5,
    local_offset: list[float] | None = None,
) -> list[dict[str, Any]]:
    """在科目内部用注意力机制筛选知识点。

    注意力公式：
        Q = query embedding
        K = 每个子节点的 k_vector
        attention_score = softmax(Q × K^T / sqrt(d))

    冷启动期回退到余弦相似度（Q/K 未分离）。

    Args:
        query_embedding: query 的原始 bge-m3 embedding（1024 维）
        child_nodes: 科目下子节点列表，每项含 {id, name, k_vector, node_type, text, ...}
        cold_start: 是否处于冷启动期（True → 回退余弦相似度）
        min_score: 最低保留分数（softmax 后）
        max_results: 最多返回的知识点数

    Returns:
        按相关性降序排列的知识点列表，每项含 {id, name, text, score}
    """
    if not child_nodes:
        return []

    # 过滤出有 k_vector 的子节点
    candidates = [
        c for c in child_nodes if c.get("k_vector") is not None
    ]
    if not candidates:
        return []

    try:
        query_vec = np.array(query_embedding, dtype=np.float32)
        dim = len(query_vec)

        # TaxoGen 局部投影
        if local_offset is None and len(candidates) >= 3:
            child_centroid = np.mean([np.array(c["k_vector"], dtype=np.float32) for c in candidates], axis=0)
            local_offset = child_centroid.tolist()

        scores: list[tuple[int, float]] = []

        for i, node in enumerate(candidates):
            k_vec = np.array(node["k_vector"], dtype=np.float32)

            if cold_start:
                sim = cosine_similarity(query_vec, k_vec)
            else:
                q_local = local_q(query_embedding, local_offset, child_count=len(candidates)) if local_offset else query_vec
                q_local_np = np.array(q_local, dtype=np.float32) if isinstance(q_local, list) else q_local
                dot_product = float(np.dot(q_local_np, k_vec))
                sim = dot_product / (np.sqrt(dim) + 1e-10)

            scores.append((i, sim))

        scores_arr = np.array([s[1] for s in scores], dtype=np.float32)
        scores_arr -= np.max(scores_arr)
        exp_scores = np.exp(scores_arr)
        softmax_scores = exp_scores / (np.sum(exp_scores) + 1e-10)

        ranked = sorted(
            [(candidates[idx], float(softmax_scores[i])) for i, (idx, _) in enumerate(scores)],
            key=lambda x: x[1], reverse=True,
        )

        # cold_start 用余弦相似度，min_score 有绝对语义；非 cold_start 用
        # softmax attention，分数随兄弟节点数量变化，不适合直接套 0.3 阈值。
        # 因此非冷启动采用"保留 topK，但过滤接近 0 的噪音"策略。
        if cold_start:
            kept = [(node, score) for node, score in ranked if score >= min_score]
        else:
            floor = 1.0 / max(len(ranked), 1) * 0.1
            kept = [(node, score) for node, score in ranked if score >= floor]
        kept = kept[:max_results]

        result: list[dict[str, Any]] = []
        for node, score in kept:
            result.append({
                "id": node["id"],
                "name": node.get("name", ""),
                "text": node.get("text", ""),
                "score": round(score, 4),
            })
        return result

    except Exception:
        logger.debug("attention_filter 异常，返回空结果", exc_info=True)
        return []


# ========== 时态过滤 ==========


_TIME_KEYWORDS_PAST = ["过去", "以前", "之前", "旧版", "老版本", "历史上", "曾", "曾经"]
_TIME_KEYWORDS_PRESENT = ["现在", "当前", "目前", "最新", "现在的", "现行", "新版", "新版本"]
_TIME_KEYWORDS_FUTURE = ["将来", "未来", "以后", "之后", "即将", "下一步"]

_DATE_PATTERN = re.compile(
    r"(?:(?:19|20)\d{2})[\-年/.]"
    r"(?:(?:0?[1-9]|1[0-2])[\-月/.])?"
    r"(?:(?:0?[1-9]|[12]\d|3[01])[日号]?)?"
)


def _parse_query_date(query: str) -> str | None:
    """从用户 query 中提取日期信号（仅提取首次出现的日期）。

    返回 ISO 日期字符串（YYYY-MM-DD / YYYY-MM / YYYY）或 None。
    仅作为粗粒度启发式，不保证精确。
    """
    m = _DATE_PATTERN.search(query)
    if not m:
        return None
    raw = m.group(0)

    parts = re.split(r"[\-年/.]", raw.rstrip("日号"))
    parts = [p for p in parts if p]
    if not parts:
        return None
    try:
        y = int(parts[0])
        if len(parts) >= 2:
            mo = int(parts[1])
            if len(parts) >= 3:
                d = int(parts[2])
                return f"{y:04d}-{mo:02d}-{d:02d}"
            return f"{y:04d}-{mo:02d}-01"
        return f"{y:04d}-01-01"
    except ValueError:
        return None


def _date_cmp(date_str: str, target_str: str) -> int:
    """比较两个 ISO 日期字符串（允许 YYYY / YYYY-MM / YYYY-MM-DD 粒度不同）。

    返回 -1 / 0 / 1。比较时取最低公共粒度。
    """
    if not date_str or not target_str:
        return 0
    dp = date_str.split("-")
    tp = target_str.split("-")
    min_len = min(len(dp), len(tp))
    for i in range(min_len):
        try:
            a = int(dp[i])
            b = int(tp[i])
        except ValueError:
            return 0
        if a < b:
            return -1
        if a > b:
            return 1
    return 0


def temporal_filter(
    kp_results: list[dict[str, Any]],
    query: str,
    *,
    demote_factor: float = 0.5,
) -> list[dict[str, Any]]:
    """对 attention_filter 结果做时态过滤 / 降权（P3-9）。

    策略：
    1. 从用户 query 中提取日期信号
    2. 如果 query 有明确"过去 / 历史"关键词：只保留 valid_until 之前的，其他降权
    3. 如果 query 有明确"现在 / 最新"关键词：过期（valid_until < 今天）的降权
    4. 如果 query 有具体日期：不在该日期有效范围内的降权
    5. 没有时间信号：不做任何调整（不删除，只降权，避免误杀）

    降权通过乘 demote_factor 实现，分数较低的会自然掉到 topK 之外。

    Args:
        kp_results: attention_filter 返回的 [{id, name, text, score, valid_from?, valid_until?}, ...]
        query: 用户查询原文
        demote_factor: 过期记忆降权系数（0-1）

    Returns:
        调整分数后的结果列表（仍按 score 降序）
    """
    if not kp_results:
        return []

    has_past_kw = any(kw in query for kw in _TIME_KEYWORDS_PAST)
    has_present_kw = any(kw in query for kw in _TIME_KEYWORDS_PRESENT)
    has_future_kw = any(kw in query for kw in _TIME_KEYWORDS_FUTURE)
    query_date = _parse_query_date(query)

    if not (has_past_kw or has_present_kw or has_future_kw or query_date):
        return kp_results

    from datetime import date
    today_str = date.today().isoformat()

    adjusted: list[dict[str, Any]] = []
    for item in kp_results:
        score = item.get("score", 0.0)
        vf = item.get("valid_from")
        vu = item.get("valid_until")
        new_score = score
        demoted = False

        if has_present_kw and vu and _date_cmp(vu, today_str) < 0:
            new_score *= demote_factor
            demoted = True

        if has_past_kw and vf and _date_cmp(vf, today_str) > 0:
            new_score *= demote_factor
            demoted = True

        if query_date:
            if vf and _date_cmp(vf, query_date) > 0:
                new_score *= demote_factor
                demoted = True
            if vu and _date_cmp(vu, query_date) < 0:
                new_score *= demote_factor
                demoted = True

        new_item = dict(item)
        new_item["score"] = round(new_score, 4)
        if demoted:
            new_item["temporal_demoted"] = True
        adjusted.append(new_item)

    adjusted.sort(key=lambda x: x["score"], reverse=True)
    return adjusted


# ========== 格式化注入 ==========


def format_context_lines(
    knowledge_points: list[dict[str, Any]],
) -> list[str]:
    """格式化知识树召回结果为 <memory-context> 注入格式。

    与 knowledge-navigation 插件的格式兼容，但 source 为 "knowledge_tree"。

    Args:
        knowledge_points: attention_filter 返回的知识点列表

    Returns:
        格式化后的文本行列表
    """
    if not knowledge_points:
        return []

    lines: list[str] = []
    for kp in knowledge_points:
        text = kp.get("text", "") or kp.get("name", "")
        if text:
            import html as _html
            node_id = kp.get("id", "")
            score = kp.get("score", 0.0)
            # 动态读取 source 字段，避免多跳 strategy 信息丢失
            source = kp.get("source", "knowledge_tree")
            escaped_text = _html.escape(str(text[:500]), quote=False)
            escaped_id = _html.escape(str(node_id), quote=True)
            lines.append(
                f'  <memory source="{source}" node_id="{escaped_id}" '
                f'score="{score:.4f}">{escaped_text}</memory>'
            )
    # 只有存在实际内容行时才包装 memory-context 标签
    if not lines:
        return []
    return ["<memory-context>"] + lines + ["</memory-context>"]


# ========== 使用日志回写 ==========


def log_use(
    adapter: Any,
    session_id: str,
    node_ids: list[int],
    query: str,
) -> None:
    """回写 knowledge_use_log 表。

    记录本次被取出的知识点 ID，用于后续 consolidation 共现检测。

    Args:
        adapter: PluginDatabaseAdapter 实例
        session_id: 当前会话 ID
        node_ids: 被取出的知识点 ID 列表
        query: 触发 recall 的原始查询
    """
    try:
        adapter.log_use(
            session_id=session_id,
            node_ids=node_ids,
            query=query,
        )
    except Exception as e:
        logger.warning("log_use failed: %s", e)
