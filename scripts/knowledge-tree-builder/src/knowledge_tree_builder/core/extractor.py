"""Step 1: LLM 知识点提取 — 每篇文章提取 3-8 个关键知识点"""

import logging
import re

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
