"""知识导航 Hook — Router 决策、四路 recall 与后处理。

拆分自 hooks.py 的路由/召回/后处理相关代码：
- 文本处理（_extract_keywords, _normalize_eval_text, _normalize_kt_score）
- Eval 匹配（_match_eval_query, _build_mentioned_at_map）
- 四路 recall 函数（_do_hindsight_recall, _do_kt_recall, _do_skill_match, _do_sag_recall）
- 候选构建（_build_knowledge_tree_candidate, _candidate_score）
- 门控与路由（_pass_gates, _get_router_mask）
- 执行与后处理（_execute_recall, _dedup_and_budget, _expand_multi_hop, _assemble_xml_output, _post_process_recall）
- pre_llm_call 入口
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from knowledge_navigation.adapters.hindsight import HindsightClient
from knowledge_navigation.config import CONFIG
from knowledge_navigation.core.circuit_breaker import (
    circuit_is_open,
    circuit_record_failure,
    circuit_record_success,
    sag_circuit_is_open,
    sag_circuit_record_failure,
    sag_circuit_record_success,
)
from knowledge_navigation.core.filtering import (
    apply_token_budget,
    calculate_score_stats,
    cross_domain_dedup,
    estimate_tokens,
    exclude_marked,
    extract_rerank_scores,
    extract_score,
    filter_by_score,
)
from knowledge_navigation.core.hooks.cache import (
    _compaction,
    _ensure_kt_imported,
    _eval_logger,
    _get_eval_logger,
    _get_use_logger,
    _hit_counter,
    _injected_ids,
    _injected_lock,
    _injected_session_ts,
    _INJECTED_LRU_MAX,
    _load_eval_queries,
    _multi_hop_recall,
    _recall_executor,
    _recall_knowledge_tree_raw,
    _task_tracker,
    _touch_injected_session,
    HAS_KNOWLEDGE_TREE,
)
from knowledge_navigation.core.hooks.db import _batch_embed, _causal_boost
from knowledge_navigation.core.recall_logger import RecallLogger
from knowledge_navigation.core.router import route as _router_route
from knowledge_navigation.core.text_utils import CJK_STOP_CHARS as _CJK_STOP_CHARS
from knowledge_navigation.turn_gate import (
    skip_non_user,
    skip_pre_llm_call,
    skip_system_prompt,
)

logger = logging.getLogger(__name__)

__all__ = [
    "_assemble_xml_output",
    "_build_knowledge_tree_candidate",
    "_build_mentioned_at_map",
    "_candidate_score",
    "_dedup_and_budget",
    "_do_hindsight_recall",
    "_do_kt_recall",
    "_do_sag_recall",
    "_do_skill_match",
    "_execute_recall",
    "_expand_multi_hop",
    "_extract_keywords",
    "_get_router_mask",
    "_match_eval_query",
    "_normalize_eval_text",
    "_normalize_kt_score",
    "_pass_gates",
    "_post_process_recall",
    "pre_llm_call",
]

# 预编译正则表达式
_EVAL_PATTERN = re.compile(r"\[EVAL:([^\]]+)\]", re.IGNORECASE)
_EVAL_CLEAN_PATTERN = re.compile(r"\[EVAL:[^\]]+\]", re.IGNORECASE)
_WHITESPACE_PATTERN = re.compile(r"\s+")


# ========== 文本处理 ==========


def _extract_keywords(text: str) -> set[str]:
    """提取文本中的有意义关键词（仅用于 eval query 匹配）。"""
    from knowledge_navigation.core.text_utils import extract_keywords as _tu_extract
    return _tu_extract(
        text,
        min_en_length=2,
        include_cjk_bigrams=True,
        include_cjk_full=False,
    )


def _normalize_eval_text(text: str) -> str:
    """规范化 eval query 文本：去掉显式标记并折叠空白。"""
    text = _EVAL_CLEAN_PATTERN.sub("", text)
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


# ========== Eval 匹配 ==========


def _match_eval_query(user_message: str) -> dict | None:
    """匹配评测查询，但只让 exact / explicit 进入 recall@k 计数。"""
    queries = _load_eval_queries()
    if not queries:
        return None

    el = _get_eval_logger()
    eval_log_data: dict[str, Any] = {
        "event": "eval_match",
        "user_message_trunc": user_message[:60],
    }

    by_id = {str(item.get("query_id", "")): item for item in queries if item.get("query_id")}

    def _build_result(item: dict, method: str, confidence: float, counted: bool) -> dict:
        return {
            "query_id": item.get("query_id", ""),
            "expected_ids": item.get("expected_ids", []),
            "match_method": method,
            "confidence": confidence,
            "counted": counted,
        }

    # 1. 显式触发
    explicit = _EVAL_PATTERN.search(user_message)
    if explicit:
        explicit_id = explicit.group(1).strip()
        item = by_id.get(explicit_id)
        eval_log_data["match_type"] = "explicit_id"
        eval_log_data["matched_query_id"] = explicit_id if item else None
        eval_log_data["score"] = 1.0 if item else 0.0
        eval_log_data["accepted"] = item is not None
        eval_log_data["counted"] = item is not None
        if el:
            el.info("eval_match", extra=eval_log_data)
        return _build_result(item, "explicit_id", 1.0, True) if item else None

    normalized_user = _normalize_eval_text(user_message)

    main_log_data: dict[str, Any] = {
        "event": "eval_match",
        "user_message_trunc": user_message[:60],
    }

    # 2. 规范化精确匹配
    for item in queries:
        if _normalize_eval_text(str(item.get("query", ""))) == normalized_user:
            eval_log_data["match_type"] = "exact"
            eval_log_data["matched_query_id"] = item.get("query_id")
            eval_log_data["matched_query_trunc"] = item.get("query", "")[:60]
            eval_log_data["score"] = 1.0
            eval_log_data["accepted"] = True
            eval_log_data["counted"] = True
            main_log_data["match_type"] = "exact"
            main_log_data["matched_query_id"] = item.get("query_id")
            main_log_data["matched_query_trunc"] = item.get("query", "")[:60]
            main_log_data["score"] = 1.0
            main_log_data["accepted"] = True
            main_log_data["counted"] = True
            main_log_data["query_id"] = item["query_id"]
            logger.info("eval_match", extra=main_log_data)
            if el:
                el.info("eval_match", extra=eval_log_data)
            return _build_result(item, "exact", 1.0, True)

    # 3. 关键词重叠
    user_keywords = _extract_keywords(user_message)
    if not user_keywords:
        eval_log_data["match_type"] = "fuzzy"
        eval_log_data["user_keywords"] = []
        eval_log_data["candidates"] = []
        eval_log_data["matched_query_id"] = None
        eval_log_data["accepted"] = False
        eval_log_data["counted"] = False
        if el:
            el.info("eval_match", extra=eval_log_data)
        return None

    candidates: list[dict[str, Any]] = []
    best_item: dict | None = None
    best_score = 0.0

    for item in queries:
        query_text = item.get("query", "")
        query_keywords = _extract_keywords(query_text)
        if not query_keywords:
            continue
        intersection = user_keywords & query_keywords
        overlap_score = len(intersection) / len(query_keywords)
        candidates.append({
            "query_id": item.get("query_id", ""),
            "score": round(overlap_score, 4),
            "matched_keywords": list(intersection),
        })
        if overlap_score > best_score:
            best_score = overlap_score
            best_item = item

    threshold = CONFIG.eval_min_score
    accepted = best_item is not None and best_score >= threshold

    eval_log_data["match_type"] = "fuzzy"
    eval_log_data["user_keywords"] = list(user_keywords)
    eval_log_data["candidates"] = candidates
    eval_log_data["matched_query_id"] = best_item.get("query_id") if best_item else None
    eval_log_data["score"] = round(best_score, 4)
    eval_log_data["threshold"] = threshold
    eval_log_data["accepted"] = accepted
    eval_log_data["counted"] = False

    if el:
        el.info("eval_match", extra=eval_log_data)

    return _build_result(best_item, "fuzzy", best_score, False) if accepted else None


def _build_mentioned_at_map(raw_results: list[dict]) -> dict[str, str]:
    """从召回结果提取 mentioned_at 映射。"""
    return {r["id"]: r["mentioned_at"] for r in raw_results if r.get("id") and r.get("mentioned_at")}


# ========== 四路 recall 辅助函数 ==========


def _do_hindsight_recall(query: str) -> dict | None:
    """执行 Hindsight recall，使用共享 Session。"""
    client = HindsightClient(CONFIG.hindsight_api_url, CONFIG.timeout_seconds)
    try:
        return client.recall(
            query,
            max_results=CONFIG.max_results * 3,
        )
    except Exception:
        raise
    finally:
        client.close()


def _do_kt_recall(session_id: str, query: str) -> list[dict]:
    """执行知识树 recall，有异常时返回空列表。"""
    if not HAS_KNOWLEDGE_TREE:
        _ensure_kt_imported()
    if not HAS_KNOWLEDGE_TREE:
        return []
    try:
        return _recall_knowledge_tree_raw(session_id, query)
    except Exception as e:
        logger.warning(
            "知识树 recall 异常（跳过）",
            extra={"session_id": session_id, "error": f"{type(e).__name__}: {e}"},
        )
        return []


def _do_skill_match(query: str) -> str:
    """执行 skill 匹配，返回注入文本或空字符串。"""
    from knowledge_navigation.core.skill_matcher import match_skills, strip_frontmatter

    try:
        matched = match_skills(query)
        if not matched:
            return ""
        lines: list[str] = ["", "<auto_loaded_skills>"]
        lines.append("以下技能与当前问题相关，已自动加载完整内容：")
        for s in matched:
            path = s["path"]
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read()
                body = strip_frontmatter(raw)
                truncated = len(body) > 4000
                content = body[:4000] if truncated else body
            except Exception:
                content = f"（无法读取 {s['name']}）"
                truncated = False
            lines.append(
                f"\n### {s['name']} (match={s['score']})\n"
                f"{content}"
            )
            if truncated:
                lines.append(
                    f"\u2139\ufe0f 该技能内容已截断，如需完整内容请调用 skill_view(name='{s['name']}') 加载。"
                )
        lines.append("\n</auto_loaded_skills>")
        logger.info(
            "Skill match: %s",
            [m["name"] for m in matched],
            extra={"event": "skill_match", "skills": [m["name"] for m in matched]},
        )
        return "\n".join(lines)
    except Exception as e:
        logger.debug("Skill match error: %s", e)
        return ""


def _do_sag_recall(query: str) -> list[dict]:
    """执行 SAG 文档检索（4th route），通过 REST API 调 /search。"""
    if sag_circuit_is_open():
        logger.debug("SAG 熔断器开启，跳过 recall")
        return []
    t0 = time.time()
    try:
        import requests as _req
        source_ids = [s.strip() for s in CONFIG.sag_source_ids.split(",") if s.strip()]
        payload = {
            "query": query,
            "topK": CONFIG.sag_search_top_k,
            "searchMode": "fast",
            "sourceIds": source_ids,
        }
        resp = _req.post(
            f"{CONFIG.sag_api_url}/search",
            json=payload,
            timeout=CONFIG.sag_search_timeout,
        )
        if resp.status_code != 200:
            sag_circuit_record_failure("service_error")
            logger.warning(
                "SAG recall HTTP %d, 耗时 %.1fms",
                resp.status_code, (time.time() - t0) * 1000,
            )
            return []
        data = resp.json()
        sections = data.get("sections", [])
        sag_circuit_record_success()
        logger.info(
            "SAG recall: %d sections, 耗时 %.1fms",
            len(sections), (time.time() - t0) * 1000,
            extra={"event": "sag_recall", "count": len(sections)},
        )
        return sections
    except Exception as e:
        sag_circuit_record_failure("exception")
        logger.debug("SAG recall 异常（跳过）: %s, 耗时 %.1fms", e, (time.time() - t0) * 1000)
        return []


# ========== 候选构建 ==========


def _normalize_kt_score(raw_score: Any) -> tuple[float, str]:
    """把知识树 score 统一映射到可比较的 final_score。"""
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return 0.45, "fallback"
    if score < 0:
        score = 0.0
    if score > 1.0:
        score = 1.0
    return round(0.5 + 0.4 * score, 4), "tree_score"


def _build_knowledge_tree_candidate(kp: dict[str, Any]) -> dict[str, Any] | None:
    """把知识树结果对齐到统一候选结构。"""
    text = (kp.get("text", "") or kp.get("name", "")).strip()
    if not text:
        return None
    final_score, score_source = _normalize_kt_score(kp.get("score"))
    return {
        "id": str(kp.get("id", "")),
        "text": text,
        "source": "knowledge_tree",
        "base_score": final_score,
        "tree_score": kp.get("score"),
        "final_score": final_score,
        "rerank_score": final_score,
        "score_source": score_source,
    }


def _candidate_score(result: dict[str, Any]) -> float:
    """统一读取候选最终分数。"""
    return extract_score(result)


# ========== 门控与路由 ==========


def _pass_gates(session_id: str, user_message: str, platform: str, is_first_turn: bool) -> tuple[bool, dict | None]:
    """三层门控 + eval bypass 判断。"""
    eval_match = _match_eval_query(user_message) if CONFIG.eval_match_enabled else None
    if eval_match:
        logger.info(
            "eval_query bypass gate",
            extra={
                "session_id": session_id,
                "eval_query_id": eval_match.get("query_id"),
                "event": "eval_query_bypass",
            },
        )
        return True, eval_match

    if skip_non_user(platform):
        logger.debug(
            "非用户平台跳过 pre_llm_call",
            extra={"session_id": session_id, "event": "skip_non_user"},
        )
        return False, None

    if skip_system_prompt(user_message, is_first_turn):
        logger.debug(
            "系统提示词跳过 pre_llm_call",
            extra={"session_id": session_id, "event": "skip_system_prompt"},
        )
        return False, None

    skip_reason = skip_pre_llm_call(user_message)
    if skip_reason:
        logger.debug(
            "turn_gate 跳过 pre_llm_call: %s",
            skip_reason,
            extra={"session_id": session_id, "event": "skip_operational"},
        )
        return False, None

    return True, None


def _get_router_mask(session_id: str, user_message: str) -> dict[str, bool]:
    """调用 Router 决策四路 mask，异常时 fallback 全开。"""
    try:
        timeout = int(os.getenv("KN_ROUTER_TIMEOUT", str(CONFIG.router_timeout)))
        mask = _router_route(
            session_id,
            user_message,
            CONFIG.router_model,
            CONFIG.router_api_url,
            CONFIG.router_api_key,
            timeout,
        )
        mask.setdefault("h", True)
        mask.setdefault("kt", True)
        mask.setdefault("s", True)
        mask.setdefault("sag", False)
    except Exception as e:
        logger.warning("Router 调用异常 (%s)，fallback 三路全开，SAG 关闭", e)
        mask = {"h": True, "kt": True, "s": True, "sag": False}
    logger.info(
        "Router mask: h=%s kt=%s s=%s sag=%s",
        mask["h"], mask["kt"], mask["s"], mask["sag"],
        extra={"session_id": session_id, "event": "router_mask", "mask": mask},
    )
    return mask


# ========== 执行 recall ==========


def _execute_recall(
    session_id: str,
    user_message: str,
    hs_active: bool,
    kt_active: bool,
    s_active: bool,
    sag_active: bool,
    active_count: int,
    t0: float,
    query_trunc: str,
    recall_logger: RecallLogger,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str, list[dict[str, Any]]]:
    """执行四路 recall（Hindsight + 知识树 + Skill + SAG），并行或串行。"""
    result: dict[str, Any] | None = None
    kt_raw_results: list[dict[str, Any]] = []
    skill_context = ""
    sag_raw_results: list[dict[str, Any]] = []

    if active_count >= 2:
        hs_future = _recall_executor.submit(_do_hindsight_recall, user_message) if hs_active else None
        kt_future = _recall_executor.submit(_do_kt_recall, session_id, user_message) if kt_active else None
        sk_future = _recall_executor.submit(_do_skill_match, user_message) if s_active else None
        sag_future = _recall_executor.submit(_do_sag_recall, user_message) if sag_active else None
        try:
            if hs_future is not None:
                _hs_t0 = time.time()
                try:
                    result = hs_future.result(timeout=CONFIG.timeout_seconds)
                    _hs_latency = (time.time() - _hs_t0) * 1000
                    _hs_results = result.get("results", []) if result else []
                    recall_logger.record("hindsight", _hs_results, _hs_latency, session_id=session_id, query=user_message)
                    if result is None:
                        circuit_record_failure("service_error")
                    else:
                        circuit_record_success()
                except FuturesTimeout:
                    hs_future.cancel()
                    circuit_record_failure("exception")
                    recall_logger.record("hindsight", [], (time.time() - _hs_t0) * 1000, session_id=session_id, query=user_message, error=f"timeout({CONFIG.timeout_seconds}s)")
                    logger.error("Hindsight recall 超时（%d 秒）", CONFIG.timeout_seconds, extra={"session_id": session_id, "query_trunc": query_trunc, "event": "recall_timeout"})
                except Exception as e:
                    circuit_record_failure("exception")
                    recall_logger.record("hindsight", [], (time.time() - _hs_t0) * 1000, session_id=session_id, query=user_message, error=f"{type(e).__name__}: {e}")
                    logger.error("Hindsight recall error", extra={"session_id": session_id, "query_trunc": query_trunc, "error": f"{type(e).__name__}: {e}", "event": "recall_error"})

            if kt_future is not None:
                remaining = max(0.1, CONFIG.timeout_seconds - (time.time() - t0))
                _kt_t0 = time.time()
                try:
                    kt_raw_results = kt_future.result(timeout=remaining)
                    _kt_latency = (time.time() - _kt_t0) * 1000
                    recall_logger.record("knowledge_tree", kt_raw_results, _kt_latency, session_id=session_id, query=user_message)
                except FuturesTimeout:
                    kt_future.cancel()
                    recall_logger.record("knowledge_tree", [], (time.time() - _kt_t0) * 1000, session_id=session_id, query=user_message, error=f"timeout({remaining:.1f}s)")
                    logger.warning("知识树 recall 超时（%.1f 秒）", remaining)
                    kt_raw_results = []
                except Exception as e:
                    recall_logger.record("knowledge_tree", [], (time.time() - _kt_t0) * 1000, session_id=session_id, query=user_message, error=f"{type(e).__name__}: {e}")
                    logger.warning("知识树 recall 异常（跳过）: %s", e)
                    kt_raw_results = []

            if sk_future is not None:
                _sk_t0 = time.time()
                try:
                    skill_context = sk_future.result(timeout=CONFIG.timeout_seconds)
                    _sk_latency = (time.time() - _sk_t0) * 1000
                    _sk_results = [{"id": "skill_context", "score": 1.0}] if skill_context else []
                    recall_logger.record("skill", _sk_results, _sk_latency, session_id=session_id, query=user_message)
                except Exception as e:
                    recall_logger.record("skill", [], (time.time() - _sk_t0) * 1000, session_id=session_id, query=user_message, error=f"{type(e).__name__}: {e}")
                    logger.debug("Skill match future error: %s", e)

            if sag_future is not None:
                sag_remaining = max(0.1, CONFIG.sag_search_timeout - (time.time() - t0))
                _sag_t0 = time.time()
                try:
                    sag_raw_results = sag_future.result(timeout=sag_remaining)
                    _sag_latency = (time.time() - _sag_t0) * 1000
                    recall_logger.record("sag", sag_raw_results, _sag_latency, session_id=session_id, query=user_message)
                except FuturesTimeout:
                    sag_future.cancel()
                    sag_circuit_record_failure("timeout")
                    recall_logger.record("sag", [], (time.time() - _sag_t0) * 1000, session_id=session_id, query=user_message, error=f"timeout({sag_remaining:.1f}s)")
                    logger.debug("SAG recall 超时（%.1f 秒）", sag_remaining)
                except Exception as e:
                    sag_circuit_record_failure("exception")
                    recall_logger.record("sag", [], (time.time() - _sag_t0) * 1000, session_id=session_id, query=user_message, error=f"{type(e).__name__}: {e}")
                    logger.debug("SAG recall future error: %s", e)
        finally:
            if hs_future is not None and not hs_future.done():
                hs_future.cancel()
            if kt_future is not None and not kt_future.done():
                kt_future.cancel()
            if sk_future is not None and not sk_future.done():
                sk_future.cancel()
            if sag_future is not None and not sag_future.done():
                sag_future.cancel()
    else:
        if hs_active:
            _hs_t0 = time.time()
            try:
                result = _do_hindsight_recall(user_message)
                _hs_latency = (time.time() - _hs_t0) * 1000
                _hs_results = result.get("results", []) if result else []
                recall_logger.record("hindsight", _hs_results, _hs_latency, session_id=session_id, query=user_message)
                if result is None:
                    circuit_record_failure("exception")
                else:
                    circuit_record_success()
            except Exception as e:
                circuit_record_failure("exception")
                recall_logger.record("hindsight", [], (time.time() - _hs_t0) * 1000, session_id=session_id, query=user_message, error=f"{type(e).__name__}: {e}")
                logger.error("Hindsight recall error", extra={"session_id": session_id, "query_trunc": query_trunc, "error": f"{type(e).__name__}: {e}", "event": "recall_error"})
                result = None
        if kt_active:
            _kt_t0 = time.time()
            try:
                kt_raw_results = _do_kt_recall(session_id, user_message)
                _kt_latency = (time.time() - _kt_t0) * 1000
                recall_logger.record("knowledge_tree", kt_raw_results, _kt_latency, session_id=session_id, query=user_message)
            except Exception as e:
                recall_logger.record("knowledge_tree", [], (time.time() - _kt_t0) * 1000, session_id=session_id, query=user_message, error=f"{type(e).__name__}: {e}")
                logger.warning("知识树 recall 异常（跳过）: %s", e)
                kt_raw_results = []
        if s_active:
            _sk_t0 = time.time()
            try:
                skill_context = _do_skill_match(user_message)
                _sk_latency = (time.time() - _sk_t0) * 1000
                _sk_results = [{"id": "skill_context", "score": 1.0}] if skill_context else []
                recall_logger.record("skill", _sk_results, _sk_latency, session_id=session_id, query=user_message)
            except Exception as e:
                recall_logger.record("skill", [], (time.time() - _sk_t0) * 1000, session_id=session_id, query=user_message, error=f"{type(e).__name__}: {e}")
                logger.debug("Skill match error: %s", e)
        if sag_active:
            _sag_t0 = time.time()
            try:
                sag_raw_results = _do_sag_recall(user_message)
                _sag_latency = (time.time() - _sag_t0) * 1000
                recall_logger.record("sag", sag_raw_results, _sag_latency, session_id=session_id, query=user_message)
            except Exception as e:
                sag_circuit_record_failure("exception")
                recall_logger.record("sag", [], (time.time() - _sag_t0) * 1000, session_id=session_id, query=user_message, error=f"{type(e).__name__}: {e}")
                logger.debug("SAG recall error: %s", e)

    return result, kt_raw_results, skill_context, sag_raw_results


# ========== 去重与预算 ==========


def _dedup_and_budget(
    kept: list[dict[str, Any]],
    session_id: str,
    skill_context: str,
) -> tuple[list[dict[str, Any]], str]:
    """Turn-to-turn 去重 + 文本去重 + Token 预算守门。"""
    _touch_injected_session(session_id)
    with _injected_lock:
        _session_history = _injected_ids[session_id]
        _turn_dedup_count = 0
        if CONFIG.turn_to_turn_dedup_mode == "demote":
            demoted = 0
            for r in kept:
                nid = str(r.get("id", ""))
                if nid and nid in _session_history:
                    demoted_score = _candidate_score(r) * 0.1
                    r["final_score"] = demoted_score
                    r["rerank_score"] = demoted_score
                    demoted += 1
            if demoted:
                logger.info("turn-to-turn 降权: %d 条已注入记忆分数降至 0.1x", demoted)
            kept.sort(key=_candidate_score, reverse=True)
        else:
            for r in list(kept):
                nid = str(r.get("id", ""))
                if nid and nid in _session_history:
                    kept.remove(r)
                    _turn_dedup_count += 1
            if _turn_dedup_count:
                logger.info("turn-to-turn 去重: 移除 %d 条已注入记忆", _turn_dedup_count)
        for r in kept:
            nid = str(r.get("id", ""))
            if nid:
                _session_history[nid] = time.time()
        if len(_session_history) > _INJECTED_LRU_MAX:
            for _ in range(_INJECTED_LRU_MAX // 2):
                _session_history.popitem(last=False)

    from knowledge_navigation.core.filtering import dedup_by_text as _dedup_by_text
    kept = _dedup_by_text(kept)

    if CONFIG.enable_token_budget:
        hs_list = [r for r in kept if r.get("source", "hindsight") == "hindsight"]
        sag_list = [r for r in kept if r.get("source") == "sag"]
        kt_list = [r for r in kept if r.get("source") == "knowledge_tree"]
        skill_list = []
        if skill_context:
            skill_list = [{"text": skill_context, "source": "skill", "final_score": 1.0}]

        hs_tokens_before = sum(estimate_tokens(str(r.get("text", ""))) for r in hs_list)
        sag_tokens_before = sum(estimate_tokens(str(r.get("text", ""))) for r in sag_list)
        kt_tokens_before = sum(estimate_tokens(str(r.get("text", ""))) for r in kt_list)
        skill_tokens_before = estimate_tokens(skill_context) if skill_context else 0

        hs_sag_combined = hs_list + sag_list
        hs_kept_tb, kt_kept_tb, skill_kept_tb = apply_token_budget(
            hs_sag_combined, kt_list, skill_list,
            CONFIG.token_budget_total,
            CONFIG.token_budget_hindsight_ratio,
            CONFIG.token_budget_kt_ratio,
            CONFIG.token_budget_skill_ratio,
        )

        hs_tokens_after = sum(estimate_tokens(str(r.get("text", ""))) for r in hs_kept_tb if r.get("source", "hindsight") == "hindsight")
        sag_tokens_after = sum(estimate_tokens(str(r.get("text", ""))) for r in hs_kept_tb if r.get("source") == "sag")
        kt_tokens_after = sum(estimate_tokens(str(r.get("text", ""))) for r in kt_kept_tb)
        skill_tokens_after = estimate_tokens(skill_kept_tb[0]["text"]) if skill_kept_tb else 0

        kept = hs_kept_tb + kt_kept_tb
        if skill_kept_tb:
            skill_context = skill_kept_tb[0]["text"]
        else:
            skill_context = ""

        logger.info(
            "Token budget: hs %d→%d, sag %d→%d, kt %d→%d, skill %d→%d (total budget=%d)",
            hs_tokens_before, hs_tokens_after,
            sag_tokens_before, sag_tokens_after,
            kt_tokens_before, kt_tokens_after,
            skill_tokens_before, skill_tokens_after,
            CONFIG.token_budget_total,
            extra={
                "event": "token_budget",
                "hs_tokens_before": hs_tokens_before,
                "hs_tokens_after": hs_tokens_after,
                "sag_tokens_before": sag_tokens_before,
                "sag_tokens_after": sag_tokens_after,
                "kt_tokens_before": kt_tokens_before,
                "kt_tokens_after": kt_tokens_after,
                "skill_tokens_before": skill_tokens_before,
                "skill_tokens_after": skill_tokens_after,
                "total_budget": CONFIG.token_budget_total,
            },
        )

    return kept, skill_context


def _expand_multi_hop(
    kt_raw_results: list[dict[str, Any]],
    kt_active: bool,
    session_id: str,
) -> list[dict[str, Any]]:
    """多跳关联展开。"""
    if not kt_raw_results or not kt_active:
        return kt_raw_results
    try:
        _seed_ids = [int(r["id"]) for r in kt_raw_results if r.get("id") and str(r["id"]).isdigit()]
        if _seed_ids:
            _mh_results = _multi_hop_recall(_seed_ids, top_k=2)
            if _mh_results:
                logger.info("多跳关联展开: %d 条", len(_mh_results), extra={"session_id": session_id, "event": "multi_hop_expand", "count": len(_mh_results)})
                _existing_ids = {r.get("id") for r in kt_raw_results}
                for _mh in _mh_results:
                    if _mh.get("id") not in _existing_ids:
                        _mh["source"] = "multi-hop"
                        kt_raw_results.append(_mh)
                        _existing_ids.add(_mh.get("id"))
    except Exception as e:
        logger.debug("多跳 recall 异常（跳过）: %s", e)
    return kt_raw_results


# ========== XML 输出组装 ==========


def _assemble_xml_output(
    kept: list[dict[str, Any]],
    skill_context: str,
    session_id: str,
    user_message: str,
    ctx: dict[str, Any],
) -> str | None:
    """组装 XML 标签化上下文 + 记录日志 + 返回最终字符串。"""
    query_trunc = ctx["query_trunc"]
    eval_match = ctx["eval_match"]
    raw_results = ctx["raw_results"]
    latency_ms = ctx["latency_ms"]
    kt_raw_results = ctx["kt_raw_results"]
    excluded_count = ctx["excluded_count"]
    kept_before_kt = ctx["kept_before_kt"]
    kt_dedup_removed = ctx["kt_dedup_removed"]
    score_comparison = ctx["score_comparison"]
    summary = ctx["summary"]
    kept_before_compress = ctx.get("kept_before_compress", len(kept))

    hs_kept = [r for r in kept if r.get("source", "hindsight") == "hindsight"]
    kt_kept = [r for r in kept if r.get("source") == "knowledge_tree"]
    sag_kept = [r for r in kept if r.get("source") == "sag"]

    context_lines: list[str] = []

    context_lines.append(f"<user_query>\n{html.escape(user_message, quote=False)}\n</user_query>")

    if hs_kept:
        avg_score = sum(_candidate_score(r) for r in hs_kept) / max(len(hs_kept), 1)
        hs_xml = "\n".join(
            f'  <memory source="hindsight" node_id="{html.escape(str(r.get("id", "")), quote=True)}">'
            f'{html.escape(str(r.get("text", ""))[:CONFIG.max_text_length], quote=False)}</memory>'
            for r in hs_kept
        )
        context_lines.append(
            f'<recalled_memory source="hindsight" count="{len(hs_kept)}" score_avg="{avg_score:.2f}">\n'
            f"{hs_xml}\n"
            f"</recalled_memory>"
        )

    if kt_kept:
        kt_xml = "\n".join(
            f'  <memory source="knowledge_tree" node_id="{html.escape(str(r.get("id", "")), quote=True)}">'
            f'{html.escape(str(r.get("text", ""))[:CONFIG.max_text_length], quote=False)}</memory>'
            for r in kt_kept
        )
        context_lines.append(
            f'<knowledge source="knowledge_tree" count="{len(kt_kept)}">\n'
            f"{kt_xml}\n"
            f"</knowledge>"
        )

    if sag_kept:
        sag_xml = "\n".join(
            f'  <memory source="sag" document_id="{html.escape(str(r.get("document_id", "")), quote=True)}">'
            f'{html.escape(str(r.get("text", ""))[:CONFIG.max_text_length], quote=False)}</memory>'
            for r in sag_kept
        )
        context_lines.append(
            f'<knowledge source="sag" count="{len(sag_kept)}"\n'
            f"{sag_xml}\n"
            f"</knowledge>"
        )

    if summary:
        context_lines.append(summary)

    context_lines.append(
        f"<system_state>\n"
        f"pwd: {os.getcwd()}\n"
        f"time: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"</system_state>"
    )

    recalled_ids = [m.get("id", "") for m in kept if m.get("id")]

    _recall_logger = ctx.get("recall_logger")
    if _recall_logger is not None:
        _compressed_from = kept_before_compress if CONFIG.enable_score_span_compress and kept_before_compress > len(kept) else None
        _compressed_to = len(kept) if _compressed_from is not None else None
        _summary_round = _task_tracker.current_round(session_id) if summary else None
        log_extra = _recall_logger.summary(
            kept_results=kept, session_id=session_id, query_trunc=query_trunc,
            excluded_count=excluded_count, kt_dedup_removed=kt_dedup_removed,
            total_chars=sum(len(line) for line in context_lines), injected_count=len(context_lines),
            score_comparison=score_comparison, eval_match=eval_match,
            total_latency_ms=latency_ms, compressed_from=_compressed_from, compressed_to=_compressed_to,
            task_summary_round=_summary_round, has_knowledge_tree=len(kt_raw_results) > 0,
        )
        log_extra["dropped_results"] = len(raw_results) - kept_before_kt
    else:
        log_extra: dict[str, Any] = {
            "session_id": session_id, "query_trunc": query_trunc,
            "event": "recall_success", "total_results": len(raw_results),
            "excluded_marked": excluded_count, "kept_results": len(kept),
            "dropped_results": len(raw_results) - kept_before_kt,
            "score_stats": calculate_score_stats([_candidate_score(r) for r in kept]),
            "injected_count": len(context_lines), "total_chars": sum(len(line) for line in context_lines),
            "has_knowledge_tree": len(kt_raw_results) > 0, "kt_dedup_removed": kt_dedup_removed,
            "latency_ms": latency_ms, "score_comparison": score_comparison,
            "recalled_ids": recalled_ids, "hs_kept": len(hs_kept), "kt_kept": len(kt_kept), "sag_kept": len(sag_kept),
        }
        if CONFIG.enable_score_span_compress and kept_before_compress > len(kept):
            log_extra["compressed_from"] = kept_before_compress
            log_extra["compressed_to"] = len(kept)
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
        if summary:
            log_extra["task_summary_round"] = _task_tracker.current_round(session_id)
        logger.info("recall success", extra=log_extra)

    if skill_context:
        context_lines.append(skill_context)

    if not context_lines:
        return None
    return "\n".join(context_lines)


# ========== 后处理 ==========


def _post_process_recall(
    result: dict[str, Any] | None,
    kt_raw_results: list[dict[str, Any]],
    hs_active: bool,
    kt_active: bool,
    skill_context: str,
    session_id: str,
    user_message: str,
    query_trunc: str,
    t0: float,
    eval_match_param: dict | None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """后处理：降级 + 过滤 + boost + 因果链 + 压缩 + 跨域去重 + KT对齐。"""
    if not result and hs_active:
        if not kt_raw_results:
            logger.info("recall empty (Hindsight + KT)", extra={"session_id": session_id, "query_trunc": query_trunc, "event": "recall_empty", "latency_ms": int((time.time() - t0) * 1000)})
            return None, {}
        logger.info("Hindsight 无结果，使用 KT-only fallback", extra={"session_id": session_id, "query_trunc": query_trunc, "event": "hindsight_fail_kt_fallback", "kt_count": len(kt_raw_results)})
        result = {"results": [], "trace": {}}

    if result is None:
        result = {"results": [], "trace": {}}

    latency_ms = int((time.time() - t0) * 1000)
    raw_results = result.get("results", [])

    if not raw_results and not kt_raw_results and not skill_context:
        if hs_active:
            circuit_record_success()
        logger.info("recall empty results", extra={"session_id": session_id, "query_trunc": query_trunc, "event": "recall_empty_results", "latency_ms": latency_ms})
        return None, {}

    if raw_results:
        circuit_record_success()

    filtered_raw, excluded_count = exclude_marked(raw_results) if raw_results else ([], 0)
    trace_data = result.get("trace", {})
    rerank_map = extract_rerank_scores(trace_data) if raw_results else {}

    if rerank_map and filtered_raw:
        for r in filtered_raw:
            nid = r.get("id", "")
            score = rerank_map.get(nid)
            if score is not None:
                r.setdefault("score", score)
                r.setdefault("rerank_score", score)

    if filtered_raw:
        _hit_counter.boost_scores(filtered_raw, rerank_map)

    if CONFIG.enable_causal_chain and rerank_map:
        try:
            _causal_boost(filtered_raw, rerank_map, CONFIG.causal_boost_alpha, CONFIG.causal_boost_cap)
        except Exception:
            logger.debug("causal_boost 异常（非关键路径，跳过）")

    effective_max = _compaction.get_effective_max_results(session_id, CONFIG.max_results)
    mentioned_at_map = _build_mentioned_at_map(filtered_raw)
    kept, all_scores, score_comparison = filter_by_score(
        filtered_raw, rerank_map,
        min_score=CONFIG.min_score,
        max_results=effective_max,
        enable_temporal=CONFIG.enable_temporal,
        mentioned_at_map=mentioned_at_map,
    )

    kept_before_compress = len(kept)

    if CONFIG.enable_score_span_compress and kept:
        from knowledge_navigation.core.filtering import extract_ce_raw_scores, compress_by_score_span
        ce_raw_map = extract_ce_raw_scores(trace_data)
        kept = compress_by_score_span(kept, ce_raw_map, effective_max, CONFIG.score_span_top3_threshold, CONFIG.score_span_half_threshold)
    kept_before_kt = len(kept)

    if eval_match_param is None:
        eval_match = _match_eval_query(user_message) if CONFIG.eval_match_enabled else None
    else:
        eval_match = eval_match_param

    summary = _task_tracker.get_summary_prompt(session_id)

    if not kept and summary is None:
        if not kt_active:
            return None, {}

    kt_dedup_removed = 0
    if kt_raw_results and kept:
        dedup_action = CONFIG.cross_domain_dedup_action
        dedup_demote_factor = CONFIG.cross_domain_dedup_demote_factor
        if CONFIG.cross_domain_dedup_mode == "text_embedding":
            kt_raw_results, kt_dedup_removed = cross_domain_dedup(
                hindsight_results=kept, kt_results=kt_raw_results,
                threshold=0.65, embed_fn=_batch_embed,
                action=dedup_action, demote_factor=dedup_demote_factor,
            )
        else:
            kt_raw_results, kt_dedup_removed = cross_domain_dedup(
                hindsight_results=kept, kt_results=kt_raw_results,
                threshold=0.65, embed_fn=None,
                action=dedup_action, demote_factor=dedup_demote_factor,
            )
        if kt_dedup_removed:
            logger.info("跨域去重（%s）移除 %d 条知识树重复结果", dedup_action, kt_dedup_removed, extra={"session_id": session_id, "kt_dedup_removed": kt_dedup_removed})

    for kp in kt_raw_results:
        candidate = _build_knowledge_tree_candidate(kp)
        if candidate:
            kept.append(candidate)

    return kept, {
        "latency_ms": latency_ms,
        "raw_results": raw_results,
        "excluded_count": excluded_count,
        "kept_before_kt": kept_before_kt,
        "kt_dedup_removed": kt_dedup_removed,
        "score_comparison": score_comparison,
        "kept_before_compress": kept_before_compress,
        "summary": summary,
        "eval_match": eval_match,
        "kt_raw_results": kt_raw_results,
    }


# ========== 主入口 ==========


def pre_llm_call(session_id: str, user_message: str, **kwargs: Any) -> str | None:
    """每次 LLM 调用前自动执行：三层门控 → LLM Router → 三路 mask 条件执行 → 后处理注入。"""

    _should_continue, _eval_match = _pass_gates(
        session_id, user_message,
        kwargs.get("platform", ""),
        kwargs.get("is_first_turn", False),
    )
    if not _should_continue:
        return None

    _hs_circuit_open = False
    if circuit_is_open():
        logger.info("熔断器跳过 Hindsight recall，知识树仍尝试")
        _hs_circuit_open = True

    mask = _get_router_mask(session_id, user_message)

    if not any(mask.values()):
        logger.info("Router 全关闭，跳过所有 recall", extra={"session_id": session_id, "event": "skip_router_all_off"})
        return None

    t0 = time.time()
    query_trunc = user_message[:60]

    _hs_active = mask["h"] and not _hs_circuit_open
    _kt_active = mask["kt"] and (HAS_KNOWLEDGE_TREE or _ensure_kt_imported())
    _s_active = mask["s"]
    _sag_active = mask.get("sag", False)
    _active_count = sum([_hs_active, _kt_active, _s_active, _sag_active])

    _recall_logger = RecallLogger(use_logger=_get_use_logger())

    result, kt_raw_results, _skill_context, sag_raw_results = _execute_recall(
        session_id, user_message,
        _hs_active, _kt_active, _s_active, _sag_active, _active_count,
        t0, query_trunc, _recall_logger,
    )

    kt_raw_results = _expand_multi_hop(kt_raw_results, _kt_active, session_id)

    _pp_result, _pp_meta = _post_process_recall(
        result, kt_raw_results, _hs_active, _kt_active, _skill_context,
        session_id, user_message, query_trunc, t0, _eval_match,
    )
    if _pp_result is None:
        if sag_raw_results and _sag_active:
            kept = []
            latency_ms = int((time.time() - t0) * 1000)
            raw_results = []
            excluded_count = 0
            kept_before_kt = 0
            kt_dedup_removed = 0
            score_comparison = {}
            kept_before_compress = 0
            summary = None
            eval_match = _eval_match
        else:
            return _skill_context if _skill_context else None
    else:
        kept = _pp_result
        latency_ms = _pp_meta["latency_ms"]
        raw_results = _pp_meta["raw_results"]
        excluded_count = _pp_meta["excluded_count"]
        kept_before_kt = _pp_meta["kept_before_kt"]
        kt_dedup_removed = _pp_meta["kt_dedup_removed"]
        score_comparison = _pp_meta["score_comparison"]
        kept_before_compress = _pp_meta.get("kept_before_compress", len(kept))
        summary = _pp_meta.get("summary")
        eval_match = _pp_meta.get("eval_match", _eval_match)
        kt_raw_results = _pp_meta.get("kt_raw_results", kt_raw_results)

    if sag_raw_results and _sag_active:
        sag_count = 0
        sag_candidates: list[dict[str, Any]] = []
        for sec in sag_raw_results:
            try:
                raw_score = float(sec.get("score", 0.5))
            except (TypeError, ValueError):
                raw_score = 0.5
            content = sec.get("content", "")
            heading = sec.get("heading", "")
            if len(content) > CONFIG.sag_pointer_threshold:
                preview = content[:80].replace("\n", " ").strip()
                text = (
                    f"[SAG 指针] heading: {heading} | score: {raw_score:.2f} | "
                    f"preview: {preview}... | "
                    f"如需完整内容，使用 sag_search 工具查询: {heading}"
                )
            else:
                text = content
            candidate = {
                "id": sec.get("chunkId", f"sag_{sag_count}"),
                "text": text,
                "score": raw_score,
                "base_score": raw_score,
                "final_score": raw_score,
                "rerank_score": raw_score,
                "source": "sag",
                "heading": heading,
                "document_id": sec.get("documentId", ""),
            }
            if candidate["final_score"] >= CONFIG.sag_min_score:
                sag_candidates.append(candidate)
                sag_count += 1
        sag_candidates.sort(key=lambda x: x["final_score"], reverse=True)
        sag_candidates = sag_candidates[: CONFIG.sag_max_inject]
        kept.extend(sag_candidates)

        logger.info("SAG recall: %d sections merged (capped to %d)", sag_count, len(sag_candidates), extra={"session_id": session_id, "event": "sag_merge", "count": len(sag_candidates)})

    kept, _skill_context = _dedup_and_budget(kept, session_id, _skill_context)

    return _assemble_xml_output(kept, _skill_context, session_id, user_message, {
        "query_trunc": query_trunc,
        "eval_match": eval_match,
        "raw_results": raw_results,
        "latency_ms": latency_ms,
        "kt_raw_results": kt_raw_results,
        "excluded_count": excluded_count,
        "kept_before_kt": kept_before_kt,
        "kt_dedup_removed": kt_dedup_removed,
        "score_comparison": score_comparison,
        "summary": summary,
        "kept_before_compress": kept_before_compress,
        "recall_logger": _recall_logger,
    })