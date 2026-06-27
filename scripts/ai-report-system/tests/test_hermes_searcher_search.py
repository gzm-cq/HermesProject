#!/usr/bin/env python3
"""
Test module for HermesWebSearcher search method and internal methods.
Following strict TDD methodology - RED phase.
"""

import pytest
from unittest.mock import Mock, patch


# Test search method addition (TASK 3)
# RED PHASE - these tests should fail initially


@pytest.mark.unit
def test_hermes_web_searcher_search_method_exists():
    """Test that search method exists on HermesWebSearcher."""
    from ai_report.adapters.web_search import HermesWebSearcher

    searcher = HermesWebSearcher()
    assert hasattr(searcher, 'search')
    assert callable(searcher.search)


@pytest.mark.unit
def test_search_method_signature():
    """Test search method signature with default parameters."""
    from ai_report.adapters.web_search import HermesWebSearcher
    from inspect import signature

    searcher = HermesWebSearcher()
    sig = signature(searcher.search)
    params = list(sig.parameters.keys())

    assert 'query' in params
    assert 'max_results' in params
    # Check default value for max_results
    default_max_results = sig.parameters['max_results'].default
    assert default_max_results == 5


@pytest.mark.unit
def test_search_returns_list_of_hermes_search_results():
    """Test that search returns a list of HermesSearchResult objects."""
    from ai_report.adapters.web_search import HermesWebSearcher, HermesSearchResult

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


@pytest.mark.unit
def test_search_with_browser_results():
    """Test search using browser method."""
    from ai_report.adapters.web_search import HermesWebSearcher, HermesSearchResult

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

        results = searcher.search("test query", max_results=2)

        # Should use browser method first
        mock_browser.assert_called_once_with("test query", 2)
        mock_web.assert_not_called()  # Should not call web if browser returns enough


@pytest.mark.unit
def test_search_with_web_fallback():
    """Test web search fallback when browser returns insufficient results."""
    from ai_report.adapters.web_search import HermesWebSearcher, HermesSearchResult

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


@pytest.mark.unit
def test_search_result_processing():
    """Test that search results are processed and deduplicated."""
    from ai_report.adapters.web_search import HermesWebSearcher, HermesSearchResult

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


@pytest.mark.unit
def test_search_caching():
    """Test that search results are cached."""
    from ai_report.adapters.web_search import HermesWebSearcher, HermesSearchResult

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


@pytest.mark.unit
def test_search_with_browser_method():
    """Test _search_with_browser method implementation."""
    from ai_report.adapters.web_search import HermesWebSearcher

    searcher = HermesWebSearcher()
    assert hasattr(searcher, '_search_with_browser')
    assert callable(searcher._search_with_browser)

    # Test signature
    from inspect import signature
    sig = signature(searcher._search_with_browser)
    params = list(sig.parameters.keys())
    assert 'query' in params
    assert 'max_results' in params


@pytest.mark.unit
def test_search_with_web_method():
    """Test _search_with_web method implementation."""
    from ai_report.adapters.web_search import HermesWebSearcher

    searcher = HermesWebSearcher()
    assert hasattr(searcher, '_search_with_web')
    assert callable(searcher._search_with_web)

    # Test signature
    from inspect import signature
    sig = signature(searcher._search_with_web)
    params = list(sig.parameters.keys())
    assert 'query' in params
    assert 'max_results' in params


@pytest.mark.unit
def test_process_results_method():
    """Test _process_results method implementation."""
    from ai_report.adapters.web_search import HermesWebSearcher

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
