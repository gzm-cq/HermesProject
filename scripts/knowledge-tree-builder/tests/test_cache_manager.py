"""P3-10: 统一缓存管理测试"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from knowledge_tree_builder.core.cache_manager import (
    CacheInfo,
    CacheManager,
    DOMAIN_CACHE_NAME,
    EMBEDDING_CACHE_NAME,
    METADATA_CACHE_NAME,
    get_migration_candidates,
    migrate_old_caches,
)


class TestCacheManager:
    """CacheManager 测试"""

    def test_ensure_cache_dir_creates_directory(self, tmp_path: Path) -> None:
        """测试缓存目录创建"""
        cache_dir = tmp_path / ".kb_cache"
        manager = CacheManager(cache_dir=str(cache_dir), enable_unified_cache=True)
        result = manager.ensure_cache_dir()
        assert cache_dir.is_dir()
        assert result == cache_dir.resolve()

    def test_get_cache_path_returns_correct_path(self, tmp_path: Path) -> None:
        """测试获取缓存路径"""
        cache_dir = tmp_path / ".kb_cache"
        manager = CacheManager(cache_dir=str(cache_dir), enable_unified_cache=True)
        path = manager.get_cache_path("test.json")
        assert path == cache_dir.resolve() / "test.json"

    def test_get_cache_path_disabled_returns_plain_name(self, tmp_path: Path) -> None:
        """测试 Feature Flag 关闭时回退到旧路径"""
        manager = CacheManager(cache_dir=".kb_cache/", enable_unified_cache=False)
        path = manager.get_cache_path("test.json")
        assert path == Path("test.json")

    def test_save_and_load_cache(self, tmp_path: Path) -> None:
        """测试缓存读写"""
        cache_dir = tmp_path / ".kb_cache"
        manager = CacheManager(cache_dir=str(cache_dir), enable_unified_cache=True)

        data = {"key": "value", "list": [1, 2, 3]}
        success = manager.save_cache("test.json", data)
        assert success

        loaded = manager.load_cache("test.json")
        assert loaded == data

    def test_load_cache_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """测试加载不存在的缓存返回 None"""
        cache_dir = tmp_path / ".kb_cache"
        manager = CacheManager(cache_dir=str(cache_dir), enable_unified_cache=True)
        result = manager.load_cache("nonexistent.json")
        assert result is None

    def test_list_caches_empty_when_no_files(self, tmp_path: Path) -> None:
        """测试空缓存目录"""
        cache_dir = tmp_path / ".kb_cache"
        cache_dir.mkdir(parents=True)
        manager = CacheManager(cache_dir=str(cache_dir), enable_unified_cache=True)
        caches = manager.list_caches()
        assert caches == []

    def test_list_caches_returns_all_files(self, tmp_path: Path) -> None:
        """测试列出所有缓存文件"""
        cache_dir = tmp_path / ".kb_cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "file1.json").write_text("{}")
        (cache_dir / "file2.json").write_text("{}")
        manager = CacheManager(cache_dir=str(cache_dir), enable_unified_cache=True)
        caches = manager.list_caches()
        assert len(caches) == 2
        assert all(isinstance(c, CacheInfo) for c in caches)
        names = {c.name for c in caches}
        assert names == {"file1.json", "file2.json"}

    def test_list_caches_returns_empty_when_disabled(self, tmp_path: Path) -> None:
        """测试 Feature Flag 关闭时 list_caches 返回空"""
        manager = CacheManager(cache_dir=str(tmp_path / ".kb_cache"), enable_unified_cache=False)
        caches = manager.list_caches()
        assert caches == []

    def test_clear_cache_removes_single_file(self, tmp_path: Path) -> None:
        """测试清除单个缓存"""
        cache_dir = tmp_path / ".kb_cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "test.json").write_text("{}")
        manager = CacheManager(cache_dir=str(cache_dir), enable_unified_cache=True)
        deleted = manager.clear_cache("test.json")
        assert deleted == 1
        assert not (cache_dir / "test.json").exists()

    def test_clear_cache_removes_all_files(self, tmp_path: Path) -> None:
        """测试清除全部缓存"""
        cache_dir = tmp_path / ".kb_cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "file1.json").write_text("{}")
        (cache_dir / "file2.json").write_text("{}")
        manager = CacheManager(cache_dir=str(cache_dir), enable_unified_cache=True)
        deleted = manager.clear_cache()
        assert deleted == 2
        assert list(cache_dir.iterdir()) == []

    def test_clear_cache_returns_zero_when_disabled(self, tmp_path: Path) -> None:
        """测试 Feature Flag 关闭时 clear_cache 返回 0"""
        manager = CacheManager(cache_dir=str(tmp_path / ".kb_cache"), enable_unified_cache=False)
        deleted = manager.clear_cache()
        assert deleted == 0

    def test_get_cache_size_empty_dir(self, tmp_path: Path) -> None:
        """测试空缓存目录大小"""
        cache_dir = tmp_path / ".kb_cache"
        cache_dir.mkdir(parents=True)
        manager = CacheManager(cache_dir=str(cache_dir), enable_unified_cache=True)
        size = manager.get_cache_size()
        assert size == 0

    def test_get_cache_size_calculates_total(self, tmp_path: Path) -> None:
        """测试缓存大小计算"""
        cache_dir = tmp_path / ".kb_cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "file1.json").write_text("x" * 100)
        (cache_dir / "file2.json").write_text("y" * 200)
        manager = CacheManager(cache_dir=str(cache_dir), enable_unified_cache=True)
        size = manager.get_cache_size()
        assert size == 300

    def test_get_cache_size_returns_zero_when_disabled(self, tmp_path: Path) -> None:
        """测试 Feature Flag 关闭时 get_cache_size 返回 0"""
        manager = CacheManager(cache_dir=str(tmp_path / ".kb_cache"), enable_unified_cache=False)
        size = manager.get_cache_size()
        assert size == 0


class TestMigration:
    """缓存迁移测试"""

    def test_get_migration_candidates_finds_old_caches(self, tmp_path: Path) -> None:
        """测试检测需要迁移的旧缓存"""
        cache_dir = tmp_path / ".kb_cache"
        cache_dir.mkdir(parents=True)
        old_cache = tmp_path / ".kb_phase4_test.json"
        old_cache.write_text("{}")
        candidates = get_migration_candidates("test", base_dir=tmp_path)
        assert len(candidates) == 1
        old_path, new_path = candidates[0]
        assert old_path == old_cache
        assert new_path == cache_dir / DOMAIN_CACHE_NAME

    def test_get_migration_candidates_skips_existing_new(self, tmp_path: Path) -> None:
        """测试迁移目标已存在时跳过"""
        cache_dir = tmp_path / ".kb_cache"
        cache_dir.mkdir(parents=True)
        old_cache = tmp_path / ".kb_phase4_test.json"
        old_cache.write_text("{}")
        (cache_dir / DOMAIN_CACHE_NAME).write_text("{}")
        candidates = get_migration_candidates("test", base_dir=tmp_path)
        assert len(candidates) == 0

    def test_migrate_old_caches_moves_file(self, tmp_path: Path) -> None:
        """测试迁移旧缓存文件"""
        cache_dir = tmp_path / ".kb_cache"
        cache_dir.mkdir(parents=True)
        old_cache = tmp_path / ".kb_phase4_test.json"
        old_cache.write_text('{"data": "test"}')
        migrated = migrate_old_caches("test", enable_unified_cache=True, base_dir=tmp_path)
        assert migrated == 1
        assert not old_cache.exists()
        assert (cache_dir / DOMAIN_CACHE_NAME).exists()

    def test_migrate_old_caches_returns_zero_when_disabled(self, tmp_path: Path) -> None:
        """测试 Feature Flag 关闭时不迁移"""
        old_cache = tmp_path / ".kb_phase4_test.json"
        old_cache.write_text("{}")
        migrated = migrate_old_caches("test", enable_unified_cache=False, base_dir=tmp_path)
        assert migrated == 0
        assert old_cache.exists()

    def test_migrate_old_caches_idempotent(self, tmp_path: Path) -> None:
        """测试迁移是幂等的"""
        cache_dir = tmp_path / ".kb_cache"
        cache_dir.mkdir(parents=True)
        old_cache = tmp_path / ".kb_phase4_test.json"
        old_cache.write_text("{}")
        migrate_old_caches("test", enable_unified_cache=True, base_dir=tmp_path)
        migrated = migrate_old_caches("test", enable_unified_cache=True, base_dir=tmp_path)
        assert migrated == 0


class TestCacheConstants:
    """缓存名称常量测试"""

    def test_domain_cache_name(self) -> None:
        assert DOMAIN_CACHE_NAME == "domain_cache.json"

    def test_embedding_cache_name(self) -> None:
        assert EMBEDDING_CACHE_NAME == "embedding_cache.json"

    def test_metadata_cache_name(self) -> None:
        assert METADATA_CACHE_NAME == "metadata_cache.json"
