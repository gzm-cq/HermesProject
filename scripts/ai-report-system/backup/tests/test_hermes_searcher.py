#!/usr/bin/env python3
"""
Test module for HermesWebSearcher base class and HermesSearchResult dataclass.
Following strict TDD methodology - RED phase.
"""

import pytest
import time
from typing import Optional
from unittest.mock import Mock, patch

# Test HermesSearchResult dataclass


def test_hermes_search_result_creation():
    """Test basic creation of HermesSearchResult dataclass."""
    # This will fail initially because the class doesn't exist yet
    from src.search.hermes_searcher import HermesSearchResult
    
    result = HermesSearchResult(
        title="Test Title",
        content="Test content",
        url="https://example.com",
        source="hermes_browser",
        relevance=0.8
    )
    
    assert result.title == "Test Title"
    assert result.content == "Test content"
    assert result.url == "https://example.com"
    assert result.source == "hermes_browser"
    assert result.relevance == 0.8


def test_hermes_search_result_without_url():
    """Test HermesSearchResult creation without URL."""
    from src.search.hermes_searcher import HermesSearchResult
    
    result = HermesSearchResult(
        title="Test Title",
        content="Test content",
        url=None,
        source="hermes_web",
        relevance=0.5
    )
    
    assert result.title == "Test Title"
    assert result.content == "Test content"
    assert result.url is None
    assert result.source == "hermes_web"
    assert result.relevance == 0.5


def test_hermes_search_result_validation_title():
    """Test validation for non-empty title."""
    from src.search.hermes_searcher import HermesSearchResult
    
    with pytest.raises(ValueError, match="Title cannot be empty"):
        HermesSearchResult(
            title="",  # Empty title should raise ValueError
            content="Test content",
            url="https://example.com",
            source="hermes_browser",
            relevance=0.8
        )


def test_hermes_search_result_validation_content():
    """Test validation for non-empty content."""
    from src.search.hermes_searcher import HermesSearchResult
    
    with pytest.raises(ValueError, match="Content cannot be empty"):
        HermesSearchResult(
            title="Test Title",
            content="",  # Empty content should raise ValueError
            url="https://example.com",
            source="hermes_browser",
            relevance=0.8
        )


def test_hermes_search_result_validation_relevance_range():
    """Test validation for relevance range (0.0 to 1.0)."""
    from src.search.hermes_searcher import HermesSearchResult
    
    # Test relevance below 0.0
    with pytest.raises(ValueError, match="Relevance must be between 0.0 and 1.0"):
        HermesSearchResult(
            title="Test Title",
            content="Test content",
            url="https://example.com",
            source="hermes_browser",
            relevance=-0.1
        )
    
    # Test relevance above 1.0
    with pytest.raises(ValueError, match="Relevance must be between 0.0 and 1.0"):
        HermesSearchResult(
            title="Test Title",
            content="Test content",
            url="https://example.com",
            source="hermes_browser",
            relevance=1.1
        )


def test_hermes_search_result_relevance_score_property():
    """Test relevance_score property that converts 0-1 to 0-100."""
    from src.search.hermes_searcher import HermesSearchResult
    
    result = HermesSearchResult(
        title="Test Title",
        content="Test content",
        url="https://example.com",
        source="hermes_browser",
        relevance=0.75
    )
    
    assert result.relevance_score == 75.0  # 0.75 * 100


def test_hermes_search_result_source_validation():
    """Test that source must be either 'hermes_browser' or 'hermes_web'."""
    from src.search.hermes_searcher import HermesSearchResult
    
    with pytest.raises(ValueError, match="Source must be either 'hermes_browser' or 'hermes_web'"):
        HermesSearchResult(
            title="Test Title",
            content="Test content",
            url="https://example.com",
            source="invalid_source",  # Invalid source
            relevance=0.8
        )


# Test HermesWebSearcher base class


def test_hermes_web_searcher_initialization():
    """Test HermesWebSearcher initialization."""
    from src.search.hermes_searcher import HermesWebSearcher
    
    searcher = HermesWebSearcher()
    
    assert searcher.search_methods == ['browser', 'web']
    assert searcher._cache == {}


def test_hermes_web_searcher_cache_key():
    """Test cache key generation method."""
    from src.search.hermes_searcher import HermesWebSearcher
    
    searcher = HermesWebSearcher()
    
    query = "test query"
    method = "browser"
    
    key = searcher._cache_key(query, method)
    
    # Cache key should be a deterministic string
    assert isinstance(key, str)
    assert key == f"{query}::{method}"


