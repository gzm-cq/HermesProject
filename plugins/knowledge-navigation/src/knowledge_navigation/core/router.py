"""LLM-driven Router for 3-way injection mask."""

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict

import httpx

from knowledge_navigation.core.source_defs import build_router_prompt
from knowledge_navigation.core.env_loader import get_env, get_env_int

logger = logging.getLogger(__name__)

# 线程安全：Router 缓存与计数器被 4-worker ThreadPoolExecutor 并发读写，
# 必须加锁保护，否则 dict 迭代时被修改会触发 RuntimeError。
_router_lock = threading.Lock()
_router_cache: OrderedDict[tuple[str, str], dict[str, bool]] = OrderedDict()
_ROUTER_CACHE_MAX = 64
_ROUTER_CACHE_TTL = 300

_router_cache_timestamps: dict[tuple[str, str], float] = {}

_ROUTER_SYSTEM_PROMPT = build_router_prompt()

_FALLBACK_COUNTER = {"json_parse": 0, "api_error": 0, "api_401": 0, "api_timeout": 0, "api_other": 0}


def _clean_expired_cache() -> None:
    """清理过期缓存条目（调用方需持有 _router_lock）。"""
    now = time.time()
    to_remove = [k for k, ts in _router_cache_timestamps.items() if now - ts > _ROUTER_CACHE_TTL]
    for k in to_remove:
        _router_cache.pop(k, None)
        _router_cache_timestamps.pop(k, None)


def _cache_get(cache_key: tuple[str, str]) -> dict[str, bool] | None:
    """线程安全的缓存读取（读取时标记为最近使用）。"""
    with _router_lock:
        _clean_expired_cache()
        val = _router_cache.get(cache_key)
        if val is not None:
            _router_cache.move_to_end(cache_key)
        return val


def _cache_put(cache_key: tuple[str, str], mask: dict[str, bool]) -> None:
    """线程安全的缓存写入（LRU 淘汰：超出容量时淘汰最久未使用）。"""
    with _router_lock:
        _router_cache[cache_key] = mask
        _router_cache.move_to_end(cache_key)
        _router_cache_timestamps[cache_key] = time.time()
        while len(_router_cache) > _ROUTER_CACHE_MAX:
            evict_key, _ = _router_cache.popitem(last=False)
            _router_cache_timestamps.pop(evict_key, None)


def _incr_fallback(key: str) -> None:
    """线程安全的 fallback 计数器自增。"""
    with _router_lock:
        _FALLBACK_COUNTER[key] = _FALLBACK_COUNTER.get(key, 0) + 1


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
    #    用嵌套花括号计数替代非贪婪正则——LLM 输出嵌套对象（如 {"h": true, "extra": {"detail": 1}}）
    #    时，非贪婪 `\{[^{}]*\}` 会过早停在内层 `}`，导致解析失败。
    if data is None:
        m = re.search(r"\{", text)
        if m:
            try:
                start = m.start()
                depth = 0
                in_str = False
                escape = False
                end = -1
                for i, ch in enumerate(text[start:], start=start):
                    if escape:
                        escape = False
                        continue
                    if ch == "\\":
                        escape = True
                        continue
                    if ch == '"':
                        in_str = not in_str
                        continue
                    if in_str:
                        continue
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end > start:
                    candidate = text[start:end]
                    data = json.loads(candidate)
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

    # 6) Regex field extraction: LLM outputs reasoning text with partial JSON
    #    e.g. "分析...需要历史经验。\n{\"h\": true, \"kt\": false, \"s\":"
    #    Extract individual "key": value pairs via regex
    if not isinstance(data, dict) or not all(k in data for k in ('h', 'kt', 's', 'sag')):
        fields = {}
        for key in ('h', 'kt', 's', 'sag'):
            m = re.search(rf'"{key}"\s*:\s*(true|false)', text, re.IGNORECASE)
            if m:
                fields[key] = m.group(1).lower() == 'true'
        if len(fields) == 4:
            data = fields

    # After salvage, default missing keys to False (conservative)
    # rather than discarding the entire mask — LLM may omit sag when
    # it judges SAG irrelevant, but h/kt/s are still valid
    if isinstance(data, dict):
        for k in ('h', 'kt', 's', 'sag'):
            if k not in data:
                data[k] = False

    if not isinstance(data, dict):
        return None

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        logger.debug("Router confidence invalid, applying fallback h/kt/s 全开+sag关")
        return {"h": True, "kt": True, "s": True, "sag": False}
    if confidence < 0.3:
        logger.debug("Router confidence=%.2f < 0.3, applying fallback h/kt/s 全开+sag关", confidence)
        return {"h": True, "kt": True, "s": True, "sag": False}

    return {
        "h": bool(data.get("h", False)),
        "kt": bool(data.get("kt", False)),
        "s": bool(data.get("s", False)),
        "sag": bool(data.get("sag", False)),
    }


