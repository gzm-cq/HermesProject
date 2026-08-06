"""LLM 调用适配器 — 封装 API 调用

通过 urllib.request（stdlib）调用兼容 OpenAI API 格式的 LLM 服务。
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Any

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM API 调用客户端

    使用 urllib.request（零外部依赖），兼容 LiteLLM / OpenAI API。
    """

    def __init__(
        self,
        api_url: str,
        model: str,
        api_key: str = "",
        timeout: int = 60,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 2048,  # min 2048 for sensenova-6.7-flash-lite fallback JSON output
        response_format: dict | None = None,
    ) -> dict[str, Any]:
        """调用 LLM chat completion API

        Args:
            messages: OpenAI 格式消息列表
            temperature: 生成温度（反思任务用低温保持稳定性）
            max_tokens: 最大输出 token
            response_format: 响应格式约束（如 {"type": "json_object"}）

        Returns:
            LLM 返回的完整 JSON 响应

        Raises:
            ConnectionError: API 不可达
            ValueError: 响应格式异常
        """
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            body["response_format"] = response_format

        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        req = urllib.request.Request(
            self._api_url,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error("LLM API HTTP %s: %s", e.code, error_body)
            raise ConnectionError(f"LLM API HTTP {e.code}: {error_body}") from e
        except urllib.error.URLError as e:
            logger.error("LLM API 不可达: %s", e.reason)
            raise ConnectionError(f"LLM API 不可达: {e.reason}") from e
        except json.JSONDecodeError as e:
            logger.error("LLM 响应 JSON 解析失败: %s", e)
            raise ValueError(f"LLM 响应 JSON 解析失败: {e}") from e

        return result

    def extract_content(self, response: dict[str, Any]) -> str:
        """从 API 响应中提取文本内容"""
        try:
            return response["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise ValueError(f"LLM 响应格式异常: {e}") from e

    def parse_json_response(self, text: str) -> dict[str, Any]:
        """从 LLM 响应文本中解析 JSON

        兼容 LLM 返回 markdown 包裹 ```json ... ``` 的情况。
        """
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # 去除 markdown 代码块标记
            for prefix in ("```json\n", "```json", "```\n", "```"):
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix):]
                    break
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())
