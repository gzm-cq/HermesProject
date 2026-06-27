"""
Hermes Agent 层 DAG 并行编排 — 在聊天框/Agent上下文执行
=====================================================

职责：
  在 Hermes Agent 上下文中（有 delegate_task 工具），
  按 DAG 分层批量委托章节生成，实现真正的并行加速。

  调用方式：
    from scripts.hermes_dag_orchestrator import run_dag_report
    result = await run_dag_report(topic, report_goal, ...)
    # 或由 Hermes Agent 直接在聊天框调用 run_dag_report()
"""

from __future__ import annotations

import json as _json
import logging
import time
from pathlib import Path
from typing import Any

from ai_report.core.dag_utils import derive_dag_layers

logger = logging.getLogger("dag_orchestrator")


def build_dag_tasks(
    chapter_prompts: list[dict],
    report_goal: dict,
    all_prev_summaries: list[str],
    sibling_info: list[dict],
) -> list[dict]:
    """为 DAG 一层构建批量 delegate_task 任务列表。

    每章一个 task，包含完整的写作上下文。
    同层章节共享 sibling_info 以避免内容重叠。

    Args:
        chapter_prompts: 本层所有章节的 prompt 字典列表
        report_goal: 报告总目标
        all_prev_summaries: 前层摘要列表
        sibling_info: 同层其他章节信息

    Returns:
        delegate_task tasks 数组
    """
    tasks: list[dict] = []
    for i, cp in enumerate(chapter_prompts):
        context = {
            "_instructions": (
                "你是一个专业的报告写作助手。请根据以下信息写出本章完整内容。\n"
                "要求：\n"
                "1. 严格遵循 writing_role 要求的角色、语调和叙述方式\n"
                "2. 必须覆盖 key_points 中的所有要点\n"
                "3. 避免涉及 avoid_topics 中的话题\n"
                "4. 如果指定了 chart_spec，在合适位置插入图表标记\n"
                "5. 确保与前文内容衔接顺畅、不重复\n"
                "6. 输出纯文本 Markdown，不要多余的前缀说明\n"
                "7. 最后用 ---SUMMARY--- 单独一行，其后跟本章摘要（不超过200字）\n"
            ),
            "task_type": "write_chapter",
            "report_goal": report_goal,
            "chapter": {
                "index": cp.get("index", i),
                "title": cp.get("title", ""),
                "level": cp.get("level", 1),
                "section_type": cp.get("section_type", "body"),
                "writing_intent": cp.get("writing_intent", ""),
                "key_points": cp.get("key_points", []),
                "avoid_topics": cp.get("avoid_topics", []),
                "chart_spec": cp.get("chart_spec"),
            },
            "materials_text": cp.get("materials_text", ""),
            "prev_chapter_summary": (
                "\n".join(f"- {s[:200]}" for s in all_prev_summaries[-5:])
                if all_prev_summaries else ""
            ),
            "sibling_chapters": sibling_info,
        }

        # 写入角色与整体规范（从 report_goal 提取）
        writing_role = report_goal.get("writing_role", {}) if isinstance(report_goal, dict) else {}
        if writing_role:
            context["writing_role"] = {
                "role": writing_role.get("role", ""),
                "tone": writing_role.get("tone", ""),
                "voice": writing_role.get("voice", ""),
                "output_conventions": writing_role.get("output_conventions", ""),
            }

        tasks.append({
            "goal": f"写报告章节: {cp.get('title', '')}",
            "context": _json.dumps(context, ensure_ascii=False),
        })

    return tasks


def parse_chapter_result(result: str, title: str) -> dict[str, Any]:
    """解析 delegate_task 返回的章节内容。

    Args:
        result: delegate_task 返回字符串（可能含 ---SUMMARY--- 标记）
        title: 章节标题

    Returns:
        {"content": str, "summary": str}
    """
    marker = "---SUMMARY---"
    if marker in result:
        parts = result.split(marker, 1)
        content = parts[0].strip()
        summary = parts[1].strip()[:200]
    else:
        content = result.strip()
        summary = content[:200]
    return {"title": title, "content": content, "summary": summary}


def derive_layers_from_prompts(
    chapter_prompts: list[dict],
) -> list[list[int]]:
    """从 chapter_prompts 的 section_type 推导 DAG 层。

    委托给 dag_utils.derive_dag_layers() 避免逻辑重复。
    将 prompts 包装为兼容 sections 对象传入。

    Args:
        chapter_prompts: StateGraph 生成的章节提示词列表

    Returns:
        DAG 分层索引列表，如 [[0, 2], [1, 3], [4]]
    """
    # 构造兼容 sections 的轻量对象，仅提供 section_type 属性
    class _MockSection:
        def __init__(self, title: str, section_type: str) -> None:
            self.title = title
            self.section_type = section_type

    sections = [
        _MockSection(
            cp.get("title", f"ch-{i}"),
            (cp.get("section_type") or "body").strip().lower(),
        )
        for i, cp in enumerate(chapter_prompts)
    ]
    return derive_dag_layers(sections, chapter_prompts=chapter_prompts)
