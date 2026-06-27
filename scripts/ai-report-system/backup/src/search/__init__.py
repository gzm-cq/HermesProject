#!/usr/bin/env python3
"""
Hermes Web Search module.

This module provides the foundational data structures and interfaces
for Hermes-integrated web search functionality.
"""

from .hermes_searcher import (
    HermesSearchResult,
    HermesWebSearcher,
    CachedSearchResult,
)

__all__ = [
    "HermesSearchResult",
    "HermesWebSearcher", 
    "CachedSearchResult",
]

__version__ = "0.1.0"