"""test_router_emb_cache.py — Router embedding 语义缓存增强验收测试。

覆盖（2026-09-04 上线）：
1. 同 session 换说法（embedding 相似 ≥0.85）→ cache_hit_emb 复用决策
2. 跨 session 相同语义 → 不命中（安全约束：仅同 session 启用）
3. embedding 失败 → 静默降级（不影响主链路）
4. 精确缓存优先级 > 前缀语义 > embedding 语义
5. 命中后正确返回 mask + meta
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import knowledge_navigation.core.router as rtr  # noqa: E402


def _emb_vec(seed: int) -> np.ndarray:
    """确定性归一化向量（模拟 bge-m3 embedding，1024 维）。"""
    rng = np.random.default_rng(seed)
    v = rng.normal(0, 1.0, 1024).astype(np.float32)
    n = np.linalg.norm(v)
    return (v / n) if n > 0 else v


def _fake_emb_config():
    return ("bge-m3", "http://127.0.0.1:8082/v1", "local-only", 20)


@pytest.fixture(autouse=True)
def reset_cache():
    """每测试清空全局缓存，避免污染。"""
    with rtr._router_lock:
        rtr._router_cache.clear()
        rtr._router_cache_timestamps.clear()
        rtr._router_cache_embs.clear()
        rtr._emb_text_cache.clear()
        rtr._session_last_mask.clear()
    yield
    with rtr._router_lock:
        rtr._router_cache.clear()
        rtr._router_cache_timestamps.clear()
        rtr._router_cache_embs.clear()
        rtr._emb_text_cache.clear()
        rtr._session_last_mask.clear()


class TestEmbeddingSemanticCache:
    def test_same_session_paraphrase_hits(self):
        """同 session 换说法（相似 ≥0.85）→ cache_hit_emb。"""
        mask_a = {"h": True, "kt": False, "s": True, "sag": True}
        key_a = ("sess-1", "帮我分析Excel销售数据")
        # 预置一条 LLM 成功决策 + embedding
        rtr._router_cache[key_a] = mask_a
        rtr._router_cache_timestamps[key_a] = __import__("time").time()
        rtr._router_cache_embs[key_a] = _emb_vec(1)

        # 同 session 换说法：embedding 相似 → 命中
        with patch.object(rtr, "_get_router_embedding",
                         return_value=_emb_vec(1)) as mock_emb:
            result = rtr._router_embedding_lookup("sess-1", "接着看利润率")

        assert result == mask_a
        mock_emb.assert_called_once()

    def test_cross_session_no_hit(self):
        """跨 session 相同语义 → 不命中（安全约束）。"""
        mask_a = {"h": True, "kt": False, "s": True, "sag": True}
        key_a = ("sess-1", "帮我分析Excel销售数据")
        rtr._router_cache[key_a] = mask_a
        rtr._router_cache_timestamps[key_a] = __import__("time").time()
        rtr._router_cache_embs[key_a] = _emb_vec(1)

        # 不同 session（即使 embedding 相同）→ 不命中
        result = rtr._router_embedding_lookup("sess-999", "接着看利润率")
        assert result is None

    def test_low_similarity_no_hit(self):
        """相似度 <0.85 → 不命中（防误复用）。"""
        mask_a = {"h": True, "kt": False, "s": True, "sag": True}
        key_a = ("sess-1", "帮我分析Excel销售数据")
        rtr._router_cache[key_a] = mask_a
        rtr._router_cache_timestamps[key_a] = __import__("time").time()
        rtr._router_cache_embs[key_a] = _emb_vec(1)  # 正交向量，相似≈0

        result = rtr._router_embedding_lookup("sess-1", "完全不同的话题")
        assert result is None

    def test_embedding_failure_degrades(self):
        """embedding 获取失败 → 返回 None（不影响主链路）。"""
        with patch.object(rtr, "_get_router_embedding", return_value=None):
            result = rtr._router_embedding_lookup("sess-1", "任何话")
        assert result is None

    def test_route_integration_emb_hit(self):
        """route() 完整链路：精确 miss → 前缀 miss → embedding 命中。"""
        mask_a = {"h": True, "kt": False, "s": True, "sag": True}
        key_a = ("sess-1", "帮我分析Excel销售数据")
        rtr._router_cache[key_a] = mask_a
        rtr._router_cache_timestamps[key_a] = __import__("time").time()
        rtr._router_cache_embs[key_a] = _emb_vec(1)

        with patch.object(rtr, "_get_router_embedding", return_value=_emb_vec(1)), \
             patch.object(rtr, "_fetch_api_key", return_value="key"), \
             patch.object(rtr, "_call_router_llm") as mock_llm:
            mask, meta = rtr.route(
                "sess-1", "接着看利润率", "model", "http://127.0.0.1:4142/v1", "key", 15,
            )

        assert mask == mask_a
        assert meta["fallback_reason"] == "cache_hit_emb"
        assert meta["is_fallback"] is False
        mock_llm.assert_not_called()  # 没走 LLM

    def test_exact_cache_priority(self):
        """精确缓存优先级 > embedding 语义。"""
        mask_a = {"h": True, "kt": False, "s": True, "sag": True}
        key_a = ("sess-1", "帮我分析Excel销售数据")
        rtr._router_cache[key_a] = mask_a
        rtr._router_cache_timestamps[key_a] = __import__("time").time()
        rtr._router_cache_embs[key_a] = _emb_vec(1)

        with patch.object(rtr, "_get_router_embedding") as mock_emb, \
             patch.object(rtr, "_fetch_api_key", return_value="key"), \
             patch.object(rtr, "_call_router_llm") as mock_llm:
            mask, meta = rtr.route(
                "sess-1", "帮我分析Excel销售数据", "model", "http://127.0.0.1:4142/v1", "key", 15,
            )

        assert mask == mask_a
        assert meta["fallback_reason"] == "cache_hit"
        mock_emb.assert_not_called()  # 精确命中不触发 embedding
        mock_llm.assert_not_called()
