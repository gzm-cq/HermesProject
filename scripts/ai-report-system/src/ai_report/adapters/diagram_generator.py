"""
Hermes Diagram Generator — 图表生成器 (Backward-compatible re-export shim)

This module has been refactored into a package at src/hermes_tools/diagrams/.
All symbols are re-exported from here for backward compatibility.
"""
from __future__ import annotations

from .diagrams.core import (
    DiagramResult,
    DiagramType,
    SkillInfo,
    SkillName,
    SkillPriority,
    _SKILL_REGISTRY,
    _get_format_for_skill,
)
from .diagrams.engine import HermesDiagramGenerator
from .diagrams.generators import (
    _generate_ascii_diagram,
    _generate_excalidraw_json,
    _generate_infographic_json,
    _generate_mermaid_diagram,
    _generate_text_architecture,
    _generate_text_comparison,
    _generate_text_flowchart,
    _generate_text_timeline,
)

__all__ = [
    "HermesDiagramGenerator",
    "DiagramType",
    "DiagramResult",
    "SkillInfo",
    "SkillName",
    "SkillPriority",
    "_SKILL_REGISTRY",
    "_get_format_for_skill",
    "_generate_ascii_diagram",
    "_generate_excalidraw_json",
    "_generate_infographic_json",
    "_generate_mermaid_diagram",
    "_generate_text_architecture",
    "_generate_text_comparison",
    "_generate_text_flowchart",
    "_generate_text_timeline",
]