def test_hermes_web_searcher_get_cached_results_empty():
    """Test getting cached results when cache is empty."""
    from src.search.hermes_searcher import HermesWebSearcher
    
    searcher = HermesWebSearcher()
    
    cached = searcher._get_cached_results("test query", "browser")
    
    assert cached is None


def test_hermes_web_searcher_cache_and_retrieve_results():
    """Test caching and retrieving results."""
    from src.search.hermes_searcher import HermesWebSearcher, HermesSearchResult
    
    searcher = HermesWebSearcher()
    
    # Create test results
    results = [
        HermesSearchResult(
            title="Result 1",
            content="Content 1",
            url="https://example.com/1",
            source="hermes_browser",
            relevance=0.9
        ),
        HermesSearchResult(
            title="Result 2",
            content="Content 2",
            url="https://example.com/2",
            source="hermes_browser",
            relevance=0.7
        )
    ]
    
    # Cache the results
    searcher._cache_results("test query", "browser", results)
    
    # Retrieve cached results
    cached = searcher._get_cached_results("test query", "browser")
    
    assert cached is not None
    assert len(cached) == 2
    assert cached[0].title == "Result 1"
    assert cached[1].title == "Result 2"


def test_hermes_web_searcher_cache_expiration():
    """Test cache expiration based on timestamp."""
    from src.search.hermes_searcher import HermesWebSearcher, HermesSearchResult
    
    searcher = HermesWebSearcher()
    
    # Create test result
    result = HermesSearchResult(
        title="Test Result",
        content="Test Content",
        url="https://example.com",
        source="hermes_browser",
        relevance=0.8
    )
    
    # Cache the result with a mock timestamp
    with patch('time.time') as mock_time:
        # Set current time to 0
        mock_time.return_value = 0.0
        searcher._cache_results("test query", "browser", [result])
        
        # Try to retrieve with time 3599 (just before 1 hour expiration)
        mock_time.return_value = 3599.0
        cached = searcher._get_cached_results("test query", "browser")
        assert cached is not None  # Should still be cached
        
        # Now try with time 3601 (just after 1 hour expiration)
        mock_time.return_value = 3601.0
        cached = searcher._get_cached_results("test query", "browser")
        assert cached is None  # Should be expired


# Test CachedSearchResult dataclass (optional)


def test_cached_search_result_dataclass():
    """Test CachedSearchResult dataclass if implemented."""
    # Try to import it
    try:
        from src.search.hermes_searcher import CachedSearchResult
    except ImportError:
        pytest.skip("CachedSearchResult not implemented")
    
    from src.search.hermes_searcher import HermesSearchResult
    
    # Create test results
    results = [
        HermesSearchResult(
            title="Result 1",
            content="Content 1",
            url="https://example.com/1",
            source="hermes_browser",
            relevance=0.9
        )
    ]
    
    cached_result = CachedSearchResult(
        results=results,
        timestamp=1000.0,
        query="test query",
        expires=4600.0  # 1000 + 3600
    )
    
    assert cached_result.results == results
    assert cached_result.timestamp == 1000.0
    assert cached_result.query == "test query"
    assert cached_result.expires == 4600.0


def test_cached_search_result_default_expires():
    """Test CachedSearchResult with default expires value."""
    try:
        from src.search.hermes_searcher import CachedSearchResult
    except ImportError:
        pytest.skip("CachedSearchResult not implemented")
    
    from src.search.hermes_searcher import HermesSearchResult
    
    results = [
        HermesSearchResult(
            title="Result 1",
            content="Content 1",
            url="https://example.com/1",
            source="hermes_browser",
            relevance=0.9
        )
    ]
    
    with patch('time.time') as mock_time:
        mock_time.return_value = 1000.0
        cached_result = CachedSearchResult(
            results=results,
            timestamp=1000.0,
            query="test query"
        )
        
        # Default should be timestamp + 3600
        assert cached_result.expires == 4600.0


# Test search method addition (TASK 3)
# RED PHASE - these tests should fail initially

def test_hermes_web_searcher_search_method_exists():
    """Test that search method exists on HermesWebSearcher."""
    from src.search.hermes_searcher import HermesWebSearcher
    
    searcher = HermesWebSearcher()
    assert hasattr(searcher, 'search')
    assert callable(searcher.search)


