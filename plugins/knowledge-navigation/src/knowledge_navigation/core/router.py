"""LLM-driven Router for 3-way injection mask."""

import json
import logging
import os
import re
import time

import httpx

from knowledge_navigation.core.source_defs import build_router_prompt
from knowledge_navigation.core.env_loader import get_env, get_env_int

logger = logging.getLogger(__name__)

_router_cache: dict[tuple[str, str], dict[str, bool]] = {}
_ROUTER_CACHE_MAX = 64
_ROUTER_CACHE_TTL = 300

_router_cache_timestamps: dict[tuple[str, str], float] = {}

_ROUTER_SYSTEM_PROMPT = build_router_prompt()

_FALLBACK_COUNTER = {"json_parse": 0, "api_error": 0, "api_401": 0, "api_timeout": 0, "api_other": 0}


def _clean_expired_cache() -> None:
    now = time.time()
    to_remove = [k for k, ts in _router_cache_timestamps.items() if now - ts > _ROUTER_CACHE_TTL]
    for k in to_remove:
        _router_cache.pop(k, None)
        _router_cache_timestamps.pop(k, None)


def _parse_mask(text: str) -> dict[str, bool] | None:
    """从 LLM 响应解析 mask JSON，含 JSON 块提取和字段缺失兜底。"""
    data: dict | None = None

    # 1) Try direct JSON parse
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2) Try ```json ... ``` or ``` ... ``` code block
    if data is None:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

    # 3) Try first { ... } object (handles inline JSON, markdown text, etc.)
    if data is None:
        m = re.search(r"\{[^{}]*\}", text)
        if m:
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

    # 4) Salvage truncated JSON: progressively strip trailing incomplete kv pairs
    #    e.g. {"h": false, "kt": false, "s": true, "confidence":  ← max_tokens cutoff
    if data is None:
        m = re.search(r'\{.*', text)  # from first { to end
        if m:
            candidate = m.group(0).rstrip()
            for _ in range(10):  # max attempts
                candidate = candidate.rstrip().rstrip(',').rstrip(':').rstrip()
                try:
                    data = json.loads(candidate + '}')
                    break
                except json.JSONDecodeError:
                    last_comma = candidate.rfind(',')
                    if last_comma > 0:
                        candidate = candidate[:last_comma]
                    else:
                        break

    # 5) Extract JSON from LLM reasoning text: LLM sometimes outputs thinking
    #    before JSON (e.g. "我们分析消息：...\n\n{\"h\": ...}") — pull the last
    #    JSON object from the full text, not just the first one.
    if data is None:
        json_objects = re.findall(r'\{[^{}]*\}', text)
        for obj_text in reversed(json_objects):  # check last one first (usually the answer)
            try:
                candidate = json.loads(obj_text)
                if isinstance(candidate, dict) and all(k in candidate for k in ('h', 'kt', 's', 'sag')):
                    data = candidate
                    break
            except json.JSONDecodeError:
                continue

    # After salvage, require all 4 mask keys present — partial mask is dangerous
    # (missing key → False → route closed when it should be open)
    if isinstance(data, dict) and not all(k in data for k in ('h', 'kt', 's', 'sag')):
        return None

    if not isinstance(data, dict):
        return None

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        logger.debug("Router confidence invalid, applying fallback 四路全开")
        return {"h": True, "kt": True, "s": True, "sag": True}
    if confidence < 0.5:
        logger.debug("Router confidence=%.2f < 0.5, applying fallback 四路全开", confidence)
        return {"h": True, "kt": True, "s": True, "sag": True}

    return {
        "h": bool(data.get("h", False)),
        "kt": bool(data.get("kt", False)),
        "s": bool(data.get("s", False)),
        "sag": bool(data.get("sag", False)),
    }


