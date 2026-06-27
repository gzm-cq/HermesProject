"""验证器 — Phase 2 session_search + LLM 验证 remove 候选（串行逐条，稳健版）。"""

import datetime
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory_cleanup.adapters.llm_client import LLMClient
    from memory_cleanup.adapters.session_db import SessionDB

logger = logging.getLogger(__name__)


def _truncate(text: str, max_len: int = 400) -> str:
    """在合理边界截断文本，避免在关键信息中间截断。"""
    if len(text) <= max_len:
        return text
    suffix = "…（截断）"
    truncated = text[:max_len - len(suffix)]
    last_newline = truncated.rfind("\n")
    if last_newline > (max_len - len(suffix)) // 2:
        truncated = truncated[:last_newline]
    return truncated + suffix


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

        sess = session_db.search(text)
        confidence = float(sess.get("confidence", 0.0) or 0.0)
        sess_text = str(sess.get("snippet", "无相关会话")) if sess.get("found") else "无相关会话"
        sess_ts = sess.get("timestamp", 0)

        # 时间窗口软降权
        if sess_ts and confidence >= 0.3:
            date_match = re.search(r"20\d{2}[-/年]\d{1,2}[-/月]\d{0,2}", text)
            if date_match:
                try:
                    date_str = date_match.group().replace("年", "-").replace("月", "-").replace("/", "-")
                    parts = [int(p) for p in date_str.split("-") if p]
                    if len(parts) >= 2:
                        entry_date = datetime.date(parts[0], parts[1], parts[2] if len(parts) > 2 else 1)
                        sess_date = datetime.date.fromtimestamp(float(sess_ts))
                        days_diff = abs((entry_date - sess_date).days)
                        if days_diff > 90:
                            confidence = round(confidence * 0.5, 2)
                except (ValueError, OSError):
                    pass

        snippet_for_llm: str | None = sess_text if confidence >= 0.3 else None
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
                result["note"] = f"corrected_text 无效（overlap={effective_overlap:.2f}，kw={kw_overlap:.2f} char={char_overlap:.2f if total_kw < 3 else kw_overlap:.2f}），降级为 correct"

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