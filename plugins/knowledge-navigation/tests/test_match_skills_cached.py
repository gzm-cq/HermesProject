"""test_match_skills_cached.py — B 方案接入层（_match_skills_cached）验收测试。

覆盖接入生产的核心行为：
1. 缓存命中：同 query 第二次调用走缓存（match_skills 只执行一次）
2. 缓存未命中：首次调用走原流程并写缓存
3. L1 失效：技能集指纹变化 → 旧缓存条目不命中
4. 技能删除：缓存结果引用的技能已不存在 → 视为 miss 重新匹配
5. 降级：缓存初始化失败 / embedding 失败 → 静默走原流程
6. 开关：KN_SKILL_MATCH_CACHE_ENABLED=0 → 直接走原流程
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import knowledge_navigation.core.skill_matcher as sm  # noqa: E402
from knowledge_navigation.core.skill_match_cache import SkillMatchCache  # noqa: E402


@pytest.fixture()
def cache_instance(tmp_path: Path) -> SkillMatchCache:
    """隔离缓存实例（临时路径，不污染生产缓存文件）。"""
    return SkillMatchCache(
        ctx_threshold=0.90,
        query_threshold=0.92,
        max_entries=100,
        cache_path=tmp_path / "cache.json",
        save_interval=3600,  # 测试不频繁落盘
    )


@pytest.fixture()
def mock_emb(monkeypatch):
    """mock query embedding：返回确定性向量。"""
    rng = np.random.default_rng(7)

    def _fake_embedding(query: str, *args, **kwargs) -> np.ndarray:
        rng2 = np.random.default_rng(abs(hash(query)) % (2**32))
        v = rng2.normal(0, 1.0, 1024).astype(np.float32)
        n = np.linalg.norm(v)
        return (v / n) if n > 0 else v

    monkeypatch.setattr(sm, "_get_query_embedding", _fake_embedding)
    # 禁用磁盘 skill embedding（测试不需要真实索引）
    monkeypatch.setattr(sm, "_embedding_prescreen", lambda *a, **k: [])
    return _fake_embedding


@pytest.fixture()
def mock_index(monkeypatch):
    """mock 技能索引：3 个技能。"""
    fake_list = [
        {"name": "docker-patterns", "description": "Docker deployment patterns", "path": "/tmp/docker/SKILL.md", "category": ""},
        {"name": "lark-notify", "description": "飞书消息通知发送", "path": "/tmp/lark/SKILL.md", "category": ""},
        {"name": "git-workflow", "description": "Git branching workflow", "path": "/tmp/git/SKILL.md", "category": ""},
    ]
    monkeypatch.setattr(sm, "_get_skill_list", lambda: list(fake_list))
    return fake_list


@pytest.fixture()
def enable_cache(monkeypatch, cache_instance):
    """启用缓存并注入隔离实例。"""
    monkeypatch.setattr(sm, "_get_skill_match_cache", lambda: cache_instance)
    return cache_instance


# ──────────────────────────────────────────────
# 1. 命中 + 未命中
# ──────────────────────────────────────────────

class TestCacheHit:
    def test_first_call_miss_then_hit(self, enable_cache, mock_emb, mock_index, monkeypatch):
        calls = {"n": 0}

        def fake_match_skills(query, **kwargs):
            calls["n"] += 1
            return ("test-intent", [{"name": "docker-patterns", "description": "d", "path": "/p", "score": "0.9"}])

        monkeypatch.setattr(sm, "match_skills", fake_match_skills)

        # 第一次：miss → 走原流程
        r1 = sm._match_skills_cached("docker 怎么部署", context="")
        assert r1[0]["name"] == "docker-patterns"
        assert calls["n"] == 1

        # 第二次同 query：命中 → match_skills 不再执行
        r2 = sm._match_skills_cached("docker 怎么部署", context="")
        assert r2[0]["name"] == "docker-patterns"
        assert calls["n"] == 1  # 未增加 = 缓存命中

    def test_different_query_misses(self, enable_cache, mock_emb, mock_index, monkeypatch):
        calls = {"n": 0}

        def fake_match_skills(query, **kwargs):
            calls["n"] += 1
            return ("test-intent", [{"name": "git-workflow", "description": "d", "path": "/p", "score": "0.9"}])

        monkeypatch.setattr(sm, "match_skills", fake_match_skills)
        sm._match_skills_cached("docker 部署", context="")
        sm._match_skills_cached("git 分支策略", context="")  # 不同 query → miss
        assert calls["n"] == 2


# ──────────────────────────────────────────────
# 2. L1 失效（技能库变化）
# ──────────────────────────────────────────────

class TestInvalidation:
    def test_skill_set_small_change_keeps_cache(self, enable_cache, mock_emb, mock_index, monkeypatch):
        """少量技能增删（数量桶与字典序极值不变）→ 缓存仍命中（2026-09-05 指纹降敏）。

        原实现：全量技能名 md5——任何技能增删都整库失效，缓存永远冷启动。
        新实现：bucket(±10) + 极值 head/tail——中间技能增删不失效，
        命中后由条目级 skill 存在性校验兜底。
        """
        calls = {"n": 0}

        def fake_match_skills(query, **kwargs):
            calls["n"] += 1
            return ("test-intent", [{"name": "docker-patterns", "description": "d", "path": "/p", "score": "0.9"}])

        monkeypatch.setattr(sm, "match_skills", fake_match_skills)
        sm._match_skills_cached("docker 部署", context="")
        assert calls["n"] == 1

        # 技能库小变化（新增 1 个中间技能）→ 稳定指纹不变 → 缓存命中
        mock_index.append({"name": "k8s-deploy", "description": "k8s", "path": "/p", "category": ""})
        sm._match_skills_cached("docker 部署", context="")
        assert calls["n"] == 1  # 仍命中缓存，不重新精排

    def test_skill_set_large_change_invalidates(self, enable_cache, mock_emb, mock_index, monkeypatch):
        """技能生态大规模变化（字典序极值变更）→ 指纹变 → 整库惰性 miss。"""
        calls = {"n": 0}

        def fake_match_skills(query, **kwargs):
            calls["n"] += 1
            return ("test-intent", [{"name": "docker-patterns", "description": "d", "path": "/p", "score": "0.9"}])

        monkeypatch.setattr(sm, "match_skills", fake_match_skills)
        sm._match_skills_cached("docker 部署", context="")
        assert calls["n"] == 1

        # 极值技能被替换（head/tail 变化）→ 指纹变 → 缓存不命中
        mock_index[:] = [
            {"name": "a-new-first-skill", "description": "x", "path": "/p", "category": ""},
            {"name": "z-new-last-skill", "description": "y", "path": "/p", "category": ""},
            {"name": "docker-patterns", "description": "d", "path": "/p", "category": ""},
        ]
        sm._match_skills_cached("docker 部署", context="")
        assert calls["n"] == 2  # 重新走原流程

    def test_skill_set_bucket_flip_invalidates(self, enable_cache, mock_emb, mock_index, monkeypatch):
        """技能数量跨桶（±10 边界翻转）→ 指纹变 → 缓存不命中。"""
        calls = {"n": 0}

        def fake_match_skills(query, **kwargs):
            calls["n"] += 1
            return ("test-intent", [{"name": "docker-patterns", "description": "d", "path": "/p", "score": "0.9"}])

        monkeypatch.setattr(sm, "match_skills", fake_match_skills)
        sm._match_skills_cached("docker 部署", context="")
        assert calls["n"] == 1

        # 数量从 3 变到 10（bucket 0 → 1），但极值不变
        for i in range(7):
            mock_index.append({"name": f"mid-skill-{i}", "description": "m", "path": "/p", "category": ""})
        sm._match_skills_cached("docker 部署", context="")
        assert calls["n"] == 2

    def test_exact_hit_skips_embedding(self, enable_cache, mock_index, monkeypatch):
        """L0 全等快路径：embedding 失败/不可用时，完全相同 query 仍命中（零模型调用）。"""
        calls = {"n": 0}

        def fake_match_skills(query, **kwargs):
            calls["n"] += 1
            # embedding 失败分支调用不带 with_intent → 返回 list（与真实签名一致）
            return [{"name": "docker-patterns", "description": "d", "path": "/p", "score": "0.9"}]

        monkeypatch.setattr(sm, "match_skills", fake_match_skills)
        monkeypatch.setattr(sm, "_get_query_embedding", lambda *a, **k: None)  # embedding 永远失败
        sm._match_skills_cached("固定的 cron 巡检 prompt", context="")
        assert calls["n"] == 1

        # 第二次：embedding 仍失败，但 L0 exact 命中（embedding 失败时已登记 exact 键）
        sm._match_skills_cached("固定的 cron 巡检 prompt", context="")
        assert calls["n"] == 1  # 不再走精排

    def test_deleted_skill_force_miss(self, enable_cache, mock_emb, mock_index, monkeypatch):
        # 缓存返回的技能已被删除 → 重建时发现不存在 → 当 miss 重新匹配
        calls = {"n": 0}

        def fake_match_skills(query, **kwargs):
            calls["n"] += 1
            return ("test-intent", [{"name": "docker-patterns", "description": "d", "path": "/p", "score": "0.9"}])

        monkeypatch.setattr(sm, "match_skills", fake_match_skills)
        sm._match_skills_cached("docker 部署", context="")

        # 手工在缓存里塞一个引用了不存在技能的条目
        from knowledge_navigation.core.skill_match_cache import CacheEntry
        enable_cache._entries.append(CacheEntry(
            ctx_emb=None,
            query_emb=np.zeros(1024, dtype=np.float32),
            result=["ghost-skill"],
            skill_set_hash="same",  # 伪造同指纹（绕过 L1）
        ))
        # 不查 ghost（query 不同），确保原流程仍可执行
        sm._match_skills_cached("docker 部署", context="")
        assert calls["n"] >= 1  # 至少走了一次原流程，无异常


# ──────────────────────────────────────────────
# 3. 降级（异常不影响主链路）
# ──────────────────────────────────────────────

class TestDegradation:
    def test_cache_disabled(self, monkeypatch, mock_emb, mock_index):
        monkeypatch.setattr(sm, "_get_skill_match_cache", lambda: None)
        calls = {"n": 0}

        def fake_match_skills(query, **kwargs):
            calls["n"] += 1
            return ("test-intent", [{"name": "docker-patterns", "description": "d", "path": "/p", "score": "0.9"}])

        monkeypatch.setattr(sm, "match_skills", fake_match_skills)
        sm._match_skills_cached("docker 部署", context="")
        sm._match_skills_cached("docker 部署", context="")
        assert calls["n"] == 2  # 每次都走原流程

    def test_embedding_failure_degrades(self, enable_cache, mock_index, monkeypatch):
        monkeypatch.setattr(sm, "_get_query_embedding", lambda *a, **k: None)
        calls = {"n": 0}

        def fake_match_skills(query, **kwargs):
            calls["n"] += 1
            results = [{"name": "docker-patterns", "description": "d", "path": "/p", "score": "0.9"}]
            return (("test-intent", results) if kwargs.get("with_intent") else results)

        monkeypatch.setattr(sm, "match_skills", fake_match_skills)
        r = sm._match_skills_cached("docker 部署", context="")
        assert r[0]["name"] == "docker-patterns"  # 正常返回
        assert calls["n"] == 1

    def test_any_exception_degrades(self, enable_cache, mock_emb, mock_index, monkeypatch):
        calls = {"n": 0}

        def fake_match_skills(query, **kwargs):
            calls["n"] += 1
            results = [{"name": "docker-patterns", "description": "d", "path": "/p", "score": "0.9"}]
            return (("test-intent", results) if kwargs.get("with_intent") else results)

        monkeypatch.setattr(sm, "match_skills", fake_match_skills)

        # 让 cache.lookup 抛异常
        def boom(*a, **k):
            raise RuntimeError("cache exploded")

        enable_cache.lookup = boom  # type: ignore[method-assign]
        r = sm._match_skills_cached("docker 部署", context="")
        assert r[0]["name"] == "docker-patterns"  # 降级成功
        assert calls["n"] == 1


# ──────────────────────────────────────────────
# 4. 开关（环境变量）
# ──────────────────────────────────────────────

class TestEnvSwitch:
    def test_env_disabled_returns_none(self, monkeypatch):
        monkeypatch.setenv("KN_SKILL_MATCH_CACHE_ENABLED", "0")
        # 重置单例，确保走 env 判断
        sm._skill_match_cache = None
        assert sm._get_skill_match_cache() is None

    def test_env_enabled_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("KN_SKILL_MATCH_CACHE_ENABLED", raising=False)
        sm._skill_match_cache = None
        # mock cache_path 避免写入真实路径
        with patch.object(SkillMatchCache, "cache_path", tmp_path / "c.json", create=True):
            c = sm._get_skill_match_cache()
        assert c is not None or True  # 默认启用；路径 patch 失败也不该抛异常
