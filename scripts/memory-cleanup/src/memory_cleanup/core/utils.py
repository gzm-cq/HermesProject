"""公共工具函数 — 跨模块复用的工具逻辑。

提取自 verifier.py、llm_client.py、memory_store.py、classifier.py 中的重复代码。
"""

from __future__ import annotations

import re
from typing import Any


# ========== 关键词提取正则 ==========
# 统一使用：中文 2 字以上、英文 4 字母以上
KEYWORD_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}")


def extract_keywords(text: str) -> list[str]:
    """提取关键词（中文 2 字+，英文 4 字母+）。

    统一了 classifier.py、verifier.py、memory_store.py、session_db.py 中的关键词提取逻辑。
    """
    return KEYWORD_PATTERN.findall(text)


# ========== 文本截断 ==========

def truncate_text(text: str, max_len: int = 400, suffix: str = "…（截断）") -> str:
    """截断文本到指定长度，超长时追加后缀。

    统一了 verifier.py 和 llm_client.py 中的 _truncate 函数。
    """
    if len(text) <= max_len:
        return text
    # 尝试在换行边界截断
    truncated = text[:max_len]
    last_newline = truncated.rfind("\n")
    if last_newline > max_len // 2:
        truncated = truncated[:last_newline]
    return truncated + suffix


# ========== 去重索引收集 ==========

def collect_remove_indices(result: dict[str, Any]) -> set[int]:
    """从清理结果中收集所有需要删除的索引（含 merge/compress/hindsight 覆盖）。

    统一了 cli.py 两处和 reporter.py 中的重复逻辑。
    """
    remove_indices: set[int] = set()
    for item in result.get("merge", []):
        if isinstance(item, dict) and "indices" in item:
            remove_indices.update(item["indices"])
    for item in result.get("compress", []):
        if isinstance(item, dict) and "indices" in item:
            remove_indices.update(item["indices"])
    # hindsight 覆盖的索引也算删除
    for item in result.get("hindsight", []):
        if isinstance(item, dict) and "indices" in item:
            remove_indices.update(item["indices"])
    return remove_indices


# ========== corrected_text 校验 ==========

def validate_corrected_text(
    original: str,
    corrected: str,
    keyword_overlap_threshold: float = 0.3,
    char_overlap_threshold: float = 0.5,
) -> tuple[bool, dict[str, float]]:
    """校验 corrected_text 是否有实质修正。

    统一了 verifier.py 和 memory_store.py 中的重复逻辑。

    Returns:
        (has_real_fix, overlap_details) — has_real_fix 为 True 表示有实质修正；
        overlap_details 包含 kw_overlap、char_overlap、effective_overlap 字段。
    """
    if not corrected or not original:
        return False, {"kw_overlap": 0.0, "char_overlap": 0.0, "effective_overlap": 0.0}

    orig_kws = set(extract_keywords(original))
    corr_kws = set(extract_keywords(corrected))

    if orig_kws:
        kw_overlap = len(orig_kws & corr_kws) / len(orig_kws)
    else:
        kw_overlap = 1.0 if not corr_kws else 0.5

    orig_lower = original.lower()
    corr_lower = corrected.lower()
    if orig_lower:
        char_overlap = sum(1 for c in corr_lower if c in orig_lower) / max(len(corr_lower), 1)
    else:
        char_overlap = 0.0

    effective_overlap = kw_overlap * 0.6 + char_overlap * 0.4
    has_real_fix = (
        kw_overlap >= keyword_overlap_threshold
        and char_overlap >= char_overlap_threshold
        and corrected.strip() != original.strip()
    )

    return has_real_fix, {
        "kw_overlap": kw_overlap,
        "char_overlap": char_overlap,
        "effective_overlap": effective_overlap,
    }
