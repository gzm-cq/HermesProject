"""测试共享 Fixture — 用于 knowledge-tree-plugin 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# 确保 knowledge-tree-builder 源码可导入
_KT_SRC = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "scripts" / "knowledge-tree-builder" / "src"
)
if str(_KT_SRC) not in sys.path:
    sys.path.insert(0, str(_KT_SRC))


@pytest.fixture
def mock_adapter() -> MagicMock:
    """模拟 PluginDatabaseAdapter。"""
    from knowledge_tree_plugin.placement import _reset_leaf_cache

    _reset_leaf_cache()

    adapter = MagicMock()

    # 模拟知识树结构
    adapter.search_subjects_by_keywords.return_value = [
        {
            "id": 1, "name": "半导体工程", "node_type": "domain",
            "parent_id": None, "k_vector": [0.1] * 1024,
            "local_offset": None, "depth": 0,
        },
        {
            "id": 2, "name": "基础理论", "node_type": "subject",
            "parent_id": 1, "k_vector": [0.2] * 1024,
            "local_offset": None, "depth": 1,
        },
    ]
    adapter.get_domain_nodes.return_value = [
        {"id": 1, "name": "半导体工程", "k_vector": [0.1] * 1024},
    ]
    adapter.get_child_nodes.return_value = [
        {
            "id": 10, "name": "欧姆定律", "node_type": "knowledge_point",
            "k_vector": [0.15] * 1024, "text": "V=IR — 欧姆定律",
        },
        {
            "id": 11, "name": "基尔霍夫定律", "node_type": "knowledge_point",
            "k_vector": [0.16] * 1024, "text": "KCL/KVL 基尔霍夫定律",
        },
    ]
    adapter.get_leaf_nodes.return_value = [
        {
            "id": 10, "name": "欧姆定律",
            "k_vector": [0.15] * 1024,
        },
        {
            "id": 11, "name": "基尔霍夫定律",
            "k_vector": [0.16] * 1024,
        },
    ]
    adapter.get_sibling_points.return_value = []
    adapter.get_placement_count.return_value = 0
    adapter.get_node_embedding.return_value = None
    adapter.insert_node.return_value = 100
    adapter.insert_point_text.return_value = 200
    return adapter


@pytest.fixture
def sample_query() -> str:
    """示例用户查询。"""
    return "什么是欧姆定律？在电路分析中怎么应用？"


@pytest.fixture
def sample_embedding() -> list[float]:
    """1024 维示例 embedding。"""
    return [0.1] * 1024
