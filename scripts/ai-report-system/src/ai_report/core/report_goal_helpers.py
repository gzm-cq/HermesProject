"""报告目标辅助函数 — 从 workflow_orchestrator.py 提取。

提供 report_goal 的持久化、加载、校验等功能。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def goal_dir_for_topic(topic: str) -> Path:
    """生成报告专属的 goal 存储目录路径。

    路径规则: reports/<主题安全名>/
    """
    safe_name = topic.replace(" ", "_").replace("/", "_").replace("\\", "_")[:60]
    return Path("reports") / safe_name


def check_goal_exists(topic: str) -> bool:
    """检查该主题是否已有已确认的 report_goal.json。"""
    goal_path = goal_dir_for_topic(topic) / "report_goal.json"
    return goal_path.exists()


def validate_report_goal(goal: dict[str, Any]) -> None:
    """校验 report_goal 结构完整性，失败抛 ValueError。"""
    required_goal = {"title", "purpose", "target_audience", "overall_strategy", "writing_role"}
    missing = required_goal - set(goal.keys())
    if missing:
        raise ValueError(f"report_goal 缺少顶层字段: {missing}")

    wr = goal.get("writing_role")
    if not isinstance(wr, dict):
        raise ValueError("report_goal.writing_role 必须是 dict")

    required_role = {"role", "expertise", "tone", "voice", "output_conventions"}
    missing_role = required_role - set(wr.keys())
    if missing_role:
        raise ValueError(f"report_goal.writing_role 缺少字段: {missing_role}")

    if not goal.get("title", "").strip():
        raise ValueError("report_goal.title 不能为空")

    # 检查所有字符串字段是否包含截断标记
    truncated = check_goal_truncation(goal)
    if truncated:
        raise ValueError(
            f"report_goal 字段被截断（含 ...）：{', '.join(truncated)}\n"
            f"请使用明确的完整表述，不要用 … 或 ... 代替后续内容"
        )


def check_goal_truncation(goal: dict[str, Any]) -> list[str]:
    """检查 goal 各字符串字段是否包含截断标记。

    返回被截断的字段名列表（空列表 = 无截断）。
    """
    truncated: list[str] = []
    # 检查顶层字符串字段
    for key in ("title", "purpose", "target_audience", "overall_strategy"):
        val = goal.get(key, "")
        if isinstance(val, str) and ("…" in val or val.rstrip().endswith("...")):
            truncated.append(key)
    # 检查 writing_role 子字段
    wr = goal.get("writing_role", {})
    if isinstance(wr, dict):
        for key in ("role", "tone", "voice", "output_conventions"):
            val = wr.get(key, "")
            if isinstance(val, str) and ("…" in val or val.rstrip().endswith("...")):
                truncated.append(f"writing_role.{key}")
    return truncated


def load_report_goal(topic: str) -> dict[str, Any] | None:
    """加载该主题已确认的 report_goal.json。"""
    goal_path = goal_dir_for_topic(topic) / "report_goal.json"
    if not goal_path.exists():
        return None
    try:
        with goal_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        validate_report_goal(data)
        return data
    except Exception as e:
        logger.warning("  goal 加载失败: %s", e)
        return None


def save_report_goal(
    topic: str,
    goal: dict[str, Any],
) -> Path:
    """保存已确认的 report_goal.json 到报告专属目录。"""
    # 保存前先检查截断，问题早暴露
    truncated = check_goal_truncation(goal)
    if truncated:
        logger.warning("⚠️ 即将保存的 goal 含截断字段: %s", ", ".join(truncated))
        logger.warning("   请修正后再保存，否则加载时 validate_report_goal 会拒绝")
    goal_dir = goal_dir_for_topic(topic)
    goal_dir.mkdir(parents=True, exist_ok=True)
    goal_path = goal_dir / "report_goal.json"
    with goal_path.open("w", encoding="utf-8") as f:
        json.dump(goal, f, ensure_ascii=False, indent=2)
    logger.info("  ✅ goal 已保存: %s", goal_path)
    return goal_path
