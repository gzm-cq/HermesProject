"""
optimize_structure node — 根据报告目标优化章节结构
"""

from __future__ import annotations
import json as _json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_prompt(goal: dict[str, Any], outlines: list[str]) -> str:
    refs_text = "\n".join(outlines[:3]) if outlines else "(无参考文章)"
    goal_text = _json.dumps(goal, ensure_ascii=False)
    return (
        f"你是一个报告规划专家。请根据以下报告目标的评估框架来规划章节结构。\n\n"
        f"## 报告目标\n{goal_text}\n\n"
        f"## 参考文章目录（仅供参考，若与目标不一致则以目标为准）\n{refs_text}\n\n"
        f"## 规划要求\n"
        f"1. 章节结构应由 report_goal.overall_strategy 定义的评估维度驱动\n"
        f"2. 不要照搬源文档的模块划分组织章节，不要写实施步骤\n"
        f"3. 聚焦方案论证/可行性评估/能力分析\n"
        f"4. 每章有明确的 writing_intent 和 key_points\n\n"
        f"输出 JSON 数组：[{{\"title\":..., \"level\":1, \"section_type\":\"intro|body|conclusion\","
        f" \"writing_intent\":..., \"key_points\":[...], \"avoid_topics\":[]}}]\n"
        f"4-6 个 H1 主章节，仅输出 JSON。"
    )


def parse_response(response: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        parsed = _json.loads(response.strip())
        if isinstance(parsed, list):
            result = parsed
        elif isinstance(parsed, dict) and "chapters" in parsed:
            result = parsed["chapters"]
    except _json.JSONDecodeError:
        import re as _re
        match = _re.search(r'\[[\s\S]*\]', response)
        if match:
            try:
                parsed = _json.loads(match.group())
                if isinstance(parsed, list):
                    result = parsed
            except _json.JSONDecodeError:
                pass
    if not result:
        logger.warning("[optimize_structure] 解析失败，使用默认大纲")
        result = [
            {"title": "概述", "level": 1, "section_type": "intro",
             "estimated_words": 500, "writing_intent": "项目背景与目标", "key_points": [], "avoid_topics": []},
            {"title": "核心分析", "level": 1, "section_type": "body",
             "estimated_words": 1500, "writing_intent": "各维度论证", "key_points": [], "avoid_topics": []},
            {"title": "总结与建议", "level": 1, "section_type": "conclusion",
             "estimated_words": 500, "writing_intent": "综合结论与决策建议", "key_points": [], "avoid_topics": []},
        ]
    return result
