"""public_api 模块测试 — recall_from_tree / recall_from_tree_raw / _recall_core."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

from knowledge_tree_plugin import public_api


# ===== _recall_core 基础测试 =====


def test_recall_core_returns_empty_on_no_config() -> None:
    """配置加载失败时 graceful fallback。"""
    kps, adapter, owns = public_api._recall_core(
        "test", "test query", cfg=MagicMock(), adapter=MagicMock()
    )
    assert kps == []
    assert owns is False


def test_recall_core_returns_empty_when_db_url_missing() -> None:
    """KT_DB_URL 未配置时返回空。"""
    cfg = MagicMock()
    cfg.db_url = ""
    with patch.dict("os.environ", {}, clear=True):
        kps, adapter, owns = public_api._recall_core("test", "query", cfg=cfg, adapter=None)
    assert kps == []
    assert adapter is None
    assert owns is False


def test_recall_core_owns_adapter_on_creation() -> None:
    """未传入 adapter 时，_recall_core 创建并 owns_adapter=True。"""
    cfg = MagicMock()
    cfg.db_url = "postgresql://test/db"
    cfg.embed_base_url = "https://api.test.com"
    cfg.embed_model = "test-model"
    cfg.embed_api_key = "test-key"
    cfg.cold_start_threshold = 20
    cfg.recall_min_score = 0.5
    cfg.max_recall_results = 10

    mock_adapter = MagicMock()
    with patch.object(public_api, "PluginDatabaseAdapter", return_value=mock_adapter):
        with patch.object(public_api, "batch_embed", return_value=[[0.1] * 1024]):
            with patch.object(public_api, "locate_subject", return_value=None):
                kps, adapter, owns = public_api._recall_core("test", "query", cfg=cfg, adapter=None)
    assert owns is True
    assert adapter is mock_adapter


def test_recall_core_query_embed_empty_returns_early() -> None:
    """query embedding 为空时提前返回。"""
    cfg = MagicMock()
    cfg.db_url = "postgresql://test/db"
    cfg.embed_base_url = "https://api.test.com"
    cfg.embed_model = "test-model"
    cfg.embed_api_key = "test-key"

    mock_adapter = MagicMock()
    with patch.object(public_api, "batch_embed", return_value=[]):
        kps, adapter, owns = public_api._recall_core("test", "query", cfg=cfg, adapter=mock_adapter)
    assert kps == []
    assert adapter is mock_adapter
    assert owns is False  # adapter was passed in, not owned


# ===== recall_from_tree 测试 =====


def test_recall_from_tree_returns_none_on_no_results() -> None:
    """无可匹配知识点时返回 None。"""
    with patch.object(public_api, "_recall_core", return_value=([], None, False)):
        result = public_api.recall_from_tree("session", "query")
    assert result is None


def test_recall_from_tree_formats_results() -> None:
    """有结果时返回格式化文本。"""
    kps = [{"id": 1, "name": "test", "text": "知识点内容", "score": 0.9}]
    with patch.object(public_api, "_recall_core", return_value=(kps, None, False)):
        with patch.object(public_api, "format_context_lines", return_value=["- 知识点内容"]):
            result = public_api.recall_from_tree("session", "query")
    assert result == "- 知识点内容"


def test_recall_from_tree_closes_owned_adapter() -> None:
    """owns_adapter=True 时自动关闭 adapter。"""
    mock_adapter = MagicMock()
    with patch.object(public_api, "_recall_core", return_value=([], mock_adapter, True)):
        public_api.recall_from_tree("session", "query")
    mock_adapter.close.assert_called_once()


def test_recall_from_tree_does_not_close_borrowed_adapter() -> None:
    """owns_adapter=False 时不关闭 adapter。"""
    mock_adapter = MagicMock()
    with patch.object(public_api, "_recall_core", return_value=([], mock_adapter, False)):
        public_api.recall_from_tree("session", "query")
    mock_adapter.close.assert_not_called()


# ===== recall_from_tree_raw 测试 =====


def test_recall_from_tree_raw_returns_empty_on_no_results() -> None:
    """无可匹配知识点时返回空列表。"""
    with patch.object(public_api, "_recall_core", return_value=([], None, False)):
        result = public_api.recall_from_tree_raw("session", "query")
    assert result == []


def test_recall_from_tree_raw_returns_kp_list() -> None:
    """有结果时返回知识点列表。"""
    kps = [{"id": 1, "name": "test", "text": "内容", "score": 0.8}]
    with patch.object(public_api, "_recall_core", return_value=(kps, None, False)):
        result = public_api.recall_from_tree_raw("session", "query")
    assert result == kps


def test_recall_from_tree_raw_closes_owned_adapter() -> None:
    """owns_adapter=True 时自动关闭 adapter。"""
    mock_adapter = MagicMock()
    with patch.object(public_api, "_recall_core", return_value=([], mock_adapter, True)):
        public_api.recall_from_tree_raw("session", "query")
    mock_adapter.close.assert_called_once()
