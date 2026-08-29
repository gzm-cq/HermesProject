"""skill embedding 磁盘缓存的单元测试（2026-08-29 P0 改造）。

覆盖点：
  1. 文本未变更 -> 命中缓存，零 API 调用
  2. skill 文本变更 -> 仅该条重新编码
  3. 模型/服务地址变更 -> 磁盘缓存整体失效
  4. 落盘文件带正确指纹，可被重新载入
  5. 缓存文件损坏 -> 静默降级，不抛异常

背景：冷启动原本要串行编码 426 个 skill（约 145-215s），远超
skill_timeout_seconds=60，导致网关重启后前若干次请求 skill 路超时。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import knowledge_navigation.core.skill_matcher as sm  # noqa: E402

MODEL = "BAAI/bge-m3"
URL = "http://127.0.0.1:8082/v1"
DIM = 8  # 测试用小维度，避免构造 1024 维


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    """每个用例重置缓存状态，并把磁盘缓存指向临时目录。"""
    monkeypatch.setattr(sm, "_embedding_cache", __import__("collections").OrderedDict())
    monkeypatch.setattr(sm, "_embedding_text_hash", {})
    monkeypatch.setattr(sm, "_embedding_disk_cache_loaded", False)
    monkeypatch.setattr(sm, "_EMBEDDING_DISK_CACHE_PATH", tmp_path / "skill_embeddings.npz")
    monkeypatch.setattr(sm, "_embedding_disk_lock", threading.Lock())
    monkeypatch.setattr(sm, "_embedding_cache_lock", threading.Lock())
    yield


def _skill(path: str, name: str, desc: str) -> dict:
    return {"path": path, "name": name, "description": desc}


def _fake_post(dim: int = DIM):
    """构造假的 /embeddings 响应：按 input 顺序返回确定性向量。"""

    def _post(url, json=None, headers=None, timeout=None, **kw):
        texts = (json or {}).get("input", [])
        payload = {
            "data": [
                {"index": i, "embedding": [float(i + 1)] * dim}
                for i in range(len(texts))
            ]
        }
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        return resp

    return _post


def test_cache_hit_avoids_api_call():
    """文本未变更时应命中缓存，不再调用 embedding API。"""
    skills = [_skill("/a/SKILL.md", "alpha", "desc-a")]

    with mock.patch("httpx.post", side_effect=_fake_post()) as post:
        sm._get_skill_embeddings(skills, MODEL, URL, "key")
        assert post.call_count == 1, "首次应编码"

    # 第二进程/第二次调用：磁盘缓存已落盘，重置内存后应直接从磁盘恢复
    sm._embedding_cache.clear()
    sm._embedding_text_hash.clear()
    sm._embedding_disk_cache_loaded = False

    with mock.patch("httpx.post", side_effect=_fake_post()) as post:
        result = sm._get_skill_embeddings(skills, MODEL, URL, "key")
        assert post.call_count == 0, "文本未变更，不应再调用 API"
    assert "/a/SKILL.md" in result


def test_text_change_triggers_reencode():
    """skill 描述变更后，该条必须重新编码，不能沿用旧向量。"""
    old = [_skill("/a/SKILL.md", "alpha", "desc-a")]
    with mock.patch("httpx.post", side_effect=_fake_post()):
        sm._get_skill_embeddings(old, MODEL, URL, "key")
    assert sm._EMBEDDING_DISK_CACHE_PATH.exists(), "应已落盘"

    # 模拟新进程：从磁盘恢复
    sm._embedding_cache.clear()
    sm._embedding_text_hash.clear()
    sm._embedding_disk_cache_loaded = False

    changed = [_skill("/a/SKILL.md", "alpha", "desc-a-CHANGED")]
    with mock.patch("httpx.post", side_effect=_fake_post()) as post:
        sm._get_skill_embeddings(changed, MODEL, URL, "key")
        assert post.call_count == 1, "文本变更，必须重新编码"


def test_model_change_invalidates_disk_cache():
    """换模型后向量空间不同，磁盘缓存必须整体失效。"""
    skills = [_skill("/a/SKILL.md", "alpha", "desc-a")]
    with mock.patch("httpx.post", side_effect=_fake_post()):
        sm._get_skill_embeddings(skills, MODEL, URL, "key")
    assert sm._EMBEDDING_DISK_CACHE_PATH.exists()

    sm._embedding_cache.clear()
    sm._embedding_text_hash.clear()
    sm._embedding_disk_cache_loaded = False

    with mock.patch("httpx.post", side_effect=_fake_post()) as post:
        sm._get_skill_embeddings(skills, "OTHER/MODEL", URL, "key")
        assert post.call_count == 1, "模型变更，缓存应失效并重新编码"


def test_url_change_invalidates_disk_cache():
    """换服务地址同样意味着向量空间可能不同，缓存须失效。"""
    skills = [_skill("/a/SKILL.md", "alpha", "desc-a")]
    with mock.patch("httpx.post", side_effect=_fake_post()):
        sm._get_skill_embeddings(skills, MODEL, URL, "key")

    sm._embedding_cache.clear()
    sm._embedding_text_hash.clear()
    sm._embedding_disk_cache_loaded = False

    with mock.patch("httpx.post", side_effect=_fake_post()) as post:
        sm._get_skill_embeddings(skills, MODEL, "http://127.0.0.1:9999/v1", "key")
        assert post.call_count == 1, "地址变更，缓存应失效"


def test_saved_file_has_fingerprint_and_is_loadable():
    """落盘文件应带正确指纹，且能被重新载入。"""
    skills = [
        _skill("/a/SKILL.md", "alpha", "desc-a"),
        _skill("/b/SKILL.md", "beta", "desc-b"),
    ]
    with mock.patch("httpx.post", side_effect=_fake_post()):
        sm._get_skill_embeddings(skills, MODEL, URL, "key")

    path = sm._EMBEDDING_DISK_CACHE_PATH
    assert path.exists()
    with np.load(path, allow_pickle=True) as data:
        assert str(data["fingerprint"]) == sm._embedding_disk_fingerprint(MODEL, URL)
        assert len(data["paths"]) == 2
        assert len(data["hashes"]) == 2
        assert data["embs"].shape == (2, DIM)


def test_corrupt_cache_file_degrades_silently():
    """缓存文件损坏时不得抛异常，应静默降级为空缓存。"""
    path = sm._EMBEDDING_DISK_CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is definitely not a valid npz file")

    skills = [_skill("/a/SKILL.md", "alpha", "desc-a")]
    with mock.patch("httpx.post", side_effect=_fake_post()) as post:
        result = sm._get_skill_embeddings(skills, MODEL, URL, "key")
        assert post.call_count == 1, "损坏时应回退到正常编码"
    assert "/a/SKILL.md" in result


def test_no_write_when_fully_cached():
    """稳态全量命中时不应产生磁盘写入。"""
    skills = [_skill("/a/SKILL.md", "alpha", "desc-a")]
    with mock.patch("httpx.post", side_effect=_fake_post()):
        sm._get_skill_embeddings(skills, MODEL, URL, "key")
    mtime = sm._EMBEDDING_DISK_CACHE_PATH.stat().st_mtime_ns

    with mock.patch("httpx.post", side_effect=_fake_post()) as post:
        sm._get_skill_embeddings(skills, MODEL, URL, "key")
        assert post.call_count == 0
    assert sm._EMBEDDING_DISK_CACHE_PATH.stat().st_mtime_ns == mtime, "全量命中不应重写文件"
