"""LLM 调用适配器 — 封装 API 调用

通过 urllib.request（零外部依赖）调用兼容 OpenAI API 格式的 LLM 服务。
"""
import json
import logging
import time
import urllib.request
import urllib.error
from typing import Any

logger = logging.getLogger(__name__)

# 默认重试配置
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 1.0  # 秒，指数退避基数


class LLMClient:
    """LLM API 调用客户端（零外部依赖，兼容 LiteLLM / OpenAI API）"""

    def __init__(
        self,
        api_url: str,
        model: str,
        api_key: str = "",
        timeout: int = 60,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: dict | None = None,
    ) -> dict[str, Any]:
        """调用 LLM chat completion API（带重试，指数退避）"""
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            body["response_format"] = response_format

        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        req = urllib.request.Request(
            self._api_url, data=data, headers=headers, method="POST",
        )

        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                last_exc = e
                # 仅对可重试错误重试：5xx、网络错误、URLError；4xx 不重试（业务错误）
                if isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500:
                    error_body = e.read().decode("utf-8", errors="replace")
                    logger.error("LLM API HTTP %s (业务错误，不重试): %s", e.code, error_body)
                    raise ConnectionError(f"LLM API HTTP {e.code}: {error_body}") from e
                if attempt < self._max_retries:
                    delay = DEFAULT_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "LLM 调用失败 (attempt %d/%d)，%0.1fs 后重试: %s",
                        attempt, self._max_retries, delay, e,
                    )
                    time.sleep(delay)
                # else: 达到最大重试次数，下方统一抛错
            except json.JSONDecodeError as e:
                # JSON 解析失败是 LLM 返回内容问题，重试无效，直接抛错
                logger.error("LLM 响应 JSON 解析失败（不重试）: %s", e)
                raise ValueError(f"LLM 响应 JSON 解析失败: {e}") from e
        # 重试耗尽（仅 HTTPError/URLError 会走到这里）
        logger.error("LLM 调用重试 %d 次后仍失败: %s", self._max_retries, last_exc)
        if isinstance(last_exc, urllib.error.HTTPError):
            raise ConnectionError(f"LLM API HTTP {last_exc.code}") from last_exc
        if isinstance(last_exc, urllib.error.URLError):
            raise ConnectionError(f"LLM API 不可达: {last_exc.reason}") from last_exc
        raise ConnectionError(f"LLM 调用失败: {last_exc}") from last_exc

    def extract_content(self, response: dict[str, Any]) -> str:
        """从 API 响应中提取文本内容"""
        try:
            return response["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise ValueError(f"LLM 响应格式异常: {e}") from e

    def parse_json_response(self, text: str) -> dict[str, Any]:
        """从 LLM 响应文本中解析 JSON，兼容 markdown 代码块包裹"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            for prefix in ("```json\n", "```json", "```\n", "```"):
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix):]
                    break
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())
