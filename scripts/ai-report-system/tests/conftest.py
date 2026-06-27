"""pytest 全局 conftest — 提供测试必需的环境变量。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _set_dify_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """自动设置 Dify 测试必需的环境变量（DifyKBRetriever 构造时强制检查）。"""
    monkeypatch.setenv("DIFY_DATASET_ID", "test-dataset-id")
    monkeypatch.setenv("DIFY_DATASET_API_KEY", "test-api-key")
