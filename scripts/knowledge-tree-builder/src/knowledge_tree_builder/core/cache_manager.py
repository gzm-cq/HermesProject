"""统一缓存管理 — P3-10

所有缓存文件统一管理到 .kb_cache/ 目录下，方便清理和迁移。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


# 缓存名称常量
DOMAIN_CACHE_NAME = "domain_cache.json"
EMBEDDING_CACHE_NAME = "embedding_cache.json"
METADATA_CACHE_NAME = "metadata_cache.json"


@dataclass
class CacheInfo:
    """缓存文件信息"""
    name: str
    path: str
    size: int
    mtime: float


class CacheManager:
    """统一缓存管理器"""

    def __init__(
        self,
        cache_dir: str = ".kb_cache/",
        enable_unified_cache: bool = True,
    ):
        """初始化缓存管理器。

        Args:
            cache_dir: 缓存目录路径（相对或绝对）
            enable_unified_cache: 是否启用统一缓存（False 时回退到旧路径）
        """
        self._cache_dir = cache_dir
        self._enable_unified_cache = enable_unified_cache

    @property
    def cache_dir(self) -> str:
        return self._cache_dir

    @property
    def enable_unified_cache(self) -> bool:
        return self._enable_unified_cache

    def get_cache_path(self, name: str) -> Path:
        """获取指定缓存的路径。

        Args:
            name: 缓存文件名（如 domain_cache.json）

        Returns:
            缓存文件的绝对路径
        """
        if not self._enable_unified_cache:
            return Path(name)
        return Path(self._cache_dir).resolve() / name

    def ensure_cache_dir(self) -> Path:
        """确保缓存目录存在。

        Returns:
            缓存目录的绝对路径
        """
        cache_path = Path(self._cache_dir).resolve()
        cache_path.mkdir(parents=True, exist_ok=True)
        return cache_path

    def list_caches(self) -> list[CacheInfo]:
        """列出所有缓存文件。

        Returns:
            缓存信息列表，每个元素包含 name, path, size, mtime
        """
        if not self._enable_unified_cache:
            return []
        cache_path = Path(self._cache_dir).resolve()
        if not cache_path.is_dir():
            return []
        caches: list[CacheInfo] = []
        for f in cache_path.iterdir():
            if f.is_file() and not f.name.endswith(".tmp"):
                stat = f.stat()
                caches.append(CacheInfo(
                    name=f.name,
                    path=str(f.resolve()),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                ))
        return sorted(caches, key=lambda x: x.name)

    def clear_cache(self, name: str | None = None) -> int:
        """清除缓存。

        Args:
            name: 缓存文件名（如 domain_cache.json），None 表示清除全部

        Returns:
            删除的文件数量
        """
        if not self._enable_unified_cache:
            return 0
        cache_path = Path(self._cache_dir).resolve()
        if not cache_path.is_dir():
            return 0
        deleted = 0
        if name is None:
            for f in cache_path.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                        deleted += 1
                    except Exception:
                        pass
        else:
            target = cache_path / name
            if target.is_file():
                try:
                    target.unlink()
                    deleted = 1
                except Exception:
                    pass
        return deleted

    def get_cache_size(self) -> int:
        """获取缓存总大小。

        Returns:
            缓存总大小（bytes）
        """
        if not self._enable_unified_cache:
            return 0
        cache_path = Path(self._cache_dir).resolve()
        if not cache_path.is_dir():
            return 0
        total = 0
        for f in cache_path.iterdir():
            if f.is_file():
                total += f.stat().st_size
        return total

    def load_cache(self, name: str) -> dict[str, Any] | None:
        """加载缓存文件。

        Args:
            name: 缓存文件名

        Returns:
            缓存内容（字典），不存在时返回 None
        """
        path = self.get_cache_path(name)
        if not path.is_file():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def save_cache(self, name: str, data: dict[str, Any]) -> bool:
        """保存缓存文件（原子写入，写入过程中断不会留下半写文件）。

        Args:
            name: 缓存文件名
            data: 要保存的数据

        Returns:
            是否保存成功
        """
        path = self.get_cache_path(name)
        if self._enable_unified_cache:
            self.ensure_cache_dir()
        try:
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            return True
        except Exception:
            try:
                if 'tmp_path' in locals() and tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            return False


def get_migration_candidates(
    input_dir: str,
    base_dir: Path | None = None,
) -> list[tuple[Path, Path]]:
    """检测需要迁移的旧缓存文件。

    Args:
        input_dir: 输入目录路径
        base_dir: 基础目录（默认为当前目录）

    Returns:
        需要迁移的文件列表，每个元素为 (旧路径, 新路径)
    """
    input_name = Path(input_dir).name if input_dir else ""
    candidates = []

    old_caches = [
        (f".kb_phase4_{input_name}.json", DOMAIN_CACHE_NAME),
        (".kb_embed_cache.json", EMBEDDING_CACHE_NAME),
    ]

    if base_dir is None:
        base_dir = Path(".").resolve()
    unified_dir = base_dir / ".kb_cache"
    for old_name, new_name in old_caches:
        old_path = base_dir / old_name
        if old_path.exists() and old_path.is_file():
            new_path = unified_dir / new_name
            if not new_path.exists():
                candidates.append((old_path, new_path))

    return candidates


def migrate_old_caches(
    input_dir: str,
    enable_unified_cache: bool = True,
    base_dir: Path | None = None,
) -> int:
    """迁移旧缓存到统一目录。

    Args:
        input_dir: 输入目录路径
        enable_unified_cache: 是否启用统一缓存
        base_dir: 基础目录（默认为当前目录）

    Returns:
        迁移的文件数量
    """
    if not enable_unified_cache:
        return 0
    if base_dir is None:
        base_dir = Path(".").resolve()
    candidates = get_migration_candidates(input_dir, base_dir)
    if not candidates:
        return 0
    unified_dir = base_dir / ".kb_cache"
    unified_dir.mkdir(parents=True, exist_ok=True)
    migrated = 0
    for old_path, new_path in candidates:
        try:
            data = old_path.read_bytes()
            new_path.write_bytes(data)
            old_path.unlink()
            migrated += 1
        except Exception:
            pass
    return migrated
