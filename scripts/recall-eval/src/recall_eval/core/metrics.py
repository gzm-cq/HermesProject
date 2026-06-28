"""评估指标模块 — 忠实度、相关性、覆盖率计算。"""

import logging
import re
from typing import Any

from recall_eval.adapters.llm_client import LLMClient

logger = logging.getLogger(__name__)


def faithfulness_score(
    query: str,
    context: str,
    answer: str,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    """计算忠实度分数：答案是否基于上下文，无幻觉。

    Args:
        query: 用户查询
        context: 检索到的上下文
        answer: 生成的回答
        llm_client: LLM 客户端实例，为 None 时使用启发式规则

    Returns:
        包含 score (0.0~1.0)、reason、支持/不支持的声明等信息的字典
    """
    if llm_client is not None:
        return llm_client.evaluate_faithfulness(query, context, answer)

    return _heuristic_faithfulness(query, context, answer)


def relevance_score(
    query: str,
    context: str,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    """计算相关性分数：上下文与查询的相关程度。

    Args:
        query: 用户查询
        context: 检索到的上下文
        llm_client: LLM 客户端实例，为 None 时使用启发式规则

    Returns:
        包含 score (0.0~1.0)、reason、相关/不相关主题等信息的字典
    """
    if llm_client is not None:
        return llm_client.evaluate_relevance(query, context)

    return _heuristic_relevance(query, context)


def coverage_score(
    query: str,
    context: str,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    """计算覆盖率分数：上下文覆盖查询要点的比例。

    Args:
        query: 用户查询
        context: 检索到的上下文
        llm_client: LLM 客户端实例，为 None 时使用启发式规则

    Returns:
        包含 score (0.0~1.0)、reason、覆盖/缺失要点等信息的字典
    """
    if llm_client is not None:
        return llm_client.evaluate_coverage(query, context)

    return _heuristic_coverage(query, context)


def _tokenize(text: str) -> set[str]:
    """简单分词：提取中文词和英文单词。"""
    text = text.lower()
    english_words = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text))
    chinese_chars = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    return english_words | chinese_chars


def _extract_keywords(text: str, top_n: int = 10) -> list[str]:
    """从文本中提取关键词（简单频率统计）。"""
    tokens = _tokenize(text)
    stop_words = {
        "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
        "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
        "自己", "这", "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
        "may", "might", "must", "shall", "can", "need", "dare", "ought", "used", "to",
        "of", "in", "for", "on", "with", "at", "by", "from", "as", "into", "through",
        "during", "before", "after", "above", "below", "between", "out", "off", "over",
        "under", "again", "further", "then", "once", "here", "there", "when", "where",
        "why", "how", "all", "both", "each", "few", "more", "most", "other", "some",
        "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
        "just", "because", "but", "and", "or", "if", "while", "although", "though",
        "that", "this", "these", "those", "i", "me", "my", "myself", "we", "our",
        "ours", "ourselves", "you", "your", "yours", "yourself", "yourselves",
        "he", "him", "his", "himself", "she", "her", "hers", "herself", "it", "its",
        "itself", "they", "them", "their", "theirs", "themselves", "what", "which",
        "who", "whom", "whose",
    }
    filtered = [t for t in tokens if t not in stop_words and len(t) >= 2]
    return sorted(filtered, key=lambda x: len(x), reverse=True)[:top_n]


def _heuristic_faithfulness(query: str, context: str, answer: str) -> dict[str, Any]:
    """启发式忠实度评估：基于关键词重叠。"""
    if not answer.strip():
        return {
            "score": 0.0,
            "reason": "答案为空",
            "supported_claims": [],
            "unsupported_claims": [],
        }

    answer_tokens = _tokenize(answer)
    context_tokens = _tokenize(context)

    if not answer_tokens:
        return {
            "score": 0.0,
            "reason": "答案无可分析内容",
            "supported_claims": [],
            "unsupported_claims": [],
        }

    overlap = answer_tokens & context_tokens
    overlap_ratio = len(overlap) / len(answer_tokens) if answer_tokens else 0.0

    answer_keywords = _extract_keywords(answer, top_n=5)
    context_keywords = _extract_keywords(context, top_n=10)
    keyword_overlap = set(answer_keywords) & set(context_keywords)
    keyword_ratio = len(keyword_overlap) / len(answer_keywords) if answer_keywords else 0.0

    score = 0.6 * overlap_ratio + 0.4 * keyword_ratio
    score = max(0.0, min(1.0, score))

    return {
        "score": round(score, 3),
        "reason": f"基于关键词重叠率 {overlap_ratio:.2f} 和关键词覆盖率 {keyword_ratio:.2f}",
        "supported_claims": list(keyword_overlap),
        "unsupported_claims": [k for k in answer_keywords if k not in context_keywords],
        "method": "heuristic",
    }


def _heuristic_relevance(query: str, context: str) -> dict[str, Any]:
    """启发式相关性评估：基于查询与上下文的关键词重叠。"""
    if not context.strip():
        return {
            "score": 0.0,
            "reason": "上下文为空",
            "relevant_topics": [],
            "irrelevant_topics": [],
        }

    query_tokens = _tokenize(query)
    context_tokens = _tokenize(context)

    if not query_tokens:
        return {
            "score": 0.5,
            "reason": "查询无可分析关键词，默认中性评分",
            "relevant_topics": [],
            "irrelevant_topics": [],
        }

    overlap = query_tokens & context_tokens
    overlap_ratio = len(overlap) / len(query_tokens) if query_tokens else 0.0

    query_keywords = _extract_keywords(query, top_n=5)
    context_keywords = _extract_keywords(context, top_n=10)
    keyword_overlap = set(query_keywords) & set(context_keywords)
    keyword_ratio = len(keyword_overlap) / len(query_keywords) if query_keywords else 0.0

    score = 0.5 * overlap_ratio + 0.5 * keyword_ratio
    score = max(0.0, min(1.0, score))

    return {
        "score": round(score, 3),
        "reason": f"基于查询关键词重叠率 {overlap_ratio:.2f} 和主题匹配率 {keyword_ratio:.2f}",
        "relevant_topics": list(keyword_overlap),
        "irrelevant_topics": [k for k in query_keywords if k not in context_keywords],
        "method": "heuristic",
    }


def _heuristic_coverage(query: str, context: str) -> dict[str, Any]:
    """启发式覆盖率评估：基于查询要点在上下文中的覆盖。"""
    if not context.strip():
        return {
            "score": 0.0,
            "reason": "上下文为空",
            "query_points": [],
            "covered_points": [],
            "missing_points": [],
        }

    query_keywords = _extract_keywords(query, top_n=8)
    if not query_keywords:
        return {
            "score": 0.5,
            "reason": "查询无可分析要点，默认中性评分",
            "query_points": [],
            "covered_points": [],
            "missing_points": [],
        }

    context_tokens = _tokenize(context)
    covered = [kw for kw in query_keywords if kw in context_tokens]
    missing = [kw for kw in query_keywords if kw not in context_tokens]

    score = len(covered) / len(query_keywords) if query_keywords else 0.0
    score = max(0.0, min(1.0, score))

    return {
        "score": round(score, 3),
        "reason": f"查询 {len(query_keywords)} 个要点中覆盖 {len(covered)} 个",
        "query_points": query_keywords,
        "covered_points": covered,
        "missing_points": missing,
        "method": "heuristic",
    }
