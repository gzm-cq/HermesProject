"""对话轮次门控 — 判断当前轮次是否值得执行 recall / 提取

纯字符串/正则运算，零 API 调用，单次判定 < 0.1ms。
供 knowledge-navigation (pre_llm_call) 和 knowledge-tree-plugin (post_llm_call) 共用。

三层门控：
    1. 来源门控：仅放行用户平台（cli/飞书/微信），内部管线全跳过
    2. 系统提示词门控：放行的平台中，第一轮长英文消息 → 系统生成，跳过
    3. 文本门控：放行的用户对话中按消息/响应内容判断是否跳过

返回值约定：
    ""       = 不跳过，正常执行
    "<原因>" = 跳过，原因字符串由调用方用自己的 logger 记录
"""

from __future__ import annotations

import re
from typing import Final

# ========== 来源门控 ==========

# 仅放行这三类用户来源，其余（curator / subagent / cron 等）全跳过
_PLATFORM_ALLOWLIST: Final[frozenset[str]] = frozenset({"cli", "feishu", "weixin"})


def skip_non_user(source_platform: str) -> bool:
    """来源门控：判断是否为非用户平台（内部管线），是则跳过。

    在 pre_llm_call / post_llm_call 最开头调用。
    返回 True 时调用方应直接 return，不执行任何 recall / 提取操作。

    Args:
        source_platform: kwargs 中的 platform 值。

    Returns:
        True = 非用户平台（跳过），False = 用户平台（继续）。
    """
    return source_platform not in _PLATFORM_ALLOWLIST


# ========== 系统提示词门控 ==========

# 系统生成的任务提示词的英文占比较低阈值
_SYSTEM_PROMPT_MIN_EN_RATIO: Final[float] = 0.5
_SYSTEM_PROMPT_MIN_LENGTH: Final[int] = 200


def _english_ratio(text: str) -> float:
    """计算文本中英文字符（不含空格/换行）的占比。"""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    en_count = sum(1 for c in chars if "a" <= c <= "z" or "A" <= c <= "Z")
    return en_count / len(chars)


def skip_system_prompt(user_message: str, is_first_turn: bool) -> bool:
    """系统提示词门控：第一轮的长英文消息 → 系统生成的内部 prompt，跳过。

    子代理任务、Cron job、Review skill 等的 user_message 都是系统构造的，
    特征一致：单轮（is_first_turn=True）+ 长消息（>200字）+ 英文主导。

    用户的正常第一轮消息如果是中文或混合语言，不会被误判。

    Args:
        user_message: 本轮消息原文（预处理前）。
        is_first_turn: 是否第一轮（来自 hook kwargs）。

    Returns:
        True = 系统 prompt（跳过），False = 可能是用户消息（继续）。
    """
    if not is_first_turn:
        return False

    if len(user_message) < _SYSTEM_PROMPT_MIN_LENGTH:
        return False

    return _english_ratio(user_message) >= _SYSTEM_PROMPT_MIN_EN_RATIO


# ========== 文本门控 ==========

# 操作型命令前缀（模块加载时预计算小写，避免循环内重复 .lower()）
_OPERATIONAL_PREFIXES: Final[tuple[str, ...]] = (
    "读", "看", "查", "搜索", "找",
    "执行", "运行", "部署",
    "重启", "停止", "启动", "杀",
    "巡检", "审计", "审查", "review",
    "检查", "配置", "修改", "设置",
    "创建", "删除", "写入", "修复", "修正",
    "添加", "移除", "清理",
)
_OPERATIONAL_PREFIXES_LOWER: Final[tuple[str, ...]] = tuple(
    p.lower() for p in _OPERATIONAL_PREFIXES
)

# 纯确认/简短响应
_CONFIRM_PATTERN: Final[re.Pattern] = re.compile(
    r"^(都.*吧|好[的吧]?$|行$|可以$|停$|嗯$|"
    r"没问题|知道了|收到|ok$|okay$|yes$|no$)",
    re.IGNORECASE,
)

# 知识型关键词：命中时不跳过 recall，即使消息以操作型前缀开头。
# 避免误伤“检查一下 X 的原理”“配置 Y 的机制是什么”这类有召回价值的提问。
_KNOWLEDGE_SIGNAL_WORDS: Final[tuple[str, ...]] = (
    "原理", "机制", "为什么", "为何", "原因", "导致", "区别", "对比",
    "定义", "概念", "公式", "流程", "规则", "结论", "含义", "作用",
    "是什么", "怎么回事", "如何理解",
)


# 内部维护 / 自动注入 prompt：不进入 recall，避免递归召回和 Hindsight 超时
_INTERNAL_PROMPT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"review the conversation above", re.IGNORECASE),
    re.compile(r"update the skill", re.IGNORECASE),
    re.compile(r"context compaction", re.IGNORECASE),
    re.compile(r"following is recalled memory context", re.IGNORECASE),
    re.compile(r"<memory-context>", re.IGNORECASE),
)


def skip_pre_llm_call(user_message: str) -> str:
    """pre_llm_call 文本门控：操作型对话跳过整个 recall 流水线。

    必须在来源门控通过后调用。返回非空字符串时跳过。

    Args:
        user_message: 本轮用户消息原文。

    Returns:
        "" = 正常执行；非空字符串 = 跳过原因。
    """
    msg = user_message.strip()
    if not msg:
        return "消息为空"

    for pat in _INTERNAL_PROMPT_PATTERNS:
        if pat.search(msg):
            return f"内部维护/注入prompt({pat.pattern})"

    # 知识型关键词反转：含强知识信号的提问不跳过，即使以操作型前缀开头。
    # 例如“检查一下 X 的原理”“配置 Y 的机制”仍有召回价值。
    if any(word in msg for word in _KNOWLEDGE_SIGNAL_WORDS):
        return ""

    first_line_lower = msg.splitlines()[0].strip().lower()
    for prefix_lower in _OPERATIONAL_PREFIXES_LOWER:
        if first_line_lower.startswith(prefix_lower):
            return f"操作型前缀({msg[:20]})"

    if len(msg) <= 3:
        return f"消息过短({len(msg)}字符)"

    if len(msg) <= 40:
        msg_lower = msg.lower()
        for prefix_lower in _OPERATIONAL_PREFIXES_LOWER:
            if msg_lower.startswith(prefix_lower):
                return f"操作型前缀({msg[:20]})"

    if _CONFIRM_PATTERN.match(msg):
        return f"确认型消息({msg[:15]})"

    # 用户消息 > 200 字符且中文 < 5% → 英文/代码查询，跳过中文知识召回
    if len(msg) > 200:
        zh_chars = sum(1 for c in msg if "\u4e00" <= c <= "\u9fff")
        if zh_chars / len(msg) < 0.05:
            return f"中文占比过低({zh_chars}/{len(msg)})"

    return ""


def skip_post_llm_call(assistant_response: str) -> str:
    """post_llm_call 文本门控：操作型响应跳过知识点提取。

    必须在来源门控通过后调用。返回非空字符串时跳过。

    Args:
        assistant_response: LLM 的完整响应。

    Returns:
        "" = 正常执行；非空字符串 = 跳过原因。
    """
    if not assistant_response:
        return "响应为空"

    response = assistant_response.strip()

    if len(response) < 80:
        return f"响应过短({len(response)}字符)"

    code_fence_count = response.count("```")
    if code_fence_count >= 3 and len(response) < 300:
        return f"纯工具输出({code_fence_count}个```)"

    first_line = response.split("\n")[0].strip()
    if len(first_line) < 20 and _CONFIRM_PATTERN.match(first_line):
        return f"确认型首行({first_line})"

    return ""