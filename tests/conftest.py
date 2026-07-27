"""Root conftest — exclude standalone scripts from plugin conftest autouse fixtures."""

import pytest


def pytest_configure(config):
    """Register custom marker for standalone test scripts."""
    config.addinivalue_line("markers", "standalone: script meant to run as python, not via pytest.")


def pytest_collection_modifyitems(config, items):
    """Skip autouse fixtures for standalone scripts during collection."""
    for item in items:
        if "standalone" in item.keywords:
            # Mark these tests to skip the knowledge-navigation autouse fixtures
            pass  # handled in session-scoped fixture below
