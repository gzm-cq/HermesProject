"""执行摘要生成器 — 负责从报告全文生成一页执行摘要。

遵循 Hermes Code Rules 规范。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ExecutiveSummaryGenerator"]


class ExecutiveSummaryGenerator:
    """负责生成执行摘要。

    从报告全文中提取关键信息，生成一页执行摘要。
    """

    def generate(self, full_content: str, plan: Any) -> str:
        """从报告全文生成一页执行摘要。

        Args:
            full_content: 报告完整 markdown 内容
            plan: 报告计划（含 goal 信息）

        Returns:
            执行摘要 markdown 文本；生成失败时返回空字符串
        """
        from ..adapters.ai_client import call_llm as _call

        # 取报告前 3000 字 + 关键章节的前 500 字
        content_preview = full_content[:3000]
        goal = plan.metadata.get("report_goal", {}) if hasattr(plan, "metadata") else {}
        title = goal.get("title", plan.topic) if isinstance(goal, dict) else plan.topic

        prompt = (
            f"请为以下报告撰写一页执行摘要。\n\n"
            f"## 报告标题\n{title}\n\n"
            f"## 报告内容（前3000字预览）\n{content_preview}\n\n"
            "## 摘要要求\n"
            "请输出 markdown 格式，包含：\n"
            "1. 核心问题（一句话）\n"
            "2. 解决方案概述（2-3个要点）\n"
            "3. 投资与回报（关键数字）\n"
            "4. 建议决策（一句话）\n\n"
            "## 格式\n"
            "# 执行摘要\n\n"
            "**核心问题：** ...\n\n"
            "**方案要点：**\n"
            "- ...\n"
            "- ...\n\n"
            "**投资与预期回报：** ...\n\n"
            "**建议决策：** ...\n"
        )
        try:
            result = _call(prompt, max_iterations=1, temperature=0.3)
            if result and len(result.strip()) > 50:
                return result.strip()
        except Exception:
            pass
        return ""
