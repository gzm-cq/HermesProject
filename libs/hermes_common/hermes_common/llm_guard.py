"""Hermes LLM 调用公共护栏模块（唯一事实来源 / single source of truth）。

本文件是「所有 LLM 调用统一护栏」的唯一来源，作为统一共享库 ``hermes_common``
的一部分部署到生产机的 ``/root/.hermes/lib/hermes_common/llm_guard.py``。各子项目
（self-evolving、memory-cleanup、recall-eval、knowledge-tree-builder 等）通过解析器
``_load_common_llm_guard()`` 从 **hermes_common** 加载本模块，不再内置副本——
副本会造成「护栏只在某处更新、其余腐化」的漂移，必须杜绝。

护栏分两层：
  A 层 · 模型行为 / 解析（零耦合纯函数，所有客户端复用）：
    - build_chat_body()      构建请求体：thinking 禁用 + JSON-only 系统约束 + max_tokens 钳制
    - extract_content()      content 空时兜底 reasoning / reasoning_content
    - parse_json_response()  健壮 JSON 解析：markdown→raw_decode→括号级→思考前缀剥离
    - clamp_max_tokens()     max_tokens 下限保护（防推理模型 reasoning 吃光预算致 content 空）
  B 层 · 传输层（重试/退避/429 Retry-After/超时不再重试/空内容重试/限速）：
    - LLMTransportError     传输层统一异常（status / retryable / retry_after）
    - make_urllib_post()    urllib(stdlib) 传输回调，返回 post_fn
    - make_requests_post()  requests 传输回调（惰性 import，common 自身零第三方依赖）
    - guarded_chat_completion(post_fn, ...)  **统一传输策略**：所有客户端共用同一套
      重试/退避/429/超时/空内容决策，仅 post_fn 不同（urllib vs requests）。

开关均带环境变量兜底，可灰度回退：
    HERMES_SE_DISABLE_THINKING  默认 "1"（禁用推理模型思考）；置 "0" 关闭回退
    HERMES_SE_MIN_CALL_INTERVAL 相邻 LLM 调用最小间隔（秒），默认 "0.5"

仅使用标准库（json/time/threading/urllib/os/logging/importlib）；requests 仅在
make_requests_post 被实际调用时惰性导入，故本模块本身零第三方依赖。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 默认配置（环境变量兜底）
# --------------------------------------------------------------------------

# 相邻两次 LLM 请求之间的最小间隔（秒），避免零间隔狂发打满 RPM 配额。
_DEFAULT_MIN_CALL_INTERVAL = float(os.environ.get("HERMES_SE_MIN_CALL_INTERVAL", "0.5"))

# JSON 场景系统级强约束（提升词）：仅靠 response_format=json_object 不足以约束推理模型
# (sensenova-6.8-flash-lite)，它仍会输出 "Thinking Process:..." 思考文本而非 JSON。
# 必须在 prompt 层显式禁止思考。该消息在检测到 JSON 请求时自动前置注入。
JSON_ONLY_SYSTEM = (
    "你只能输出一个合法的 JSON 对象，禁止输出任何思考过程、分析、解释、"
    "前缀说明或 markdown 代码块。直接以 { 开始、以 } 结束，不要输出其他任何文字。"
    "若你需要推理，请在内部完成，最终只给出 JSON。"
)

# max_tokens 默认下限：推理模型 reasoning 会占用 token 预算，偏小会导致 content 为空。
_DEFAULT_MAX_TOKENS_FLOOR = 16384


def _thinking_disabled() -> bool:
    """是否禁用推理模型思考过程（thinking）。默认开启，可用 env 回退关闭。"""
    return os.environ.get("HERMES_SE_DISABLE_THINKING", "1") != "0"


# --------------------------------------------------------------------------
# A 层 · 模型行为 / 解析
# --------------------------------------------------------------------------

def clamp_max_tokens(val: int | None, lo: int = _DEFAULT_MAX_TOKENS_FLOOR, hi: int | None = None) -> int:
    """钳制 max_tokens 到 [lo, hi]。

    - 下限 lo 防止推理模型 reasoning 吃光预算导致 content 空（畸形/空回包）。
    - 上限 hi（可选）防止慢模型生成过长触发超时长尾。
    """
    v = int(val or 0)
    if v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def build_chat_body(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = _DEFAULT_MAX_TOKENS_FLOOR,
    json_mode: bool = False,
    max_tokens_floor: int = _DEFAULT_MAX_TOKENS_FLOOR,
    max_tokens_cap: int | None = None,
    disable_thinking: bool | None = None,
) -> dict[str, Any]:
    """构建 OpenAI 兼容 chat completion 请求体，统一注入护栏。

    Args:
        model: 模型名
        messages: OpenAI 格式消息列表
        temperature: 生成温度
        max_tokens: 期望最大输出 token（会被钳制到 [floor, cap]）
        json_mode: 是否 JSON 输出模式（注入 JSON-only 系统约束 + response_format）
        max_tokens_floor: max_tokens 下限（默认 16384）
        max_tokens_cap: max_tokens 上限（可选）
        disable_thinking: 是否禁用推理模型思考；None 时按环境变量 _thinking_disabled() 决定

    Returns:
        可直接 json.dumps 的请求体 dict
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": clamp_max_tokens(max_tokens, lo=max_tokens_floor, hi=max_tokens_cap),
    }

    # 禁用推理模型的思考过程(thinking)，避免 reasoning 占满 token 预算导致
    # content 为空 / 输出 "Thinking Process:..." 而非 JSON。
    if disable_thinking if disable_thinking is not None else _thinking_disabled():
        body["thinking"] = {"type": "disabled"}

    # JSON 场景：注入强约束 system 消息，抑制推理模型输出思考过程/解释/markdown。
    # 仅靠 response_format 不够，必须用提升词约束。
    if json_mode:
        body["response_format"] = {"type": "json_object"}
        if not any(isinstance(m, dict) and m.get("role") == "system" for m in body["messages"]):
            body["messages"] = [{"role": "system", "content": JSON_ONLY_SYSTEM}] + list(body["messages"])

    return body


