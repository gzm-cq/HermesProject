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


def test_recall_core_uses_pooled_adapter() -> None:
    """S-2: 未传 adapter 时使用 thread-local 池化 adapter，不再创建新实例。"""
    cfg = MagicMock()
    cfg.db_url = "postgresql://test/db"
    cfg.embed_base_url = "https://api.test.com"
    cfg.embed_model = "test-model"
    cfg.embed_api_key = "test-key"
    cfg.cold_start_threshold = 20
    cfg.recall_min_score = 0.5
    cfg.max_recall_results = 10

    mock_adapter = MagicMock()
    mock_get = MagicMock(return_value=mock_adapter)
    with patch.object(public_api, "_get_thread_adapter", mock_get):
        with patch.object(public_api, "PluginDatabaseAdapter") as mock_cls:
            with patch.object(public_api, "batch_embed", return_value=[[0.1] * 1024]):
                with patch.object(public_api, "locate_subject", return_value=None):
                    kps, adapter, _owns = public_api._recall_core("test", "query", cfg=cfg, adapter=None)
    mock_get.assert_called_once_with("postgresql://test/db")
    assert mock_cls.call_count == 0  # 不再直接创建
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


def test_recall_from_tree_pooled_adapter_not_closed() -> None:
    """S-2: 池化 adapter 由 pool 管理，recall_from_tree 不再主动关闭。"""
    mock_adapter = MagicMock()
    with patch.object(public_api, "_recall_core", return_value=([], mock_adapter, False)):
        public_api.recall_from_tree("session", "query")
    mock_adapter.close.assert_not_called()


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


# ===== 主流程跨域多跳扩展（2026-08-30 下沉） =====


def _mock_full_recall_cfg() -> MagicMock:
    """构造走完整召回流程的 cfg（含跨域扩展开关与 top_k）。"""
    cfg = MagicMock()
    cfg.db_url = "postgresql://test/db"
    cfg.embed_base_url = "https://api.test.com"
    cfg.embed_model = "test-model"
    cfg.embed_api_key = "test-key"
    cfg.cold_start_threshold = 20
    cfg.recall_min_score = 0.3
    cfg.max_recall_results = 5
    cfg.enable_temporal_filter = False
    cfg.enable_multi_hop_expand = True
    cfg.multi_hop_top_k = 4
    return cfg


def test_recall_core_appends_cross_domain_results() -> None:
    """Step 3.6: attention_filter 结果作 seed，跨域 KP 合入返回，带 source 标记。"""
    cfg = _mock_full_recall_cfg()
    adapter = MagicMock()
    seed_kps = [{"id": 10, "name": "欧姆定律", "text": "V=IR", "score": 0.9}]
    cross_domain = [
        {"id": 99, "name": "跨域KP", "text": "另一个科目的关联知识点", "score": 0.33, "strategy": "edge"},
    ]

    with patch.object(public_api, "batch_embed", return_value=[[0.1] * 1024]):
        with patch.object(public_api, "locate_subject", return_value={
            "id": 2, "name": "基础理论", "child_count": 5,
            "children": [{"id": 10, "name": "欧姆定律", "k_vector": [0.15] * 1024, "text": "V=IR"}],
            "local_offset": None,
        }):
            with patch.object(public_api, "attention_filter", return_value=seed_kps):
                with patch.object(public_api, "multi_hop_recall", return_value=cross_domain):
                    with patch.object(public_api, "log_use"):
                        kps, _adapter, _owns = public_api._recall_core("session", "query", cfg=cfg, adapter=adapter)

    # 原 seed 保留 + 跨域 KP 追加并打 source 标记
    assert len(kps) == 2
    assert kps[0]["id"] == 10
    by_id = {k["id"]: k for k in kps}
    assert by_id[99]["source"] == "multi-hop"
    # seed 自身不打 cross-domain 标记
    assert "source" not in by_id[10]


def test_recall_core_dedups_cross_domain_with_seed() -> None:
    """跨域结果与 seed 撞 id 时不重复追加。"""
    cfg = _mock_full_recall_cfg()
    adapter = MagicMock()
    seed_kps = [{"id": 10, "name": "欧姆定律", "text": "V=IR", "score": 0.9}]
    # 撞 id：multi_hop_recall 把 seed 自己返回来
    dup = [{"id": 10, "name": "欧姆定律", "text": "V=IR", "score": 0.9, "strategy": "edge"}]

    with patch.object(public_api, "batch_embed", return_value=[[0.1] * 1024]):
        with patch.object(public_api, "locate_subject", return_value={
            "id": 2, "name": "基础理论", "child_count": 5,
            "children": [{"id": 10, "name": "欧姆定律", "k_vector": [0.15] * 1024, "text": "V=IR"}],
            "local_offset": None,
        }):
            with patch.object(public_api, "attention_filter", return_value=seed_kps):
                with patch.object(public_api, "multi_hop_recall", return_value=dup):
                    with patch.object(public_api, "log_use"):
                        kps, _adapter, _owns = public_api._recall_core("session", "query", cfg=cfg, adapter=adapter)

    assert len(kps) == 1  # 不重复追加


def test_recall_core_disabled_expand_keeps_seed_only() -> None:
    """关闭扩展时仅返回 attention_filter 结果，不调 multi_hop_recall。"""
    cfg = _mock_full_recall_cfg()
    cfg.enable_multi_hop_expand = False
    adapter = MagicMock()
    seed_kps = [{"id": 10, "name": "欧姆定律", "text": "V=IR", "score": 0.9}]

    with patch.object(public_api, "batch_embed", return_value=[[0.1] * 1024]):
        with patch.object(public_api, "locate_subject", return_value={
            "id": 2, "name": "基础理论", "child_count": 5,
            "children": [{"id": 10, "name": "欧姆定律", "k_vector": [0.15] * 1024, "text": "V=IR"}],
            "local_offset": None,
        }):
            with patch.object(public_api, "attention_filter", return_value=seed_kps):
                with patch.object(public_api, "multi_hop_recall") as mock_mh:
                    with patch.object(public_api, "log_use"):
                        kps, _adapter, _owns = public_api._recall_core("session", "query", cfg=cfg, adapter=adapter)

    assert kps == seed_kps
    mock_mh.assert_not_called()


def test_recall_from_tree_raw_pooled_adapter_not_closed() -> None:
    """S-2: 池化 adapter由 pool 管理，recall_from_tree_raw 不再主动关闭。"""
    mock_adapter = MagicMock()
    with patch.object(public_api, "_recall_core", return_value=([], mock_adapter, False)):
        public_api.recall_from_tree_raw("session", "query")
    mock_adapter.close.assert_not_called()
