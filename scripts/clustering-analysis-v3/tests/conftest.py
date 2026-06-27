"""全局测试 fixtures"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from clustering_analysis.config import AppConfig


@pytest.fixture
def default_config() -> AppConfig:
    """返回默认测试配置"""
    return AppConfig()


@pytest.fixture
def mock_db_adapter() -> MagicMock:
    """返回 mock 的数据库适配器"""
    adapter = MagicMock()
    adapter.fetch_memory_units.return_value = [
        (1, "hermes", "测试文本1", "[0.1, 0.2, 0.3]"),
        (2, "hermes", "测试文本2", "[0.2, 0.3, 0.4]"),
        (3, "hermes", "测试文本3", "[0.3, 0.4, 0.5]"),
    ]
    adapter.fetch_unit_entities.return_value = [
        (1, "entity_1"),
        (2, "entity_1"),
        (2, "entity_2"),
    ]
    return adapter


@pytest.fixture
def sample_embeddings() -> np.ndarray:
    """返回小型测试 embedding 矩阵"""
    rng = np.random.default_rng(42)
    return rng.random((10, 8)).astype(np.float32)
