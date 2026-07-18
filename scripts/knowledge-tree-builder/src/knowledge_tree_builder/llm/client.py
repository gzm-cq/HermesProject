"""LLM API 调用封装 — OpenAI 兼容接口"""

import json
import time
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


# 请求间间隔（秒），避免 QPS 过载
LLM_QPS_INTERVAL: float = 0.3


def call_llm(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0,
    max_tokens: int = 2048,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
    retries: int = 3,
    timeout_seconds: int = 120,
) -> str:
    """调用 LLM（OpenAI 兼容接口），带重试和指数退避。

    Args:
        prompt: 用户消息内容
        system_prompt: 系统提示（可选）
        temperature: 生成温度
        max_tokens: 最大输出 token 数
        api_url: OpenAI 兼容 API 地址
        api_key: API 密钥
        model: 模型名称
        retries: 失败重试次数

    Returns:
        LLM 返回的文本内容
    """
    if requests is None:
        return ""

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    # 请求间间隔，避免 QPS 过载
    time.sleep(LLM_QPS_INTERVAL)

    for attempt in range(retries):
        try:
            resp = requests.post(
                api_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Connection": "close",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=(10, timeout_seconds),
            )
            resp.raise_for_status()
            content = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            return content if content else ""
        except Exception:
            if attempt < retries - 1:
                time.sleep(2**attempt)  # 失败后退避一次
    return ""


def call_llm_json(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
    retries: int = 3,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """调用 LLM 并期望 JSON 响应。

    从响应中提取 JSON（支持 markdown fence 包裹），解析后返回。

    Returns:
        dict: 解析后的 JSON 对象；解析失败返回 {"error": "..."}
    """
    text = call_llm(
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=4096,
        api_url=api_url,
        api_key=api_key,
        model=model,
        retries=retries,
        timeout_seconds=timeout_seconds,
    )
    if not text:
        return {"error": "empty_response"}

    # 尝试直接解析
    text = text.strip()
    if text.startswith("```"):
        # 去掉 markdown fence
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return dict(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        # 尝试找到 JSON 对象范围
        for start in range(len(text)):
            if text[start] == "{":
                end = text.rfind("}")
                if end > start:
                    try:
                        return dict(json.loads(text[start : end + 1]))
                    except (json.JSONDecodeError, ValueError):
                        pass
        return {"error": f"parse_failed: {text[:200]}"}
