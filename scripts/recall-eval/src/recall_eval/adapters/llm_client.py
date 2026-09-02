"""LLM 调用适配器 — 封装 HTTP 调用、JSON 解析重试逻辑。

所有 LLM 调用护栏（response_format=json_object / JSON-only 系统约束 / max_tokens 钳制 / 健壮 JSON 解析 /
重试 / 429 退避 / 超时不再重试 / 限速 / 空内容重试）统一由 hermes_common.llm_guard 的
``guarded_chat_completion`` **单一实现** 提供；本文件仅做薄封装。
"""

import importlib.util
import json
import logging
import os
import sys
from typing import Any

import requests

from recall_eval.config import AppConfig, CONFIG

logger = logging.getLogger(__name__)


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
RateLimiter = _lg.RateLimiter

# 全局限流：相邻两次 LLM 请求之间的最小间隔（秒）。
_DEFAULT_MIN_CALL_INTERVAL = float(os.environ.get("HERMES_SE_MIN_CALL_INTERVAL", "0.5"))
_rate_limiter = RateLimiter(_DEFAULT_MIN_CALL_INTERVAL)


def _truncate(text: str, max_len: int = 2000) -> str:
    """在合理边界截断文本，避免在关键信息中间截断。"""
    if len(text) <= max_len:
        return text
    suffix = "…（截断）"
    truncated = text[: max_len - len(suffix)]
    last_newline = truncated.rfind("\n")
    if last_newline > (max_len - len(suffix)) // 2:
        truncated = truncated[:last_newline]
    return truncated + suffix


FAITHFULNESS_SYSTEM_PROMPT = """你是严格的 RAG 忠实度评估专家。
判断给定的答案是否完全基于提供的上下文，没有引入上下文之外的信息。
只输出 JSON 格式，不要任何解释。"""

RELEVANCE_SYSTEM_PROMPT = """你是严格的 RAG 相关性评估专家。
判断提供的上下文与查询的相关程度。
只输出 JSON 格式，不要任何解释。"""

COVERAGE_SYSTEM_PROMPT = """你是严格的 RAG 覆盖率评估专家。
判断提供的上下文在多大程度上覆盖了查询中的所有要点。
只输出 JSON 格式，不要任何解释。"""