def _fetch_api_key() -> str:
    """从环境变量获取 API key，支持动态刷新，兜底读 ~/.hermes/.env。"""
    return get_env("KN_ROUTER_API_KEY", "") or get_env("LLM_API_KEY", "") or ""


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

    api_key 为空时从环境变量 KN_ROUTER_API_KEY → LLM_API_KEY 动态加载，
    绕过 CONFIG 模块级单例在 import 时的 env 未就绪问题。

    遇到 401 Unauthorized 会自动重试一次（刷新 API key）。
    """
    if not api_key:
        api_key = _fetch_api_key()
    if timeout <= 0:
        timeout = get_env_int("KN_ROUTER_TIMEOUT", 15)

    _clean_expired_cache()

    cache_key = (session_id, message)
    cached = _router_cache.get(cache_key)
    if cached is not None:
        return cached

    safe_msg = message[:300] + message[-200:] if len(message) > 500 else message
    safe_msg = safe_msg.replace("\n", " ").replace("\r", " ")

    start_time = time.time()
    fallback_reason = "unknown"

    for attempt in range(2):
        try:
            current_key = _fetch_api_key() if attempt == 1 else api_key

            resp = httpx.post(
                f"{api_url.rstrip('/')}/chat/completions",
                json={
                    "model": model,
                    "temperature": 0.1,
                    "max_tokens": 512,  # Round2: 256→512 防DeepSeek推理文本截断导致JSON输出不全
                    "thinking": {"type": "disabled"},
                    "messages": [
                        {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                        {"role": "user", "content": f"消息：{safe_msg}\n\nJSON 输出："},
                    ],
                },
                headers={"Authorization": f"Bearer {current_key}"} if current_key else {},
                timeout=timeout,
            )
            resp.raise_for_status()
            choice = resp.json()["choices"][0]["message"]
            raw = choice.get("content") or ""
            if not raw.strip():
                rc = choice.get("reasoning_content", "") or ""
                if rc:
                    logger.info("Router content 为空, 使用 reasoning_content (%d chars)", len(rc))
                    raw = rc
            raw = raw.strip()

            mask = _parse_mask(raw)
            if mask is None:
                fallback_reason = "json_parse"
                _FALLBACK_COUNTER["json_parse"] += 1
                logger.warning("Router JSON 解析失败, fallback 四路全开, raw: %s", raw[:200])
                mask = {"h": True, "kt": True, "s": True, "sag": True}
            else:
                fallback_reason = "success"

            duration = time.time() - start_time
            logger.info("Router 调用成功, mask=%s, duration=%.2fs", mask, duration)

            _router_cache[cache_key] = mask
            _router_cache_timestamps[cache_key] = time.time()
            if len(_router_cache) > _ROUTER_CACHE_MAX:
                _evict = _ROUTER_CACHE_MAX // 2
                evict_keys = list(_router_cache.keys())[:_evict]
                for k in evict_keys:
                    _router_cache.pop(k, None)
                    _router_cache_timestamps.pop(k, None)
            return mask

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                _FALLBACK_COUNTER["api_401"] += 1
                if attempt == 0:
                    logger.warning("Router 401 Unauthorized, 尝试刷新 API key 并重试")
                    continue
                fallback_reason = "api_401"
                logger.warning("Router 401 Unauthorized 重试失败, fallback 四路全开")
            else:
                _FALLBACK_COUNTER["api_other"] += 1
                fallback_reason = f"api_{e.response.status_code}"
                logger.warning("Router HTTP 错误 (%s), fallback 四路全开", e)
            break

        except httpx.TimeoutException:
            _FALLBACK_COUNTER["api_timeout"] += 1
            fallback_reason = "api_timeout"
            logger.warning("Router 调用超时, fallback 四路全开")
            break

        except Exception as e:
            _FALLBACK_COUNTER["api_error"] += 1
            fallback_reason = "api_error"
            logger.warning("Router 调用失败 (%s), fallback 四路全开", e)
            break

    duration = time.time() - start_time
    logger.info("Router fallback 四路全开, reason=%s, duration=%.2fs", fallback_reason, duration)

    mask = {"h": True, "kt": True, "s": True, "sag": True}
    _router_cache[cache_key] = mask
    _router_cache_timestamps[cache_key] = time.time()
    return mask