def test_search_method_signature():
    """Test search method signature with default parameters."""
    from src.search.hermes_searcher import HermesWebSearcher
    from inspect import signature
    
    searcher = HermesWebSearcher()
    sig = signature(searcher.search)
    params = list(sig.parameters.keys())
    
    assert 'query' in params
    assert 'max_results' in params
    # Check default value for max_results
    default_max_results = sig.parameters['max_results'].default
    assert default_max_results == 5


def test_search_returns_list_of_hermes_search_results():
    """Test that search returns a list of HermesSearchResult objects."""
    from src.search.hermes_searcher import HermesWebSearcher, HermesSearchResult
    
    searcher = HermesWebSearcher()
    # Mock the internal search methods to return test data
    with patch.object(searcher, '_search_with_browser') as mock_browser, \
         patch.object(searcher, '_search_with_web') as mock_web:
        # Setup mock to return empty lists to test fallback logic
        mock_browser.return_value = []
        mock_web.return_value = []
        
        results = searcher.search("test query", max_results=5)
        
        assert isinstance(results, list)
        # Should return empty list if both methods return nothing


def test_search_with_browser_results():
    """Test search using browser method."""
    from src.search.hermes_searcher import HermesWebSearcher, HermesSearchResult
    
    searcher = HermesWebSearcher()
    
    # Create mock results
    mock_results = [
        HermesSearchResult(
            title="Browser Result 1",
            content="Content from browser",
            url="https://example.com/browser1",
            source="hermes_browser",
            relevance=0.9
        ),
        HermesSearchResult(
            title="Browser Result 2",
            content="More content from browser",
            url="https://example.com/browser2",
            source="hermes_browser", 
            relevance=0.7
        )
    ]
    
    with patch.object(searcher, '_search_with_browser') as mock_browser, \
         patch.object(searcher, '_search_with_web') as mock_web, \
         patch.object(searcher, '_process_results') as mock_process:
        
        mock_browser.return_value = mock_results
        mock_web.return_value = []
        mock_process.side_effect = lambda x: x  # Pass-through processing
        
        results = searcher.search("test query", max_results=5)
        
        # Should use browser method first
        mock_browser.assert_called_once_with("test query", 5)
        mock_web.assert_not_called()  # Should not call web if browser returns enough


def test_search_with_web_fallback():
    """Test web search fallback when browser returns insufficient results."""
    from src.search.hermes_searcher import HermesWebSearcher, HermesSearchResult
    
    searcher = HermesWebSearcher()
    
    # Browser returns only 2 results, need 5
    mock_browser_results = [
        HermesSearchResult(
            title="Browser Result 1",
            content="Content from browser",
            url="https://example.com/browser1",
            source="hermes_browser",
            relevance=0.9
        ),
        HermesSearchResult(
            title="Browser Result 2",
            content="More content from browser",
            url="https://example.com/browser2",
            source="hermes_browser",
            relevance=0.7
        )
    ]
    
    mock_web_results = [
        HermesSearchResult(
            title="Web Result 1",
            content="Content from web",
            url="https://example.com/web1",
            source="hermes_web",
            relevance=0.8
        ),
        HermesSearchResult(
            title="Web Result 2",
            content="More content from web",
            url="https://example.com/web2",
            source="hermes_web",
            relevance=0.6
        ),
        HermesSearchResult(
            title="Web Result 3",
            content="Even more content from web",
            url="https://example.com/web3",
            source="hermes_web",
            relevance=0.5
        )
    ]
    
    with patch.object(searcher, '_search_with_browser') as mock_browser, \
         patch.object(searcher, '_search_with_web') as mock_web, \
         patch.object(searcher, '_process_results') as mock_process:
        
        mock_browser.return_value = mock_browser_results
        mock_web.return_value = mock_web_results
        mock_process.side_effect = lambda x: x  # Pass-through processing
        
        results = searcher.search("test query", max_results=5)
        
        # Should call both methods
        mock_browser.assert_called_once_with("test query", 5)
        mock_web.assert_called_once_with("test query", 5 - len(mock_browser_results))