def extract_content(message: dict[str, Any]) -> str:
    """从 API 响应的 message 对象中提取文本内容。

    content 非空时优先返回；为空时兜底从 reasoning / reasoning_content 抽取
    （推理模型常把最终答案放在 reasoning 字段）。两者皆空抛 ValueError。
    """
    if not isinstance(message, dict):
        raise ValueError(f"LLM message 非对象: {type(message)}")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    fallback = message.get("reasoning") or message.get("reasoning_content")
    if isinstance(fallback, str) and fallback.strip():
        return fallback
    raise ValueError("LLM 响应 content 与 reasoning 均为空")


def parse_json_response(text: str, *, allow_thinking_prefix: bool = True) -> dict[str, Any]:
    """从 LLM 响应文本中解析 JSON，兼容多种畸形输出。

    解析链（每个候选文本依次尝试）：
      1) 整体 json.loads；
      2) raw_decode 提取首个完整 JSON 对象（容忍尾部多余文本 / 多对象）；
      3) 截取首个 `{` 到末个 `}`；
      4) 剥离常见思考前缀(Thinking Process:/Thinking:/思考过程：/Let me think/Here is the JSON ...) 后重试 1-3；
      5) 通用括号级提取：遍历文本每个 `{`/`[` 位置用 raw_decode 尝试（覆盖嵌套/前缀/后缀）。
    任一候选成功即返回；全失败抛 ValueError。
    """
    if text is None:
        raise ValueError("LLM 响应文本为空")

    candidates: list[str] = []

    def _add(c: str) -> None:
        c = c.strip()
        if c:
            candidates.append(c)

    _add(text)

    # 去 markdown 代码块包裹
    cleaned = text.strip()
    if cleaned.startswith("```"):
        for prefix in ("```json\n", "```json", "```\n", "```"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                break
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    _add(cleaned)

    # 剥离常见思考/说明前缀，保留其后内容重试
    if allow_thinking_prefix:
        _THINK_PREFIXES = (
            "Thinking Process:", "Thinking:", "思考过程：", "思考过程:",
            "Let me think", "Here is the JSON", "Here's the JSON",
            "以下是 JSON", "以下是JSON", "输出 JSON：", "输出 JSON:",
            "我的分析如下", "分析如下",
        )
        for prefix in _THINK_PREFIXES:
            idx = cleaned.find(prefix)
            if idx != -1:
                _add(cleaned[idx + len(prefix):])

    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        # 1) 整体解析
        try:
            result = json.loads(cand)
            if isinstance(result, dict):
                return result
            if isinstance(result, list) and result and isinstance(result[0], dict):
                return result[0]
        except json.JSONDecodeError:
            pass
        # 2) raw_decode 首个对象
        try:
            obj, _ = json.JSONDecoder().raw_decode(cand)
            if isinstance(obj, dict):
                return obj
            if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                return obj[0]
        except (json.JSONDecodeError, ValueError):
            pass
        # 3) 截取首个 { 到末个 }
        start = cand.find("{")
        end = cand.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cand[start:end + 1])
            except json.JSONDecodeError:
                pass
        # 5) 通用括号级提取：在每个 { 或 [ 位置尝试 raw_decode
        for ch in ("{", "["):
            pos = cand.find(ch)
            while pos != -1:
                try:
                    obj, _ = json.JSONDecoder().raw_decode(cand[pos:])
                    if isinstance(obj, dict):
                        return obj
                    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                        return obj[0]
                except (json.JSONDecodeError, ValueError):
                    pass
                pos = cand.find(ch, pos + 1)

    raise ValueError(f"无法从响应中解析 JSON: {text[:200]!r}")


