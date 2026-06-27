"""
Hermes AI Client — 委托式 LLM 调用
====================================
不再直接调 HTTP API。统一通过 task_executor 委托给 Hermes Agent，
由 Agent 用自己的 provider 路由、credential pool、重试机制处理。

设计原则（C09）：
  - 管线模块不直接调工具/API，由 Hermes Agent 决定实现
  - ai_client 是唯一调 LLM 的模块，但通过 Agent 间接完成
  - 退化降级：task_executor 不可用时保留直接 API 调用（仅测试/离线用）

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from ..config import get_env_config
from ..core.exceptions import LLMCallError

logger = logging.getLogger(__name__)

# ── 全局 task_executor（由编排器在启动时注入） ────────────────

_task_executor: Callable[..., str] | None = None


def set_task_executor(executor: Callable[..., str] | None) -> None:
    """设置全局 task_executor，让 call_llm 通过 Hermes Agent 调用 LLM。

    由 orchestrator.run() 在管线启动时注入。一旦设置后永久生效。

    Args:
        executor: 封装 delegate_task 的可调用对象。None 则清除（测试用）。
    """
    global _task_executor
    _task_executor = executor


# ── 公开接口 ────────────────────────────────────────────────


def call_llm(
    prompt: str,
    max_iterations: int = 1,
    system_prompt: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """通过 Hermes Agent 调用 LLM（委托模式）。

    优先路径：task_executor → Hermes Agent（使用 Agent 的 provider 路由）
    降级路径：直接 HTTP 调用（仅当 task_executor 未设置时）

    Args:
        prompt: 用户提示
        max_iterations: 预留参数，与旧接口兼容
        system_prompt: 系统提示（可选）
        max_tokens: 最大输出 token 数
        temperature: 温度

    Returns:
        LLM 返回的文本，全部失败返回空字符串
    """
    if _task_executor is not None:
        return _call_via_agent(prompt, system_prompt, max_tokens, temperature)

    # 降级路径：直接 HTTP 调用（仅测试/离线场景）
    logger.warning("call_llm: task_executor 未设置，使用直接 API 调用降级")
    return _call_direct(prompt, system_prompt, max_tokens, temperature)


# ── 优先路径：委托 Hermes Agent ────────────────────────────


def _call_via_agent(
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """通过 task_executor 委托 Hermes Agent 调用 LLM。

    向 Agent 发送结构化上下文，Agent 收到后用自有 provider 路由处理。
    不使用任何工具（web_search 等），仅纯文本回复。
    """
    context = json.dumps({
        "task_type": "llm_call",
        "prompt": prompt,
        "system_prompt": system_prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }, ensure_ascii=False)

    try:
        result = _task_executor(
            goal=(
                "请根据以下要求生成回复。\n"
                "要求：\n"
                "1. 直接输出内容，不要额外解释\n"
                "2. 不要使用任何工具（web_search、文件操作等）\n"
                "3. 只需根据提供的 prompt 生成回复\n"
                "4. 输出后以 ---END--- 单独一行结束"
            ),
            context=context,
        )
        # 解析结果
        if not result:
            logger.warning("Agent 返回空结果")
            return ""

        # 提取 ---END--- 标记前的内容
        marker = "---END---"
        if marker in result:
            content = result.split(marker, 1)[0].strip()
            return content

        # 无标记时直接返回
        return result.strip()
    except (ConnectionError, OSError, TimeoutError, ValueError, RuntimeError) as e:
        logger.warning("Agent 委托失败，降级到直接调用: %s", e)
        return _call_direct(prompt, system_prompt, max_tokens, temperature)


# ── 降级路径：直接 HTTP 调用 ──────────────────────────────


# 降级用的 HTTP 底层函数（从旧版 ai_client 保留，仅异常路径）
import os as _os
import time as _time


def _resolve_env(key: str) -> str:
    """解析环境变量值，支持 ${VAR} 引用语法。"""
    val = _os.environ.get(key, "")
    if val:
        return val
    return ""


def _load_direct_config() -> list[dict[str, Any]]:
    """从环境变量加载 LLM provider 配置（降级路径用）。

    优先级：
      1. AI_REPORT_LITELLM_API_KEY → LiteLLM 网关（http://127.0.0.1:4142）
      2. AI_REPORT_DEEPSEEK_API_KEY → DeepSeek 官方 API
      3. AI_REPORT_SILICONFLOW_API_KEY → SiliconFlow API
      4. AI_REPORT_GLM_API_KEY → 智谱 AI
      5. AI_REPORT_SHANGTANG_API_KEY → 商汤 API

    旧环境变量名（LITELLM_MASTER_KEY 等）作为 fallback 保留向后兼容。
    """
    env = get_env_config()
    providers: list[dict[str, Any]] = []

    known_providers = [
        {
            "name": "litellm",
            "base_url": "http://127.0.0.1:4142",
            "api_key": env.litellm_api_key,
            "model": "s-deepseek-v4-flash",
        },
        {
            "name": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key": env.deepseek_api_key,
            "model": "deepseek-v4-flash",
        },
        {
            "name": "siliconflow",
            "base_url": "https://api.siliconflow.cn/v1",
            "api_key": env.siliconflow_api_key,
            "model": "Pro/deepseek-ai/DeepSeek-V3.2",
        },
        {
            "name": "zhipu",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": env.glm_api_key,
            "model": "glm-4-flash",
        },
        {
            "name": "shangtang",
            "base_url": "https://token.sensenova.cn/v1",
            "api_key": env.shangtang_api_key,
            "model": "sensenova-6.7-flash-lite",
        },
    ]

    for prov in known_providers:
        if prov["api_key"]:
            providers.append(prov)

    return providers


def _call_direct(
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """直接 HTTP 调用 LLM API（降级路径）。"""
    providers = _load_direct_config()
    if not providers:
        logger.error("降级调用失败：无可用 provider 配置")
        return ""

    try:
        import httpx as _httpx
    except ImportError:
        import requests as _requests  # type: ignore[import-untyped]
        _httpx = None  # type: ignore[assignment]

    for prov in providers:
        url = f"{prov['base_url']}/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": prov["model"], "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {prov['api_key']}",
            "Content-Type": "application/json",
        }

        for attempt in range(3):
            try:
                if _httpx:
                    resp = _httpx.post(url, json=payload, headers=headers, timeout=60.0)
                else:
                    resp = _requests.post(url, json=payload, headers=headers, timeout=60.0)
                resp.raise_for_status()
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    return content
            except (ConnectionError, OSError, TimeoutError, ValueError, KeyError) as e:
                logger.debug("  降级调用 %s 失败 (attempt %d): %s", prov["name"], attempt + 1, e)
                if attempt < 2:
                    _time.sleep(0.5 * (2 ** attempt))

    logger.error("降级调用：所有 provider 均失败")
    return ""


def reload_config() -> None:
    """预留接口，与旧版本兼容（现无缓存可清除）。"""
    logger.debug("reload_config: 无操作（ai_client 已无配置缓存）")
