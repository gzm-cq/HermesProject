"""Step 1: LLM 知识点提取 — 每篇文章提取 3-8 个关键知识点

⚠️ 已弃用：被 phase/merged.py 替代，保留仅供历史回放和回滚
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from knowledge_tree_builder.llm.client import call_llm

logger = logging.getLogger(__name__)


def extract_knowledge_points(
    article_text: str,
    article_title: str = "",
    *,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
    temperature: float = 0,
    max_tokens: int = 2048,
) -> list[str]:
    """用 LLM 从文章中提取最核心的 3-8 个知识点。

    Args:
        article_text: 文章内容（Markdown 长文）
        article_title: 文章标题
        api_url: LLM API 地址
        api_key: API 密钥
        model: 模型名
        temperature: 生成温度（提取时设为 0 避免随机波动）
        max_tokens: 最大输出 token 数

    Returns:
        提取出的知识点列表（每条独立可理解的短文本）
    """
    # 截断避免超长
    if len(article_text) > 8000:
        logger.warning("文章超长（%d 字符），截断至 8000 字符", len(article_text))
    truncated = article_text[:8000]

    prompt = (
        "从这篇文章中提取最核心的 3-8 个知识点。\n"
        "只提取：理论、公式、原理、方法论、标准流程。\n"
        "忽略：配置参数、版本号、操作记录、文件路径、个人偏好。\n"
        "每个知识点一句话概括，独立可理解。\n"
        "用「-」开头逐条列出，不要编号。\n"
        f"\n文章标题：{article_title}\n"
        f"文章内容：\n{truncated}"
    )

    response = call_llm(
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        api_url=api_url,
        api_key=api_key,
        model=model,
    )
    if not response:
        return []

    return _parse_extracted_points(response)


def extract_knowledge_points_with_temporal(
    article_text: str,
    article_title: str = "",
    *,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
    temperature: float = 0,
    max_tokens: int = 3072,
) -> list[dict[str, Any]]:
    """用 LLM 提取知识点并附带 temporal 信息（P3-9 时态感知）。

    Args:
        article_text: 文章内容
        article_title: 文章标题
        api_url: LLM API 地址
        api_key: API 密钥
        model: 模型名
        temperature: 生成温度
        max_tokens: 最大输出 token 数

    Returns:
        [{text, valid_from, valid_until}, ...] 列表
        valid_from / valid_until 为 ISO 日期字符串或 None
    """
    from knowledge_tree_builder.core.temporal import TemporalRange, parse_llm_temporal

    if len(article_text) > 8000:
        logger.warning("文章超长（%d 字符），截断至 8000 字符", len(article_text))
    truncated = article_text[:8000]

    prompt = (
        "从这篇文章中提取最核心的 3-8 个知识点。\n"
        "只提取：理论、公式、原理、方法论、标准流程。\n"
        "忽略：配置参数、版本号、操作记录、文件路径、个人偏好。\n"
        "\n"
        "同时识别每个知识点的有效时间范围：\n"
        "- 如果知识点有明确的起始时间（如'2024年起'、'v2之后'），填 valid_from\n"
        "- 如果知识点有明确的失效时间（如'2023年前'、'旧版本中'），填 valid_until\n"
        "- 如果没有时间限制，两个字段都留 null\n"
        "- 日期用 ISO 格式：YYYY-MM-DD 或 YYYY-MM 或 YYYY\n"
        "\n"
        "输出 JSON 数组格式，每项包含 {text, valid_from, valid_until}。\n"
        "不要输出任何其他解释文字。\n"
        f"\n文章标题：{article_title}\n"
        f"文章内容：\n{truncated}"
    )

    response = call_llm(
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        api_url=api_url,
        api_key=api_key,
        model=model,
    )
    if not response:
        return []

    parsed = _parse_temporal_response(response)
    if parsed:
        return parsed

    # JSON 解析失败时，fallback 到普通提取 + 启发式 temporal
    plain_points = _parse_extracted_points(response)
    from knowledge_tree_builder.core.temporal import extract_temporal_from_text
    result = []
    for text in plain_points:
        tr = extract_temporal_from_text(text)
        result.append({
            "text": text,
            "valid_from": tr.valid_from,
            "valid_until": tr.valid_until,
        })
    return result


def _parse_temporal_response(response: str) -> list[dict[str, Any]]:
    """尝试从 LLM 响应中解析 JSON 数组。"""
    if not response:
        return []

    # 尝试直接解析
    try:
        data = json.loads(response)
        if isinstance(data, list):
            return _normalize_temporal_items(data)
    except (json.JSONDecodeError, ValueError):
        pass

    # 提取 ```json 代码块
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", response)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return _normalize_temporal_items(data)
        except (json.JSONDecodeError, ValueError):
            pass

    # 提取 [{...}] 形式的数组
    m = re.search(r"\[\s*\{[\s\S]*\}\s*\]", response)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return _normalize_temporal_items(data)
        except (json.JSONDecodeError, ValueError):
            pass

    return []


def _normalize_temporal_items(items: list[Any]) -> list[dict[str, Any]]:
    """规范化 temporal 提取结果。"""
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("content") or item.get("point") or ""
        if not text:
            continue
        result.append({
            "text": str(text).strip(),
            "valid_from": item.get("valid_from") or item.get("from") or item.get("start") or None,
            "valid_until": item.get("valid_until") or item.get("until") or item.get("end") or None,
        })
    return result


def _parse_extracted_points(response: str) -> list[str]:
    """解析 LLM 返回的知识点列表文本为字符串列表。

    支持的格式：
    - 「- xxx」或「-xxx」（markdown 列表）
    - 「1. xxx」或「1、xxx」（编号列表）
    - 每行一条，无前缀
    """
    points: list[str] = []
    for line in response.splitlines():
        line = line.strip()
        if not line:
            continue
        # 去掉列表前缀
        line = re.sub(r"^[-─—•]\s*", "", line)
        line = re.sub(r"^\d+[\.、\)]\s*", "", line)
        line = line.strip()
        if line:
            points.append(line)

    # 如果 LLM 返回了用换行分隔的内容但没给列表前缀，按换行切割
    if not points and response.strip():
        points = [p.strip() for p in response.strip().splitlines() if p.strip()]

    return points