# --------------------------------------------------------------------------
# B 层 · 传输层（统一策略）
# --------------------------------------------------------------------------

class RateLimiter:
    """进程内全局请求节流器：保证相邻两次 LLM 调用至少间隔 min_interval 秒。"""

    def __init__(self, min_interval: float = _DEFAULT_MIN_CALL_INTERVAL) -> None:
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last_ts = 0.0

    def throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_ts = time.monotonic()


class LLMTransportError(Exception):
    """传输层统一异常，承载 guarded_chat_completion 做重试决策所需的全部信息。

    Attributes:
        status: int（HTTP 状态码）或 str（"timeout" / "network" 等非 HTTP 故障）
        retryable: 是否可重试。4xx 业务错误(非 429) = False；5xx / 429 / network = True
        retry_after: float | None，仅 429 携带 Retry-After 头（秒）
        message: 可读的错误描述（用于日志）
    """

    def __init__(
        self,
        status: int | str,
        retryable: bool,
        retry_after: float | None = None,
        message: str = "",
    ) -> None:
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
        self.message = message
        super().__init__(message)


def _parse_retry_after(value: str | None, default_delay: float) -> float:
    """解析 HTTP Retry-After 头（秒），无效时回退到默认退避时长。"""
    if value:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
    return default_delay


# ---- 传输回调：把具体 HTTP 栈的差异收敛为统一的 post_fn 契约 ----
# post_fn(body: dict, timeout: float) -> dict（返回解析后的 OpenAI 响应 dict）
# 失败时抛 LLMTransportError，由 guarded_chat_completion 统一决策重试/放弃。

