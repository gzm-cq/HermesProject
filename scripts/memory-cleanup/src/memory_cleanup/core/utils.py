"""公共工具函数 — 跨模块复用的工具逻辑。

提取自 verifier.py、llm_client.py、memory_store.py、classifier.py 中的重复代码。
"""

from __future__ import annotations

import re
from typing import Any  # noqa: F401 — kept for type annotations in other modules


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

# ========== 完成 ==========
