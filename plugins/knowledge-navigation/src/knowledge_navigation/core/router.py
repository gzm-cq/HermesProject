"""LLM-driven Router for 3-way injection mask."""

import json
import logging
import re

import httpx

from knowledge_navigation.core.source_defs import build_router_prompt

logger = logging.getLogger(__name__)

_router_cache: dict[tuple[str, str], dict[str, bool]] = {}
_ROUTER_CACHE_MAX = 64

_ROUTER_SYSTEM_PROMPT = build_router_prompt()


def _parse_mask(text: str) -> dict[str, bool] | None:
    """从 LLM 响应解析 mask JSON，含 JSON 块提取和字段缺失兜底。"""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
        else:
            return None
    if not isinstance(data, dict):
        return None
    return {
        "h": bool(data.get("h", False)),
        "kt": bool(data.get("kt", False)),
        "s": bool(data.get("s", False)),
    }


def route(
    session_id: str,
    message: str,
    model: str,
    api_url: str,
    api_key: str,
    timeout: int,
) -> dict[str, bool]:
    """LLM Router 决策三路 mask。

    缓存 key=(session_id, message) 精确匹配，同轮 tool call 复用，新 message 重走。
    """
    cache_key = (session_id, message)
    cached = _router_cache.get(cache_key)
    if cached is not None:
        return cached

    safe_msg = message[:300] + message[-200:] if len(message) > 500 else message
    safe_msg = safe_msg.replace("\n", " ").replace("\r", " ")

    try:
        resp = httpx.post(
            f"{api_url.rstrip('/')}/chat/completions",
            json={
                "model": model,
                "temperature": 0.1,
                "max_tokens": 64,
                "messages": [
                    {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"消息：{safe_msg}\n\nJSON 输出："},
                ],
            },
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("Router 调用失败 (%s)，fallback 全开", e)
        _router_cache[cache_key] = {"h": True, "kt": True, "s": True}
        return _router_cache[cache_key]

    mask = _parse_mask(raw)
    if mask is None:
        logger.warning("Router JSON 解析失败, fallback 全开")
        mask = {"h": True, "kt": True, "s": True}

    _router_cache[cache_key] = mask
    if len(_router_cache) > _ROUTER_CACHE_MAX:
        _evict = _ROUTER_CACHE_MAX // 2
        for _k in list(_router_cache.keys())[:_evict]:
            del _router_cache[_k]
    return mask