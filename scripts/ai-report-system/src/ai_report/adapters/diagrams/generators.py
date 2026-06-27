"""Diagram generators stub"""
from __future__ import annotations

from typing import Any

from .core import DiagramResult, DiagramType


def _generate_ascii_diagram(description: str) -> str:
    return f"ASCII: {description}"


def _generate_excalidraw_json(description: str) -> dict[str, Any]:
    return {}


def _generate_infographic_json(description: str) -> dict[str, Any]:
    return {}


def _generate_mermaid_diagram(description: str) -> str:
    return f"mermaid: {description}"


def _generate_text_architecture(description: str) -> str:
    return f"Architecture: {description}"


def _generate_text_comparison(description: str) -> str:
    return f"Comparison: {description}"


def _generate_text_flowchart(description: str) -> str:
    return f"Flowchart: {description}"


def _generate_text_timeline(description: str) -> str:
    return f"Timeline: {description}"
