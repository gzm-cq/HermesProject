#!/usr/bin/env python3
"""
Test module for HermesWebSearcher base class and HermesSearchResult dataclass.
Following strict TDD methodology - RED phase.

This file is a compatibility shim. Tests have been split into:
  - test_hermes_search_result.py      — HermesSearchResult dataclass tests
  - test_hermes_searcher_base.py      — WebSearcher init + caching + CachedSearchResult
  - test_hermes_searcher_search.py    — search method + internal method tests
"""

import pytest

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
