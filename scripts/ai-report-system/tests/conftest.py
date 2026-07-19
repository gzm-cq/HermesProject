"""Pytest 配置：统一 sys.path 注入，避免每个测试文件重复。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Ensure src and scripts are on sys.path for all tests
for p in (str(PROJECT_DIR / "src"), str(PROJECT_DIR / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers to suppress PytestUnknownMarkWarning."""
    config.addinivalue_line("markers", "unit: Tests that require no file IO (pure logic).")
    config.addinivalue_line("markers", "integration: Tests that involve real file IO.")