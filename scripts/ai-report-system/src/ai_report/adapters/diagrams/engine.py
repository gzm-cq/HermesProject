"""Diagram engine"""
from __future__ import annotations

from typing import Any

from .core import DiagramResult, DiagramType


class HermesDiagramGenerator:
    """Diagram generator with caching support."""

    def __init__(self) -> None:
        self._cache: dict[str, DiagramResult] = {}
        self._hits = 0
        self._misses = 0

    def get_cache_stats(self) -> dict[str, Any]:
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
        }

    def generate(
        self,
        description: str,
        diagram_type: Any = DiagramType.ASCII,
        priority: str = "quality",
    ) -> DiagramResult:
        """Generate a diagram with caching."""
        cache_key = f"{description}:{diagram_type}:{priority}"

        if cache_key in self._cache:
            self._hits += 1
            return self._cache[cache_key]

        self._misses += 1

        if isinstance(diagram_type, str):
            try:
                diagram_type = DiagramType(diagram_type)
            except ValueError:
                diagram_type = DiagramType.ASCII

        # Map priority to skill/quality
        if priority == "quality":
            skill = "best_skill"
            quality = 0.95
        else:
            skill = "fast_skill"
            quality = 0.75

        result = DiagramResult(
            content=f"Diagram: {description}",
            diagram_type=diagram_type,
            format="ascii",
            skill_used=skill,
            quality_score=quality,
            fallback_chain=["ascii"],
        )

        self._cache[cache_key] = result
        return result