class LLMClient:
    """LiteLLM HTTP API 客户端，用于 RAG 评估。

    提供三个公共方法：
    - evaluate_faithfulness(): 忠实度评估
    - evaluate_relevance(): 相关性评估
    - evaluate_coverage(): 覆盖率评估

    内部封装 3 次 HTTP 重试和 JSON 解析重试。
    """

    def __init__(self, config: AppConfig = CONFIG) -> None:
        self._url = config.eval_api_url
        self._key = config.eval_api_key
        self._model = config.eval_model
        self._post_fn = make_requests_post(self._url, self._key)
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0

        if self._key and self._url.startswith("http://"):
            logger.warning("LLM API key transmitted over HTTP (not HTTPS)")

    def _call(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 16384,  # min 16384 for sensenova-6.8-flash-lite fallback JSON output
        json_mode: bool = False,
    ) -> str | None:
        """执行单次 LLM HTTP 调用（传输层统一由 common.guarded_chat_completion 处理重试/退避）。

        失败（限流/超时/网络/空内容耗尽）返回 None，由调用方降级处理。
        """
        try:
            resp = guarded_chat_completion(
                self._post_fn,
                model=self._model,
                messages=messages,
                temperature=0.0,
                top_p=0.1,
                max_tokens=max_tokens,
                json_mode=json_mode,
                timeout=120,
                max_retries=3,
                min_interval=_DEFAULT_MIN_CALL_INTERVAL,
                max_tokens_floor=16384,
                max_tokens_cap=None,
                rate_limiter=_rate_limiter,
            )
        except ConnectionError as e:
            logger.warning("LLM call failed after retries: %s", e)
            return None

        # 收集 token 使用量
        usage = resp.get("usage", {}) if isinstance(resp, dict) else {}
        self.total_prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.total_completion_tokens += int(usage.get("completion_tokens", 0) or 0)

        # 提取 content（统一护栏：content 空时兜底 reasoning / reasoning_content）
        try:
            return str(extract_content(resp["choices"][0]["message"]))
        except (ValueError, KeyError) as e:
            logger.warning("LLM response 解析 content 失败: %s", e)
            return None

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        """健壮 JSON 解析：走公共解析（markdown/思考前缀/括号级），失败返回 None。"""
        try:
            return parse_json_response(raw)
        except (ValueError, json.JSONDecodeError):
            return None

    def evaluate_faithfulness(self, query: str, context: str, answer: str) -> dict[str, Any]:
        """评估忠实度：答案是否完全基于上下文，无幻觉。

        返回格式: {"score": 0.0~1.0, "reason": "...", "claims": [...]}
        失败时返回 {"score": 0.0, "reason": "evaluation failed", "error": "..."}
        """
        user_prompt = f"""请评估以下 RAG 回答的忠实度（Faithfulness）。

**查询**: {_truncate(query)}

**上下文**:
{_truncate(context)}

**回答**:
{_truncate(answer)}

**评估标准**:
忠实度衡量回答中的每一个陈述是否都能在上下文中找到依据。
- score 1.0: 回答完全基于上下文，没有任何幻觉或额外信息
- score 0.7: 大部分内容基于上下文，仅有少量无关或无法验证的表述
- score 0.4: 约一半内容有上下文支持，另一半是推测或编造
- score 0.0: 回答完全是幻觉，与上下文无关或矛盾

**输出 JSON 格式**:
{{
  "score": 0.0~1.0之间的浮点数,
  "reason": "简短的评分理由",
  "supported_claims": ["有上下文支持的陈述列表"],
  "unsupported_claims": ["无上下文支持的陈述列表"]
}}"""

        messages = [
            {"role": "system", "content": FAITHFULNESS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        raw = self._call(messages, max_tokens=16384, json_mode=True)  # min 16384 for sensenova-6.8-flash-lite fallback JSON output
        if raw is not None:
            result = self._parse_json(raw)
            if result is not None and "score" in result:
                try:
                    result["score"] = float(result["score"])
                    return result
                except (ValueError, TypeError):
                    pass
        return {"score": 0.0, "reason": "evaluation failed", "error": "LLM call / JSON parse failed"}

    def evaluate_relevance(self, query: str, context: str) -> dict[str, Any]:
        """评估相关性：上下文与查询的相关程度。

        返回格式: {"score": 0.0~1.0, "reason": "...", "relevant_chunks": [...]}
        失败时返回 {"score": 0.0, "reason": "evaluation failed", "error": "..."}
        """
        user_prompt = f"""请评估以下上下文与查询的相关性（Relevance）。

**查询**: {_truncate(query)}

**上下文**:
{_truncate(context)}

**评估标准**:
相关性衡量上下文中的内容是否与查询主题相关，以及相关信息的多少。
- score 1.0: 上下文高度相关，包含大量与查询直接相关的信息
- score 0.7: 上下文大部分相关，有一些无关内容
- score 0.4: 上下文约一半相关，或只有间接相关的内容
- score 0.0: 上下文完全不相关

**输出 JSON 格式**:
{{
  "score": 0.0~1.0之间的浮点数,
  "reason": "简短的评分理由",
  "relevant_topics": ["相关的主题列表"],
  "irrelevant_topics": ["不相关的主题列表"]
}}"""

        messages = [
            {"role": "system", "content": RELEVANCE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        raw = self._call(messages, max_tokens=16384, json_mode=True)  # min 16384 for sensenova-6.8-flash-lite fallback JSON output
        if raw is not None:
            result = self._parse_json(raw)
            if result is not None and "score" in result:
                try:
                    result["score"] = float(result["score"])
                    return result
                except (ValueError, TypeError):
                    pass
        return {"score": 0.0, "reason": "evaluation failed", "error": "LLM call / JSON parse failed"}

    def evaluate_coverage(self, query: str, context: str) -> dict[str, Any]:
        """评估覆盖率：上下文覆盖查询要点的比例。

        返回格式: {"score": 0.0~1.0, "reason": "...", "covered_points": [...], "missing_points": [...]}
        失败时返回 {"score": 0.0, "reason": "evaluation failed", "error": "..."}
        """
        user_prompt = f"""请评估以下上下文对查询要点的覆盖率（Coverage）。

**查询**: {_truncate(query)}

**上下文**:
{_truncate(context)}

**评估标准**:
覆盖率衡量查询中需要了解的关键信息点，有多少能在上下文中找到答案。
- score 1.0: 上下文完全覆盖查询的所有要点
- score 0.7: 上下文覆盖了大部分要点，缺少少量次要信息
- score 0.4: 上下文只覆盖了约一半的要点
- score 0.0: 上下文完全没有覆盖查询的任何要点

**输出 JSON 格式**:
{{
  "score": 0.0~1.0之间的浮点数,
  "reason": "简短的评分理由",
  "query_points": ["从查询中提取的关键信息点"],
  "covered_points": ["上下文中覆盖的信息点"],
  "missing_points": ["上下文中缺失的信息点"]
}}"""

        messages = [
            {"role": "system", "content": COVERAGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        raw = self._call(messages, max_tokens=16384, json_mode=True)  # min 16384 for sensenova-6.8-flash-lite fallback JSON output
        if raw is not None:
            result = self._parse_json(raw)
            if result is not None and "score" in result:
                try:
                    result["score"] = float(result["score"])
                    return result
                except (ValueError, TypeError):
                    pass
        return {"score": 0.0, "reason": "evaluation failed", "error": "LLM call / JSON parse failed"}
