"""LLM API 调用封装 — OpenAI 兼容接口

所有 LLM 调用护栏（response_format=json_object / JSON-only 系统约束 / max_tokens 钳制 / 健壮 JSON 解析 /
重试 / 429 退避 / 超时不再重试 / 限速 / 空内容重试）统一由 hermes_common.llm_guard 的
``guarded_chat_completion`` **单一实现** 提供；本文件仅做薄封装。
"""

import importlib.util
import json
import os
import sys
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


def _load_common_llm_guard():
    """定位并加载 hermes_common.llm_guard（唯一事实来源）。

    查找顺序：① 开发态仓库 libs/hermes_common/hermes_common/llm_guard.py；
              ② 生产部署 /root/.hermes/lib/hermes_common/llm_guard.py。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates: list[str] = []
    # 开发态：从 __file__ 向上定位仓库根（含 libs/ 的目录）
    d = here
    root = None
    for _ in range(12):
        if os.path.isdir(os.path.join(d, "libs")):
            root = d
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    if root is not None:
        candidates.append(
            os.path.join(root, "libs", "hermes_common", "hermes_common", "llm_guard.py")
        )
    # 生产部署
    candidates.append("/root/.hermes/lib/hermes_common/llm_guard.py")
    for path in candidates:
        if os.path.isfile(path):
            # 将包父目录注入 sys.path，确保 hermes_common.llm_guard 作为子模块正确加载
            pkg_parent = os.path.dirname(os.path.dirname(path))
            if pkg_parent not in sys.path:
                sys.path.insert(0, pkg_parent)
            spec = importlib.util.spec_from_file_location("hermes_common.llm_guard", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError(
        "无法定位 hermes_common.llm_guard。请确认统一共享库已部署："
        "在仓库根目录执行 `./deploy/deploy.sh deploy hermes-common`"
    )


_lg = _load_common_llm_guard()
guarded_chat_completion = _lg.guarded_chat_completion
make_requests_post = _lg.make_requests_post
extract_content = _lg.extract_content
parse_json_response = _lg.parse_json_response


# 请求间间隔（秒），避免 QPS 过载（同时作为限速器最小间隔）
LLM_QPS_INTERVAL: float = 0.3


def call_llm(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0,
    top_p: float | None = None,
    max_tokens: int = 16384,
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
        top_p: 核采样（None 不写入，交由服务端默认）
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

    # 传输层统一由 common.guarded_chat_completion 处理重试/退避/429/超时/空内容
    post_fn = make_requests_post(api_url, api_key)
    try:
        resp = guarded_chat_completion(
            post_fn,
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            json_mode=False,
            timeout=timeout_seconds,
            max_retries=retries,
            min_interval=LLM_QPS_INTERVAL,
        )
    except ConnectionError:
        return ""

    try:
        return str(extract_content(resp["choices"][0]["message"]))
    except (ValueError, KeyError):
        return ""


def call_llm_json(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0,
    top_p: float | None = None,
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
        top_p=top_p,
        max_tokens=16384,
        api_url=api_url,
        api_key=api_key,
        model=model,
        retries=retries,
        timeout_seconds=timeout_seconds,
    )
    if not text:
        return {"error": "empty_response"}

    # 健壮 JSON 解析（markdown/思考前缀/括号级），失败返回错误标记
    try:
        return dict(parse_json_response(text))
    except (ValueError, json.JSONDecodeError):
        return {"error": f"parse_failed: {text[:200]}"}
