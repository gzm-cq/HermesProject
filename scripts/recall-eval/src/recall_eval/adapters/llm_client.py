"""LLM 调用适配器 — 封装 HTTP 调用、JSON 解析重试逻辑。"""

import json
import logging
import random
import re
import time
from typing import Any

import requests

from recall_eval.config import AppConfig, CONFIG

logger = logging.getLogger(__name__)


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
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0

        if self._key and self._url.startswith("http://"):
            logger.warning("LLM API key transmitted over HTTP (not HTTPS)")

    def _call(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,  # min 2048 for sensenova-6.7-flash-lite fallback JSON output
        json_mode: bool = False,
    ) -> str | None:
        """执行单次 LLM HTTP 调用，带指数退避 + jitter 重试。"""
        base_delay = 1.0
        for attempt in range(3):
            try:
                payload: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "temperature": 0.0,
                    "max_tokens": max_tokens,
                }
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}
                r = requests.post(
                    self._url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._key}"},
                    timeout=120,
                )
                data = r.json()
                if "choices" not in data or not data["choices"]:
                    logger.warning("LLM response missing 'choices': %s", str(data)[:300])
                    raise KeyError("choices")
                usage = data.get("usage", {})
                self.total_prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
                self.total_completion_tokens += int(usage.get("completion_tokens", 0) or 0)
                return str(data["choices"][0]["message"]["content"])
            except Exception as e:
                if attempt < 2:
                    delay = base_delay * (2**attempt) + random.uniform(0, 1)
                    logger.debug(
                        "LLM call attempt %d failed, retry in %.1fs: %s", attempt + 1, delay, e
                    )
                    time.sleep(delay)
                    continue
                logger.warning("LLM call failed after 3 attempts: %s", e)
                return None
        return None

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        """多路径 JSON 解析：正常 → strip/clean → 栈匹配。"""
        for fmt in [
            lambda x: x.strip().strip("`").replace("json\n", "").replace("\n```", ""),
            lambda x: x.strip(),
        ]:
            try:
                return dict(json.loads(fmt(raw)))
            except Exception:
                continue
        start = raw.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(raw)):
                c = raw[i]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return dict(json.loads(raw[start : i + 1]))
                        except Exception:
                            break
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

        raw = self._call(messages, max_tokens=2048, json_mode=True)  # min 2048 for sensenova-6.7-flash-lite fallback JSON output
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

        raw = self._call(messages, max_tokens=2048, json_mode=True)  # min 2048 for sensenova-6.7-flash-lite fallback JSON output
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

        raw = self._call(messages, max_tokens=2048, json_mode=True)  # min 2048 for sensenova-6.7-flash-lite fallback JSON output
        if raw is not None:
            result = self._parse_json(raw)
            if result is not None and "score" in result:
                try:
                    result["score"] = float(result["score"])
                    return result
                except (ValueError, TypeError):
                    pass
        return {"score": 0.0, "reason": "evaluation failed", "error": "LLM call / JSON parse failed"}