_CORE_TECH_KEYWORDS = frozenset({
    # 系统组件名（不来自 skill 列表，架构级固定）
    "skill", "kt", "sag", "hindsight", "litellm", "kn ", "router",
    "gateway", "plugin", "deploy", "cron", "embed", "rerank",
    "min_score", "token", "budget", "recall", "inject",
    # 操作动词
    "修复", "检查", "排他", "管道", "部署", "测试",
    "运行", "验证", "重启", "日志", "错误", "报警", "飞轮",
    "知识树", "聚类", "记忆", "清理", "巡检", "基线",
})

_dynamic_keywords_cache: frozenset[str] | None = None


def _get_dynamic_keywords() -> frozenset[str]:
    """Lazy load tech keywords from skill_matcher, with fallback to empty set."""
    global _dynamic_keywords_cache
    if _dynamic_keywords_cache is not None:
        return _dynamic_keywords_cache
    try:
        from knowledge_navigation.core.skill_matcher import get_tech_keywords
        _dynamic_keywords_cache = get_tech_keywords()
    except Exception:
        _dynamic_keywords_cache = frozenset()
    return _dynamic_keywords_cache


def _has_substantive_content(msg: str) -> bool:
    """Check if a message contains technical substance beyond pure chitchat.

    Used as a guard when Router LLM returns all-False — if the query contains
    question marks, technical terms, or action verbs, it's likely a false negative.
    """
    # Question marks → substantive
    if "？" in msg or "?" in msg:
        return True
    # Technical keywords: core + dynamic (from skill list)
    msg_lower = msg.lower()
    for kw in _CORE_TECH_KEYWORDS:
        if kw in msg_lower:
            return True
    for kw in _get_dynamic_keywords():
        if kw in msg_lower:
            return True
    return False


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

    cache_key = (session_id, message)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    safe_msg = message[:300] + message[-200:] if len(message) > 500 else message
    safe_msg = safe_msg.replace("\n", " ").replace("\r", " ")

    start_time = time.time()
    fallback_reason = "unknown"

    # 项目硬约束：SAG fallback 策略在正常和异常路径必须统一为 sag: False
    # SAG 是延迟加载源，仅在 Router 明确开启时才召回，fallback 时关闭避免误触发
    FALLBACK_MASK = {"h": True, "kt": True, "s": True, "sag": False}

    for attempt in range(2):
        try:
            current_key = _fetch_api_key() if attempt == 1 else api_key

            resp = httpx.post(
                f"{api_url.rstrip('/')}/chat/completions",
                json={
                    "model": model,
                    "temperature": 0.1,
                    "max_tokens": 2048,  # Round3: 512→2048 适配sensenova-6.7-flash-lite fallback（需≥2048返回完整JSON）
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
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                _incr_fallback("empty_choices")
                logger.warning("Router LLM 返回空 choices, fallback h/kt/s 全开+sag关")
                fallback_reason = "empty_choices"
                break
            choice = choices[0].get("message", {})
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
                _incr_fallback("json_parse")
                logger.warning("Router JSON 解析失败, fallback h/kt/s 全开+sag关, raw: %s", raw[:200])
                mask = dict(FALLBACK_MASK)
            elif not any(mask.values()):
                # LLM returned all-False — guard against false negatives
                # by checking for substantive keywords in the original message
                if _has_substantive_content(safe_msg):
                    logger.info("Router 全关但 query 含实质内容, fallback h/kt/s 全开+sag关, query=%s", safe_msg[:60])
                    mask = dict(FALLBACK_MASK)
                    fallback_reason = "all_off_guarded"
                else:
                    fallback_reason = "success_all_off"
            else:
                fallback_reason = "success"

            duration = time.time() - start_time
            logger.info("Router 调用成功, mask=%s, duration=%.2fs", mask, duration)

            _cache_put(cache_key, mask)
            return mask

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                _incr_fallback("api_401")
                if attempt == 0:
                    logger.warning("Router 401 Unauthorized, 尝试刷新 API key 并重试")
                    continue
                fallback_reason = "api_401"
                logger.warning("Router 401 Unauthorized 重试失败, fallback h/kt/s 全开+sag关")
            else:
                _incr_fallback("api_other")
                fallback_reason = f"api_{e.response.status_code}"
                logger.warning("Router HTTP 错误 (%s), fallback h/kt/s 全开+sag关", e)
            break

        except httpx.TimeoutException:
            _incr_fallback("api_timeout")
            fallback_reason = "api_timeout"
            logger.warning("Router 调用超时, fallback h/kt/s 全开+sag关")
            break

        except Exception as e:
            _incr_fallback("api_error")
            fallback_reason = "api_error"
            logger.warning("Router 调用失败 (%s), fallback h/kt/s 全开+sag关", e)
            break

    duration = time.time() - start_time
    logger.info("Router fallback h/kt/s 全开+sag关, reason=%s, duration=%.2fs", fallback_reason, duration)

    mask = dict(FALLBACK_MASK)
    _cache_put(cache_key, mask)
    return mask