"""环境变量兜底加载器。

cron 环境没有 shell profile，os.environ.get 拿不到 ~/.hermes/.env 里的变量。
本模块提供 get_env() 作为 os.environ.get 的替代，优先读环境变量，兜底读 .env 文件。

.env 文件缓存 60 秒，过期后自动刷新，避免修改 .env 后需重启进程。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

_ENV_CACHE_TTL = 60

_env_cache: dict[str, str] = {}
_env_cache_ts: float = 0.0


def _read_env_file() -> dict[str, str]:
    """从 ~/.hermes/.env 读取 KEY=VALUE，跳过注释和空行，60s TTL 缓存。"""
    global _env_cache, _env_cache_ts
    now = time.time()
    if _env_cache and (now - _env_cache_ts) < _ENV_CACHE_TTL:
        return _env_cache
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        _env_cache = {}
        _env_cache_ts = now
        return _env_cache
    result: dict[str, str] = {}
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("\"'")
                if key and key not in result:
                    result[key] = val
    except Exception:
        pass
    _env_cache = result
    _env_cache_ts = now
    return _env_cache


def get_env(key: str, default: str = "") -> str:
    """优先 os.environ，兜底 ~/.hermes/.env。"""
    val = os.environ.get(key)
    if val:
        return val
    return _read_env_file().get(key, default)


def get_env_int(key: str, default: int) -> int:
    """get_env 的 int 版本。"""
    raw = get_env(key, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def get_env_float(key: str, default: float) -> float:
    """get_env 的 float 版本。"""
    raw = get_env(key, "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


_BOOL_TRUE = {"true", "1", "yes"}
_BOOL_FALSE = {"false", "0", "no"}


def get_env_bool(key: str, default: bool) -> bool:
    """get_env 的 bool 版本，支持 true/false/1/0/yes/no，大小写不敏感。"""
    raw = get_env(key, "").strip().lower()
    if raw in _BOOL_TRUE:
        return True
    if raw in _BOOL_FALSE:
        return False
    return default


def get_env_list(key: str, default: list[str], separator: str = ",") -> list[str]:
    """get_env 的 list 版本，按分隔符拆分，strip 空白，过滤空值。"""
    raw = get_env(key, "")
    parts = [p.strip() for p in raw.split(separator)]
    return [p for p in parts if p] if raw else default
