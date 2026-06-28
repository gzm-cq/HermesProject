"""Step 1.5: 知识准入（规则驱动的二次过滤）

作为 LLM 提取的兜底过滤，只处理 LLM 漏掉的情况。
不走 LLM，纯规则，避免额外调用成本。
"""

import re


def filter_knowledge_points(
    candidates: list[str],
    min_length: int = 10,
) -> list[str]:
    """规则驱动的知识准入过滤。

    Args:
        candidates: LLM 提取出的候选知识点列表
        min_length: 最短知识点字数

    Returns:
        通过准入的知识点列表
    """
    valid: list[str] = []
    for text in candidates:
        text = text.strip()
        if not text:
            continue

        # 规则1: 长度太短 → 丢弃
        if len(text) < min_length:
            continue

        # 规则2: 以"本文"、"文章"、"本研究"开头 → 丢弃（不是独立知识）
        if re.match(r"^(本文|文章|本研究|本篇|该文章|该研究)", text):
            continue

        # 规则3: 包含模糊概括动词 → 丢弃
        if re.search(r"(讨论了|介绍了|分析了|探讨了|概述了|总结了|描述了|阐述了)", text):
            continue

        # 规则4: 过于笼统的句子（不包含任何具体事实或术语）→ 丢弃
        # 保留包含具体名词、技术术语或数字内容的句子
        if _is_vague(text):
            continue

        valid.append(text)

    return valid


def _is_vague(text: str) -> bool:
    """判断知识点是否过于笼统，缺乏具体信息。

    通过检测是否包含具体术语或数字来判断。
    """
    # 包含数字 → 通常比较具体
    if re.search(r"\d+", text):
        return False

    # 包含常见技术术语 → 具体
    specific_indicators = [
        "算法",
        "模型",
        "架构",
        "框架",
        "系统",
        "原理",
        "机制",
        "流程",
        "协议",
        "策略",
        "范式",
        "工具",
        "技术",
        "理论",
        "公式",
        "定理",
        "定律",
        "标准",
        "规范",
    ]
    if any(indicator in text for indicator in specific_indicators):
        return False

    # 包含引号/书名号（引用具体概念）→ 具体
    if re.search(r"['\"「」『』《》【】]", text):
        return False

    # 只含"是XXX的XXX"这类高度抽象的句式 → 笼统
    abstract_patterns = [
        r"^[^，。]*是[^，。]*的[^，。]*$",
        r"^[^，。]*对[^，。]*的[^，。]*$",
        r"^[^，。]*在[^，。]*方面",
        r"^[^，。]*具有[^，。]*特点",
    ]
    if any(re.match(pat, text) for pat in abstract_patterns):
        return True

    return False
