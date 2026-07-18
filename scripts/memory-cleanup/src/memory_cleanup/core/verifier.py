"""验证器 — Phase 2 session_search + LLM 验证 remove 候选（串行逐条，稳健版）。"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory_cleanup.adapters.llm_client import LLMClient
    from memory_cleanup.adapters.session_db import SessionDB

logger = logging.getLogger(__name__)

# ── Phase 2 验证阈值常量（P2-MC-022） ──
_SESSION_CONFIDENCE_THRESHOLD = 0.3
_TIME_WINDOW_DAYS = 90
_TIME_DECAY_FACTOR = 0.5


def phase2_verify(
    entries: list[str],
    remove_list: list[dict[str, Any]],
    source: str,
    llm_client: "LLMClient",
    session_db: "SessionDB",
    max_workers: int = 8,
) -> dict[str, list]:
    """对 remove 候选逐条执行 session_search → LLM 判断（并行）。

    每候选独立 LLM 调用，确保 LLM 输出质量。不批量（批量 prompt 易导致 LLM 输出格式混乱）。

    Args:
        entries: 完整条目列表
        remove_list: 需验证的 remove 候选
        source: "MEMORY.md" 或 "USER.md"
        llm_client: LLMClient 实例
        session_db: SessionDB 实例
        max_workers: 并发线程数

    Returns:
        {"correct": [...], "corrected": [...], "keep": [...]}
    """
    if not remove_list:
        return {"correct": [], "corrected": [], "keep": []}

    print(f"\n  Phase 2: {source} - {len(remove_list)} 条...", flush=True)

    tasks: list[tuple[int, str, str, str, str | None, float]] = []
    for r in remove_list:
        idx = r.get("index", -1)
        if idx < 0 or idx >= len(entries):
            continue
        text = entries[idx]
        reason = r.get("原因", "")

        try:
            sess = session_db.search(text)
        except Exception as e:
            # session_db.search 异常不应该导致整个 verify 流程失败
            # 降级为"未找到相关会话"，让 LLM 单独判断
            logger.warning("session_db.search 异常，降级处理 (text=%s): %s", text[:50], e)
            sess = {"found": False, "confidence": 0.0, "snippet": "", "timestamp": 0}
        confidence = float(sess.get("confidence", 0.0) or 0.0)
        sess_text = str(sess.get("snippet", "无相关会话")) if sess.get("found") else "无相关会话"
        sess_ts = sess.get("timestamp", 0)

        # 时间窗口软降权
        if sess_ts and confidence >= _SESSION_CONFIDENCE_THRESHOLD:
            date_match = re.search(r"20\d{2}[-/年]\d{1,2}[-/月]\d{0,2}", text)
            if date_match:
                try:
                    date_str = date_match.group().replace("年", "-").replace("月", "-").replace("/", "-")
                    parts = [int(p) for p in date_str.split("-") if p]
                    if len(parts) >= 2:
                        entry_date = date(parts[0], parts[1], parts[2] if len(parts) > 2 else 1)
                        sess_date = date.fromtimestamp(float(sess_ts))
                        days_diff = abs((entry_date - sess_date).days)
                        if days_diff > _TIME_WINDOW_DAYS:
                            confidence = round(confidence * _TIME_DECAY_FACTOR, 2)
                except (ValueError, OSError):
                    pass

        snippet_for_llm: str | None = sess_text if confidence >= _SESSION_CONFIDENCE_THRESHOLD else None
        tasks.append((idx, text, reason, source, snippet_for_llm, confidence))

    results: dict[str, list] = {"correct": [], "corrected": [], "keep": []}
    n = len(tasks)

    def _verify_one(task: tuple[int, str, str, str, str | None, float]) -> dict[str, Any]:
        idx, text, reason, src, snippet, confidence = task
        result = llm_client.verify_one(idx, text, reason, src, snippet)
        result["confidence"] = confidence

        # 校验 corrected_text 有效性
        if result.get("verdict") == "corrected":
            corrected = (result.get("corrected_text") or "").strip()
            orig_kw = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", text))
            corr_kw = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", corrected))
            kw_overlap = len(orig_kw & corr_kw) / max(len(orig_kw), 1) if corrected else 0
            # 对于英文主导（关键词太少）的场景，回退到字符级重叠检查
            total_kw = max(len(orig_kw), len(corr_kw))
            char_overlap = kw_overlap  # 默认值，当 total_kw >= 3 时用 kw_overlap
            if total_kw < 3 and corrected:
                orig_chars = set(text.lower())
                corr_chars = set(corrected.lower())
                char_overlap = len(orig_chars & corr_chars) / max(len(orig_chars | corr_chars), 1)
                effective_overlap = char_overlap
            else:
                effective_overlap = kw_overlap
            has_real_fix = (
                corrected
                and len(corrected) > 10
                and corrected != text[: len(corrected)]
                and "修正" not in corrected[:20]
                and "需补充" not in corrected[:20]
                and effective_overlap > 0.2
            )
            if not has_real_fix:
                result["verdict"] = "correct"
                result.pop("corrected_text", None)
                _char_val = char_overlap if total_kw < 3 else kw_overlap
                result["note"] = f"corrected_text 无效（overlap={effective_overlap:.2f}，kw={kw_overlap:.2f} char={_char_val:.2f}），降级为 correct"

        return {"index": idx, "original": text, "session_snippet": snippet or "", **result}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_verify_one, t) for t in tasks]
        for f in as_completed(futures):
            entry = f.result()
            verdict = entry.get("verdict", "keep")
            if verdict == "correct":
                results["correct"].append(entry)
            elif verdict == "corrected":
                results["corrected"].append(entry)
            else:
                results["keep"].append(entry)

    print(
        f"     {source} 完成: "
        f"correct={len(results['correct'])} "
        f"corrected={len(results['corrected'])} "
        f"keep={len(results['keep'])}",
        flush=True,
    )
    return results