def test_search_result_processing():
    """Test that search results are processed and deduplicated."""
    from src.search.hermes_searcher import HermesWebSearcher, HermesSearchResult
    
    searcher = HermesWebSearcher()
    
    # Create results with potential duplicates
    raw_results = [
        HermesSearchResult(
            title="Duplicate Title",
            content="Content 1",
            url="https://example.com/1",
            source="hermes_browser",
            relevance=0.9
        ),
        HermesSearchResult(
            title="Duplicate Title",  # Same title, different URL
            content="Content 2",
            url="https://example.com/2",
            source="hermes_browser",
            relevance=0.8
        ),
        HermesSearchResult(
            title="Unique Title",
            content="Unique content",
            url="https://example.com/3",
            source="hermes_browser",
            relevance=0.7
        )
    ]
    
    # Mock the processing method to simulate deduplication
    with patch.object(searcher, '_search_with_browser') as mock_browser, \
         patch.object(searcher, '_search_with_web') as mock_web, \
         patch.object(searcher, '_process_results') as mock_process:
        
        mock_browser.return_value = raw_results
        mock_web.return_value = []
        
        # Simulate deduplication - remove duplicate based on title
        deduplicated = [raw_results[0], raw_results[2]]  # Keep first and third
        
        # Make _process_results actually process
        def process_func(results):
            # Simple deduplication by title
            seen_titles = set()
            processed = []
            for result in results:
                if result.title not in seen_titles:
                    seen_titles.add(result.title)
                    processed.append(result)
            return processed
        
        mock_process.side_effect = process_func
        
        results = searcher.search("test query", max_results=5)
        
        # Should call processing
        mock_process.assert_called_once()
        # Should have deduplicated results (2 instead of 3)
        assert len(results) == 2


def test_search_caching():
    """Test that search results are cached."""
    from src.search.hermes_searcher import HermesWebSearcher, HermesSearchResult
    
    searcher = HermesWebSearcher()
    
    mock_results = [
        HermesSearchResult(
            title="Cached Result",
            content="Cached content",
            url="https://example.com/cached",
            source="hermes_browser",
            relevance=0.9
        )
    ]
    
    with patch.object(searcher, '_search_with_browser') as mock_browser, \
         patch.object(searcher, '_search_with_web') as mock_web, \
         patch.object(searcher, '_process_results') as mock_process, \
         patch.object(searcher, '_get_cached_results') as mock_get_cached, \
         patch.object(searcher, '_cache_results') as mock_cache:
        
        # First call: no cache, should perform search
        mock_get_cached.return_value = None
        mock_browser.return_value = mock_results
        mock_web.return_value = []
        mock_process.side_effect = lambda x: x
        
        results = searcher.search("test query", max_results=5)
        
        # Should check cache
        mock_get_cached.assert_called()
        # Should cache results
        mock_cache.assert_called_once()
        
        # Reset mocks for second call
        mock_get_cached.reset_mock()
        mock_browser.reset_mock()
        mock_cache.reset_mock()
        
        # Second call: cache hit
        mock_get_cached.return_value = mock_results
        
        results2 = searcher.search("test query", max_results=5)
        
        # Should get from cache
        mock_get_cached.assert_called()
        # Should NOT perform new search
        mock_browser.assert_not_called()
        # Should NOT cache again
        mock_cache.assert_not_called()


def test_search_with_browser_method():
    """Test _search_with_browser method implementation."""
    from src.search.hermes_searcher import HermesWebSearcher
    
    searcher = HermesWebSearcher()
    assert hasattr(searcher, '_search_with_browser')
    assert callable(searcher._search_with_browser)
    
    # Test signature
    from inspect import signature
    sig = signature(searcher._search_with_browser)
    params = list(sig.parameters.keys())
    assert 'query' in params
    assert 'max_results' in params


def test_search_with_web_method():
    """Test _search_with_web method implementation."""
    from src.search.hermes_searcher import HermesWebSearcher
    
    searcher = HermesWebSearcher()
    assert hasattr(searcher, '_search_with_web')
    assert callable(searcher._search_with_web)
    
    # Test signature
    from inspect import signature
    sig = signature(searcher._search_with_web)
    params = list(sig.parameters.keys())
    assert 'query' in params
    assert 'max_results' in params


def test_process_results_method():
    """Test _process_results method implementation."""
    from src.search.hermes_searcher import HermesWebSearcher
    
    searcher = HermesWebSearcher()
    assert hasattr(searcher, '_process_results')
    assert callable(searcher._process_results)
    
    # Test signature
    from inspect import signature
    sig = signature(searcher._process_results)
    params = list(sig.parameters.keys())
    assert 'results' in params


if __name__ == "__main__":
    pytest.main([__file__, "-v"])