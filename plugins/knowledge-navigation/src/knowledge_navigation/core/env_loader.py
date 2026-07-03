"""环境变量兜底加载器。

cron 环境没有 shell profile，os.environ.get 拿不到 ~/.hermes/.env 里的变量。
本模块提供 get_env() 作为 os.environ.get 的替代，优先读环境变量，兜底读 .env 文件。
"""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=1)
def _read_env_file() -> dict[str, str]:
    """从 ~/.hermes/.env 读取 KEY=VALUE，跳过注释和空行。"""
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return {}
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
    return result


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
