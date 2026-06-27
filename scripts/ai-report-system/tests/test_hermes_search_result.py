#!/usr/bin/env python3
"""
Test module for HermesSearchResult dataclass.
Following strict TDD methodology - RED phase.
"""

import pytest


# Test HermesSearchResult dataclass


@pytest.mark.unit
def test_hermes_search_result_creation():
    """Test basic creation of HermesSearchResult dataclass."""
    # This will fail initially because the class doesn't exist yet
    from ai_report.adapters.web_search import HermesSearchResult

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


@pytest.mark.unit
def test_hermes_search_result_without_url():
    """Test HermesSearchResult creation without URL."""
    from ai_report.adapters.web_search import HermesSearchResult

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


@pytest.mark.unit
def test_hermes_search_result_validation_title():
    """Test validation for non-empty title."""
    from ai_report.adapters.web_search import HermesSearchResult

    with pytest.raises(ValueError, match="Title cannot be empty"):
        HermesSearchResult(
            title="",  # Empty title should raise ValueError
            content="Test content",
            url="https://example.com",
            source="hermes_browser",
            relevance=0.8
        )


@pytest.mark.unit
def test_hermes_search_result_validation_content():
    """Test validation for non-empty content."""
    from ai_report.adapters.web_search import HermesSearchResult

    with pytest.raises(ValueError, match="Content cannot be empty"):
        HermesSearchResult(
            title="Test Title",
            content="",  # Empty content should raise ValueError
            url="https://example.com",
            source="hermes_browser",
            relevance=0.8
        )


@pytest.mark.unit
def test_hermes_search_result_validation_relevance_range():
    """Test validation for relevance range (0.0 to 1.0)."""
    from ai_report.adapters.web_search import HermesSearchResult

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


@pytest.mark.unit
def test_hermes_search_result_relevance_score_property():
    """Test relevance_score property that converts 0-1 to 0-100."""
    from ai_report.adapters.web_search import HermesSearchResult

    result = HermesSearchResult(
        title="Test Title",
        content="Test content",
        url="https://example.com",
        source="hermes_browser",
        relevance=0.75
    )

    assert result.relevance_score == 75.0  # 0.75 * 100


@pytest.mark.unit
def test_hermes_search_result_source_validation():
    """Test that source must be either 'hermes_browser' or 'hermes_web'."""
    from ai_report.adapters.web_search import HermesSearchResult

    with pytest.raises(ValueError, match="Source must be either 'hermes_browser' or 'hermes_web'"):
        HermesSearchResult(
            title="Test Title",
            content="Test content",
            url="https://example.com",
            source="invalid_source",  # Invalid source
            relevance=0.8
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
