#!/usr/bin/env python3
"""
Hermes Web Searcher base classes and data structures.

This module provides the foundational data structures and interfaces
for Hermes-integrated web search functionality.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

# Constants
DEFAULT_CACHE_TTL = 3600.0  # 1 hour in seconds
VALID_SOURCES = {'hermes_browser', 'hermes_web'}


@dataclass
class HermesSearchResult:
    """Represents a single search result."""
    title: str
    content: str
    url: Optional[str]
    source: str
    relevance: float
    
    def __post_init__(self) -> None:
        """Validate the search result after initialization."""
        # Validate title
        if not self.title or not self.title.strip():
            raise ValueError("Title cannot be empty")
        
        # Validate content
        if not self.content or not self.content.strip():
            raise ValueError("Content cannot be empty")
        
        # Validate source
        if self.source not in VALID_SOURCES:
            raise ValueError("Source must be either 'hermes_browser' or 'hermes_web'")
        
        # Validate relevance range
        if not 0.0 <= self.relevance <= 1.0:
            raise ValueError("Relevance must be between 0.0 and 1.0")
    
    @property
    def relevance_score(self) -> float:
        """Convert relevance from 0-1 scale to 0-100 scale.
        
        Returns:
            float: Relevance score on 0-100 scale.
        """
        return self.relevance * 100.0


class HermesWebSearcher:
    """Base class for Hermes-integrated web search adapters."""
    
    def __init__(self) -> None:
        """Initialize the web searcher."""
        self.search_methods: List[str] = ['browser', 'web']
        self._cache: Dict[str, 'CachedSearchResult'] = {}
    
    def _cache_key(self, query: str, method: str) -> str:
        """Generate a cache key for the query and method.
        
        Args:
            query: Search query string
            method: Search method ('browser' or 'web')
            
        Returns:
            str: Cache key
        """
        return f"{query}::{method}"
    
    def _get_cached_results(self, query: str, method: str) -> Optional[List[HermesSearchResult]]:
        """Get cached search results if available and not expired.
        
        Args:
            query: Search query string
            method: Search method ('browser' or 'web')
            
        Returns:
            Optional list of HermesSearchResult objects if cached and valid,
            None otherwise.
        """
        key = self._cache_key(query, method)
        
        if key not in self._cache:
            return None
        
        cached = self._cache[key]
        
        # Check if cache has expired
        current_time = time.time()
        if current_time > cached.expires:
            # Cache expired, remove it
            del self._cache[key]
            return None
        
        return cached.results
    
    def _cache_results(self, query: str, method: str, results: List[HermesSearchResult]) -> None:
        """Cache search results for future use.

        Args:
            query: Search query string
            method: Search method ('browser' or 'web')
            results: List of search results to cache
        """
        key = self._cache_key(query, method)
        
        cached_result = CachedSearchResult(
            results=results,
            timestamp=time.time(),
            query=query
        )
        
        self._cache[key] = cached_result

    def search(self, query: str, max_results: int = 5) -> List[HermesSearchResult]:
        """Search for information using Hermes tools.

        Args:
            query: Search query string
            max_results: Maximum number of results to return (default: 5)

        Returns:
            List of HermesSearchResult objects with search results.
            
        Implementation logic:
        1. Check cache first
        2. Try browser search (higher priority)
        3. If not enough results, try web_search as fallback
        4. Process results (deduplicate, sort by relevance)
        5. Cache results for future use
        """
        # First check cache
        cached = self._get_cached_results(query, "search")
        if cached is not None:
            # Apply max_results limit to cached results
            return cached[:max_results]
        
        # Try browser search first
        browser_results = self._search_with_browser(query, max_results)
        all_results = list(browser_results)
        
        # If not enough results, try web search as fallback
        if len(all_results) < max_results:
            remaining_needed = max_results - len(all_results)
            web_results = self._search_with_web(query, remaining_needed)
            all_results.extend(web_results)
        
        # Process results (deduplicate, sort, etc.)
        processed_results = self._process_results(all_results)
        
        # Apply max_results limit
        final_results = processed_results[:max_results]
        
        # Cache the results for future use
        self._cache_results(query, "search", final_results)
        
        return final_results

    def _search_with_browser(self, query: str, max_results: int) -> List[HermesSearchResult]:
        """Search using Hermes browser tools.

        Args:
            query: Search query string
            max_results: Maximum number of results to generate

        Returns:
            List of HermesSearchResult objects.

        TODO: Integrate actual Hermes browser tools:
        - browser_navigate: Navigate to search engine
        - browser_snapshot: Capture and extract results
        """
        # TODO: Actual Hermes browser tool integration
        # This is a mock/placeholder implementation
        
        # Return empty list as placeholder
        return []

    def _search_with_web(self, query: str, max_results: int) -> List[HermesSearchResult]:
        """Search using Hermes web search tools.

        Args:
            query: Search query string
            max_results: Maximum number of results to generate

        Returns:
            List of HermesSearchResult objects.

        TODO: Integrate actual Hermes web_search tool
        """
        # TODO: Actual Hermes web_search tool integration
        # This is a mock/placeholder implementation
        
        # Return empty list as placeholder
        return []

    def _process_results(self, results: List[HermesSearchResult]) -> List[HermesSearchResult]:
        """Process search results: deduplicate, sort, and filter.

        Args:
            results: List of raw search results

        Returns:
            List of processed search results.
        """
        if not results:
            return []
        
        # Step 1: Remove invalid results
        valid_results = []
        for result in results:
            # Basic validation
            if result.title and result.content and result.relevance >= 0:
                valid_results.append(result)
        
        if not valid_results:
            return []
        
        # Step 2: Deduplication based on title and content similarity
        deduplicated = []
        seen_titles = set()
        seen_urls = set()
        
        for result in valid_results:
            title_lower = result.title.lower().strip()
            url_key = result.url.lower().strip() if result.url else None
            
            # Check for duplicates by title or URL
            is_duplicate = (title_lower in seen_titles) or \
                          (url_key and url_key in seen_urls)
            
            if not is_duplicate:
                seen_titles.add(title_lower)
                if url_key:
                    seen_urls.add(url_key)
                deduplicated.append(result)
        
        # Step 3: Sort by relevance (highest first)
        sorted_results = sorted(
            deduplicated, 
            key=lambda x: x.relevance, 
            reverse=True
        )
        
        return sorted_results


@dataclass
class CachedSearchResult:
    """Represents cached search results with expiration."""
    results: List[HermesSearchResult]
    timestamp: float
    query: str
    expires: Optional[float] = None
    
    def __post_init__(self) -> None:
        """Set default expiration if not provided."""
        if self.expires is None:
            self.expires = self.timestamp + DEFAULT_CACHE_TTL  # 1 hour default