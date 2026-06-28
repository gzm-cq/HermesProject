"""测试 Phase 4 领域缓存路径 hash 功能"""

from __future__ import annotations

from hashlib import md5
from pathlib import Path

import pytest

from knowledge_tree_builder.commands.run import _domain_cache_key
from knowledge_tree_builder.config import AppConfig, load_config


class TestDomainCacheKey:
    def test_path_hash_enabled(self) -> None:
        """启用 path hash 时，key 包含 MD5 前缀和标题"""
        input_dir = "/data/articles"
        article_path = "/data/articles/ai/ml-intro.md"
        title = "机器学习入门"
        key = _domain_cache_key(article_path, title, input_dir, True)
        assert key.endswith(f"_{title}")
        prefix = key.split("_", 1)[0]
        assert len(prefix) == 12
        assert all(c in "0123456789abcdef" for c in prefix)

    def test_path_hash_disabled(self) -> None:
        """关闭 path hash 时，key 就是标题本身（旧行为）"""
        input_dir = "/data/articles"
        article_path = "/data/articles/ai/ml-intro.md"
        title = "机器学习入门"
        key = _domain_cache_key(article_path, title, input_dir, False)
        assert key == title

    def test_same_title_different_path_different_key(self) -> None:
        """同标题不同路径，生成不同的 key"""
        input_dir = "/data/articles"
        title = "README"
        path1 = "/data/articles/dir1/README.md"
        path2 = "/data/articles/dir2/README.md"
        key1 = _domain_cache_key(path1, title, input_dir, True)
        key2 = _domain_cache_key(path2, title, input_dir, True)
        assert key1 != key2
        assert key1.endswith(f"_{title}")
        assert key2.endswith(f"_{title}")

    def test_same_path_same_title_same_key(self) -> None:
        """同路径同标题，生成相同的 key（确定性）"""
        input_dir = "/data/articles"
        article_path = "/data/articles/ai/ml-intro.md"
        title = "机器学习入门"
        key1 = _domain_cache_key(article_path, title, input_dir, True)
        key2 = _domain_cache_key(article_path, title, input_dir, True)
        assert key1 == key2

    def test_relative_path_vs_absolute_path_same_key(self, tmp_path: Path) -> None:
        """相对路径和绝对路径，只要相对 input_dir 相同则 key 相同"""
        input_dir = str(tmp_path)
        rel_path = "ai/ml-intro.md"
        abs_path = str(tmp_path / "ai" / "ml-intro.md")
        title = "机器学习入门"
        key_rel = _domain_cache_key(rel_path, title, input_dir, True)
        key_abs = _domain_cache_key(abs_path, title, input_dir, True)
        assert key_rel == key_abs

    def test_path_outside_input_dir_fallback(self) -> None:
        """路径不在 input_dir 下时，使用完整路径作为 fallback"""
        input_dir = "/data/articles"
        article_path = "/other/path/article.md"
        title = "测试文章"
        key = _domain_cache_key(article_path, title, input_dir, True)
        assert key.endswith(f"_{title}")
        assert len(key.split("_", 1)[0]) == 12

    def test_hash_is_md5_first_12_chars(self, tmp_path: Path) -> None:
        """验证 hash 值确实是相对路径 MD5 的前 12 位"""
        input_dir = str(tmp_path)
        rel_path = str(Path("subdir") / "article.md")
        abs_path = str(tmp_path / "subdir" / "article.md")
        title = "测试文章"
        expected_hash = md5(rel_path.encode("utf-8")).hexdigest()[:12]
        key = _domain_cache_key(abs_path, title, input_dir, True)
        assert key.startswith(expected_hash)
        assert key == f"{expected_hash}_{title}"


class TestDomainCacheConfig:
    def test_default_config_has_domain_cache_use_path_hash(self) -> None:
        """默认配置中 domain_cache_use_path_hash 应为 True"""
        config = AppConfig()
        assert config.domain_cache_use_path_hash is True

    def test_config_from_dict(self) -> None:
        """从字典创建配置时正确读取 domain_cache_use_path_hash"""
        config = AppConfig.from_dict({"domain_cache_use_path_hash": False})
        assert config.domain_cache_use_path_hash is False

    def test_env_var_override_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量 KT_DOMAIN_CACHE_USE_PATH_HASH=true 时覆盖配置"""
        monkeypatch.setenv("KT_DOMAIN_CACHE_USE_PATH_HASH", "true")
        config_dict = load_config("nonexistent.yaml")
        assert config_dict["domain_cache_use_path_hash"] is True

    def test_env_var_override_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量 KT_DOMAIN_CACHE_USE_PATH_HASH=false 时覆盖配置"""
        monkeypatch.setenv("KT_DOMAIN_CACHE_USE_PATH_HASH", "false")
        config_dict = load_config("nonexistent.yaml")
        assert config_dict["domain_cache_use_path_hash"] is False
