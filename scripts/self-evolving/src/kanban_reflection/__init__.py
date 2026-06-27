"""Kanban 反思回路 — 失败后自动分析原因并注入重试 prompt

基于 Reflexion 范式：失败轨迹 → LLM 分析 → 结构化反思结果 → 注入重试。
"""

from kanban_reflection.core.reflector import reflect_on_failure, ReflectionResult
from kanban_reflection.config import KanbanReflectionConfig

__all__ = [
    "reflect_on_failure",
    "ReflectionResult",
    "KanbanReflectionConfig",
]
