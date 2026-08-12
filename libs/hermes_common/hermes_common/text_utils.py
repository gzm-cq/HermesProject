"""文本处理公共工具 — 关键词提取、CJK 处理等。

集中封装原本散落在 hooks.py / recall.py / skill_matcher.py 三处的关键词提取逻辑，
通过参数差异化配置各调用方需求：
  - hooks._extract_keywords: 用于 eval query 匹配（英文 + CJK 2-gram，保守）
  - recall._extract_keywords: 用于知识树科目定位（英文 >=3 字符 + CJK 2-gram）
  - skill_matcher._extract_keywords: 用于 skill 预筛选（英文 + 中文整段 + 2-gram，激进）

现作为统一共享库 hermes_common 的一部分，被脚本层与插件层共同复用。
"""

from __future__ import annotations

import re

# 中文停用字（用于 2-gram 首字过滤）
CJK_STOP_CHARS = frozenset(
    "的了在是有和就不人都也到说要去会着这他那她它那些吗吧呢啊哦嗯嘛"
)


def extract_keywords(
    text: str,
    *,
    min_en_length: int = 2,
    include_cjk_bigrams: bool = True,
    include_cjk_full: bool = False,
    stop_chars: frozenset[str] | None = None,
    en_pattern: str = r"[a-zA-Z][a-zA-Z0-9_\-\.]*",
) -> set[str]:
    """通用关键词提取。

    Args:
        text: 输入文本
        min_en_length: 英文 token 最短字符数（默认 2）
        include_cjk_bigrams: 是否包含 CJK 2-gram（默认 True）
        include_cjk_full: 是否包含原始 CJK 段落文本（skill_matcher 需要）
        stop_chars: 2-gram 首字停用字集（None 使用默认 CJK_STOP_CHARS）
        en_pattern: 英文识别正则

    Returns:
        去重后的关键词集合，全部小写
    """
    if stop_chars is None:
        stop_chars = CJK_STOP_CHARS

    keywords: set[str] = set()

    # 英文 token
    for token in re.findall(en_pattern, text):
        if len(token) >= min_en_length:
            keywords.add(token.lower())

    # CJK 处理
    if include_cjk_bigrams or include_cjk_full:
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)

        if include_cjk_bigrams:
            for i in range(len(cjk_chars) - 1):
                if cjk_chars[i] not in stop_chars:
                    keywords.add(cjk_chars[i] + cjk_chars[i + 1])

        if include_cjk_full:
            # skill_matcher 场景：整段 CJK 连续序列
            for match in re.findall(r"[\u4e00-\u9fff]+", text):
                if len(match) >= 2:
                    keywords.add(match)

    return keywords
