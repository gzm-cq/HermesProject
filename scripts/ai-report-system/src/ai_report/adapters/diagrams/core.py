"""Diagram core types"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DiagramType(Enum):
    ASCII = "ascii"
    EXCALIDRAW = "excalidraw"
    INFOGRAPHIC = "infographic"
    MERMAID = "mermaid"
    TEXT_ARCHITECTURE = "text_architecture"
    TEXT_COMPARISON = "text_comparison"
    TEXT_FLOWCHART = "text_flowchart"
    TEXT_TIMELINE = "text_timeline"
    FLOWCHART = "flowchart"
    ARCHITECTURE = "architecture"
    DATA_STRUCTURE = "data_structure"
    COMPARISON = "comparison"
    TIMELINE = "timeline"


@dataclass
class DiagramResult:
    content: str
    diagram_type: DiagramType
    format: str = "ascii"
    skill_used: str = "default"
    quality_score: float = 0.0
    fallback_chain: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillInfo:
    name: str
    priority: int
    description: str


SkillName = str
SkillPriority = int

_SKILL_REGISTRY: dict[str, SkillInfo] = {}


def _get_format_for_skill(skill_name: str) -> str:
    return "ascii"
