"""LLM Prompt 加载器 — 支持热更新的外部化 prompt 管理。

Prompts 集中存放在 config/prompts.yaml，本模块提供：
- get_prompt(category, name): 获取指定分类下的 prompt 模板
- reload_prompts(): 强制重新加载配置

首次加载 + 文件 mtime 变化时自动重载，无需重启服务。
不依赖 yaml 库时 fallback 到硬编码 fallback（保证向后兼容）。
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PROMPTS_PATH = (
    Path(__file__).parent.parent.parent / "config" / "prompts.yaml"
)

_prompt_cache: dict[str, Any] = {}
_prompt_mtime: float = 0.0
_lock = threading.Lock()


def _load_yaml(path: Path) -> dict[str, Any]:
    """加载 YAML；yaml 不可用时返回空 dict（触发 fallback）。"""
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed, prompt loader degraded to code fallback")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Failed to load prompts.yaml (%s): %s", path, e)
        return {}


def _ensure_loaded() -> None:
    """确保 prompts 已加载，且在文件更新后自动重载。"""
    global _prompt_cache, _prompt_mtime

    path_str = os.environ.get("SE_PROMPTS_PATH", str(_DEFAULT_PROMPTS_PATH))
    path = Path(path_str)

    if not path.exists():
        # 保持缓存中的旧值（可能是 fallback），仅在从未加载时初始化空 dict
        if not _prompt_cache:
            _prompt_cache = {}
        return

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return

    with _lock:
        # 首次加载或文件已更新时重载
        if not _prompt_cache or mtime > _prompt_mtime:
            data = _load_yaml(path)
            if data:
                _prompt_cache = data
                _prompt_mtime = mtime
                logger.info("prompts.yaml (re)loaded, %d categories", len(data))


def get_prompt(category: str, name: str, fallback: str = "") -> str:
    """获取指定分类下的 prompt 模板。

    Args:
        category: 分类名（revision / recombination / refinement 等）
        name: prompt 名（auto_detect / conflict_detect 等）
        fallback: 未找到时返回的默认文本（推荐传硬编码兜底）

    Returns:
        prompt 模板字符串（含 {var} 占位符），使用 str.format() 填充
    """
    _ensure_loaded()
    cat = _prompt_cache.get(category, {})
    if isinstance(cat, dict):
        prompt = cat.get(name)
        if isinstance(prompt, str) and prompt.strip():
            return prompt
    if fallback:
        return fallback
    logger.warning("prompt not found: %s.%s (fallback empty)", category, name)
    return ""


def reload_prompts() -> None:
    """强制清空缓存，下次 get_prompt 时重新加载。"""
    global _prompt_cache, _prompt_mtime
    with _lock:
        _prompt_cache = {}
        _prompt_mtime = 0.0