def make_urllib_post(
    api_url: str,
    api_key: str = "",
    extra_headers: dict[str, str] | None = None,
) -> Callable[[dict, float], dict]:
    """构造基于 urllib（stdlib，零外部依赖）的传输回调。"""
    base_url = (api_url or "").rstrip("/")

    def _post(body: dict, timeout: float) -> dict:
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(base_url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            retry_after = e.headers.get("Retry-After") if e.headers else None
            err_body = e.read().decode("utf-8", errors="replace")
            raise LLMTransportError(
                status=e.code,
                retryable=(e.code >= 500 or e.code == 429),
                retry_after=retry_after,
                message=err_body[:500],
            ) from e
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", None)
            is_timeout = isinstance(reason, TimeoutError) or (
                isinstance(reason, str) and "timed out" in reason.lower()
            )
            # socket 超时属慢模型/网络拥塞，重发同样的请求大概率仍超时，
            # 不重试（避免 3×timeout 极端长尾，最坏 9 分钟/task）。
            raise LLMTransportError(
                status="timeout" if is_timeout else "network",
                retryable=not is_timeout,
                message=str(e),
            ) from e

    return _post


def make_requests_post(
    api_url: str,
    api_key: str = "",
    extra_headers: dict[str, str] | None = None,
) -> Callable[[dict, float], dict]:
    """构造基于 requests 的传输回调。

    requests 为第三方依赖，此处**惰性导入**——只有真正走 requests 的客户端调用本函数时
    才会 import，从而保证 hermes_common/llm_guard.py 自身在任何环境（含仅 stdlib 的 urllib 客户端）
    都可被 import，不强制安装 requests。
    """
    base_url = (api_url or "").rstrip("/")

    def _post(body: dict, timeout: float) -> dict:
        import requests  # 惰性导入，保持 common 零第三方依赖
        from requests.exceptions import (
            HTTPError,
            Timeout,
            ConnectionError as _ReqConnectionError,
            RequestException,
        )

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update(extra_headers)
        try:
            r = requests.post(base_url, json=body, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except HTTPError as e:
            resp = getattr(e, "response", None)
            status = resp.status_code if resp is not None else None
            retry_after = resp.headers.get("Retry-After") if resp is not None else None
            retryable = (status is not None and (status >= 500 or status == 429))
            raise LLMTransportError(
                status=status if status is not None else "network",
                retryable=retryable,
                retry_after=retry_after,
                message=str(e),
            ) from e
        except Timeout as e:
            # 超时不再重试，避免长尾
            raise LLMTransportError("timeout", False, None, str(e)) from e
        except _ReqConnectionError as e:
            raise LLMTransportError("network", True, None, str(e)) from e
        except RequestException as e:
            raise LLMTransportError("network", True, None, str(e)) from e

    return _post


class _RetryableLLMError(Exception):
    """LLM 返回 200 但 content 缺失/空，属可重试的瞬时故障（后端偶发畸形响应）。"""
    pass


def _require_content(data: dict) -> None:
    """校验响应含非空 content；缺失/空则视情况抛 _RetryableLLMError（可重试）。

    - 若 message 同时含非空 reasoning（推理模型把输出放进 reasoning 而 content 为空），
      视为「已产生输出」，放行（交由 extract_content 从 reasoning 兜底抽取），不再重试。
    - 若 content 与 reasoning 皆空，才是真·畸形/空回包，触发退避重试。
    """
    if not isinstance(data, dict):
        raise _RetryableLLMError(f"响应非 JSON 对象: {type(data)}")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _RetryableLLMError(f"响应无 choices: keys={list(data.keys())}")
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        raise _RetryableLLMError(f"响应 message 异常: {msg}")
    content = msg.get("content")
    reasoning = (msg.get("reasoning") or msg.get("reasoning_content") or "")
    if isinstance(content, str) and content.strip():
        return  # content 非空，正常放行
    # content 空：若 reasoning 非空，说明推理模型已产出输出（落在 reasoning），放行不重试
    if isinstance(reasoning, str) and reasoning.strip():
        return
    raise _RetryableLLMError(f"响应 content 与 reasoning 均为空: content={content!r}")


def guarded_chat_completion(
    post_fn: Callable[[dict, float], dict],
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = _DEFAULT_MAX_TOKENS_FLOOR,
    json_mode: bool = False,
    timeout: float = 60.0,
    max_retries: int = 3,
    min_interval: float = _DEFAULT_MIN_CALL_INTERVAL,
    max_tokens_floor: int = _DEFAULT_MAX_TOKENS_FLOOR,
    max_tokens_cap: int | None = None,
    disable_thinking: bool | None = None,
    rate_limiter: RateLimiter | None = None,
) -> dict[str, Any]:
    """带全护栏的 LLM 调用（传输无关，统一策略）。

    所有客户端共用本函数，仅传入不同的 post_fn（urllib / requests）。
    护栏集合（**单一实现，杜绝漂移**）：
      - 请求前强制最小间隔（限速，防打满 RPM 配额）
      - 429 限流：指数退避并尊重 Retry-After 头
      - 4xx 业务错误(非429)：不重试，直接抛 ConnectionError
      - 5xx 服务端错误：指数退避重试
      - 网络 URLError/ConnectionError：重试
      - **超时(Timeout)：不再重试**（避免 3×timeout 长尾）
      - 200 但 content/reasoning 皆空：视为可重试瞬时故障，退避重试
      - thinking 禁用 + JSON-only 系统约束 + max_tokens 钳制（经 build_chat_body）

    Args:
        post_fn: 传输回调，签名 (body: dict, timeout: float) -> dict（返回解析后的响应 dict），
                 失败时抛 LLMTransportError。可由 make_urllib_post / make_requests_post 构造。
        model / messages / temperature / max_tokens / json_mode / disable_thinking / max_tokens_floor / max_tokens_cap: 见 build_chat_body
        timeout: 单次请求超时（秒），传给 post_fn
        max_retries: 最大尝试次数（含首次）
        min_interval: 相邻调用最小间隔（秒），传给 RateLimiter
        rate_limiter: 可选共享 RateLimiter（跨多次调用限速）

    Returns:
        LLM 返回的完整 JSON 响应 dict

    Raises:
        ConnectionError: API 不可达 / 限流耗尽 / 超时 / 4xx 业务错误 / 空内容耗尽
    """
    body = build_chat_body(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=json_mode,
        max_tokens_floor=max_tokens_floor,
        max_tokens_cap=max_tokens_cap,
        disable_thinking=disable_thinking,
    )

    limiter = rate_limiter or RateLimiter(min_interval)
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        # 限流保护：请求前强制最小间隔
        limiter.throttle()
        try:
            resp = post_fn(body, timeout)
        except LLMTransportError as e:
            last_exc = e
            if e.status == 429:
                delay = _parse_retry_after(e.retry_after, 1.0 * (2 ** (attempt - 1)))
                if attempt < max_retries:
                    logger.warning(
                        "LLM API HTTP 429 限流 (attempt %d/%d)，%0.1fs 后重试: %s",
                        attempt, max_retries, delay, e.message[:200],
                    )
                    time.sleep(delay)
                    continue
                logger.error("LLM API HTTP 429 限流，重试 %d 次仍失败", max_retries)
                raise ConnectionError(f"LLM API HTTP 429 (限流): {e.message}") from e
            if e.status == "timeout":
                logger.error("LLM 调用超时(不重试，避免长尾): %s", e.message)
                raise ConnectionError(f"LLM 调用超时: {e.message}") from e
            if not e.retryable:
                logger.error("LLM API HTTP %s (业务错误，不重试): %s", e.status, e.message)
                raise ConnectionError(f"LLM API HTTP {e.status}: {e.message}") from e
            # 5xx / network：可重试
            if attempt < max_retries:
                delay = 1.0 * (2 ** (attempt - 1))
                logger.warning(
                    "LLM 调用失败 (attempt %d/%d)，%0.1fs 后重试: %s",
                    attempt, max_retries, delay, e.message,
                )
                time.sleep(delay)
                continue
            logger.error("LLM 调用失败（%s），重试 %d 次仍失败: %s", e.status, max_retries, e.message)
            raise ConnectionError(f"LLM 调用失败: {e.message}") from e

        # 200 但 content 缺失/空：后端偶发畸形响应，视为可重试瞬时故障
        try:
            _require_content(resp)
        except _RetryableLLMError as e:
            last_exc = e
            if attempt < max_retries:
                delay = 1.0 * (2 ** (attempt - 1))
                logger.warning(
                    "LLM 返回空内容 (attempt %d/%d)，%0.1fs 后重试: %s",
                    attempt, max_retries, delay, e,
                )
                time.sleep(delay)
                continue
            logger.error("LLM 连续 %d 次返回空内容，放弃该请求", max_retries)
            raise ConnectionError(f"LLM 返回空内容: {e}") from e

        return resp

    # 重试耗尽（极端情况兜底）
    logger.error("LLM 调用重试 %d 次后仍失败: %s", max_retries, last_exc)
    raise ConnectionError(f"LLM 调用失败: {last_exc}") from last_exc
