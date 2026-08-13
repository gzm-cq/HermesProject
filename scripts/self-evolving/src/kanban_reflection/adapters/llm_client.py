"""LLM 调用适配器 — 封装 API 调用（护栏统一由 hermes_common.llm_guard 提供）。

通过 urllib.request（stdlib）调用兼容 OpenAI API 格式的 LLM 服务。

所有护栏（thinking 禁用 / JSON-only 系统约束 / max_tokens 钳制 / 健壮 JSON 解析 /
超时不再重试 / 限速 / 空内容重试）统一由公共模块 ``hermes_common.llm_guard`` 的
``guarded_chat_completion`` **单一实现**提供，本文件仅做薄封装。与 self_evolving
客户端共用同一套护栏（均经 _load_common_llm_guard 加载 common），消除漂移。

max_tokens 钳制保留本项目原值 [16384, 16384]（上限防慢模型生成过长超时，
下限防推理模型 reasoning 吃光致 content 空）。
"""

import importlib.util
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _load_common_llm_guard():
    """定位并加载 hermes_common.llm_guard（唯一事实来源）。

    查找顺序：① 开发态仓库 libs/hermes_common/hermes_common/llm_guard.py；
              ② 生产部署 /root/.hermes/lib/hermes_common/llm_guard.py。
    若均失败，抛出明确错误，提示先 `deploy.sh deploy hermes-common`。
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
make_urllib_post = _lg.make_urllib_post
extract_content = _lg.extract_content
parse_json_response = _lg.parse_json_response
RateLimiter = _lg.RateLimiter

DEFAULT_MAX_RETRIES = 3

# 全局限流：相邻两次 LLM 请求之间的最小间隔（秒）。
_DEFAULT_MIN_CALL_INTERVAL = float(os.environ.get("HERMES_SE_MIN_CALL_INTERVAL", "0.5"))
_rate_limiter = RateLimiter(_DEFAULT_MIN_CALL_INTERVAL)

# 本项目 max_tokens 钳制区间 [16384, 16384]
_MAX_TOKENS_FLOOR = 16384
_MAX_TOKENS_CAP = 16384


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
        # 传输回调统一由 common 的 urllib 实现提供
        self._post_fn = make_urllib_post(self._api_url, self._api_key)

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = _MAX_TOKENS_FLOOR,
        response_format: dict | None = None,
    ) -> dict[str, Any]:
        """调用 LLM chat completion API（带全护栏，委托 common.guarded_chat_completion）。"""
        json_mode = bool(response_format and response_format.get("type") == "json_object")
        return guarded_chat_completion(
            self._post_fn,
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            timeout=self._timeout,
            max_retries=self._max_retries,
            min_interval=_DEFAULT_MIN_CALL_INTERVAL,
            max_tokens_floor=_MAX_TOKENS_FLOOR,
            max_tokens_cap=_MAX_TOKENS_CAP,
            rate_limiter=_rate_limiter,
        )

    def extract_content(self, response: dict[str, Any]) -> str:
        """从 API 响应中提取文本内容（content 空时兜底 reasoning）。"""
        try:
            return extract_content(response["choices"][0]["message"])
        except (KeyError, IndexError, TypeError):
            raise ValueError("响应格式异常: 缺少 choices 字段")

    def parse_json_response(self, text: str) -> dict[str, Any]:
        """从 LLM 响应文本中解析 JSON，兼容 markdown 包裹 / 思考前缀 / 尾部多余文本。"""
        try:
            return parse_json_response(text)
        except ValueError as e:
            raise json.JSONDecodeError(str(e), text, 0)
