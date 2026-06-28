"""LLM 调用适配器 — 封装 HTTP 调用、JSON 解析重试逻辑。"""

import json
import logging
import random
import re
import time
from typing import Any

import requests

from memory_cleanup.config import AppConfig, CONFIG

logger = logging.getLogger(__name__)


def _truncate(text: str, max_len: int = 400) -> str:
    """在合理边界截断文本，避免在关键信息中间截断。"""
    if len(text) <= max_len:
        return text
    suffix = "…（截断）"
    truncated = text[:max_len - len(suffix)]
    last_newline = truncated.rfind("\n")
    if last_newline > (max_len - len(suffix)) // 2:
        truncated = truncated[:last_newline]
    return truncated + suffix


class LLMClient:
    """LiteLLM HTTP API 客户端。

    提供两个公共方法：
    - classify_batch(): Phase 1 分类调用
    - verify_one(): Phase 2 验证调用

    内部封装 3 次 HTTP 重试和 JSON 三路径解析。
    """

    def __init__(self, config: AppConfig = CONFIG) -> None:
        self._url = config.llm_url
        self._key = config.llm_key
        self._model = config.llm_model
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0

        if self._key and self._url.startswith("http://"):
            logger.warning("LLM API key transmitted over HTTP (not HTTPS)")

    def _call(self, messages: list[dict[str, str]], max_tokens: int = 3000, json_mode: bool = False) -> str | None:
        """
        执行单次 LLM HTTP 调用，带指数退避 + jitter 重试。
        失败返回 None，最多重试 3 次。
        """
        base_delay = 1.0
        for attempt in range(3):
            try:
                payload: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "temperature": 0.05,
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
                # 校验 choices 键存在（LLM 返回 error/thinking 模式时可能缺失）
                if "choices" not in data or not data["choices"]:
                    logger.warning("LLM response missing 'choices': %s", str(data)[:300])
                    raise KeyError("choices")
                # 收集 token 使用量
                usage = data.get("usage", {})
                self.total_prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
                self.total_completion_tokens += int(usage.get("completion_tokens", 0) or 0)
                return str(data["choices"][0]["message"]["content"])
            except Exception as e:
                if attempt < 2:
                    delay = base_delay * (2**attempt) + random.uniform(0, 1)
                    logger.debug("LLM call attempt %d failed, retry in %.1fs: %s", attempt + 1, delay, e)
                    time.sleep(delay)
                    continue
                logger.warning("LLM call failed after 3 attempts: %s", e)
                return None
        return None

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        """四路径 JSON 解析：正常 → strip/clean → 栈匹配 → 正则提取回退。"""
        for fmt in [
            lambda x: x.strip().strip("`").replace("json\n", "").replace("\n```", ""),
            lambda x: x.strip(),
        ]:
            try:
                return dict(json.loads(fmt(raw)))
            except Exception:
                continue
        # 栈匹配回退：找到第一个 { 对应的 }，支持嵌套
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
        # 正则提取回退：从 LLM 输出中逐数组提取
        return LLMClient._regex_fallback_parse(raw)

    @staticmethod
    def _regex_fallback_parse(raw: str) -> dict[str, Any] | None:
        """正则提取回退 — 当 JSON 解析全部失败时，尝试从文本中提取分类结果。

        匹配模式如 "remove": [{"index": 3, "原因": "..."}] 中的各个条目。
        """
        result: dict[str, Any] = {"merge": [], "remove": [], "compress": []}
        found_any = False

        # 提取 remove 条目: {"index": N, "原因": "..."}
        for m in re.finditer(
            r'\{\s*"index"\s*:\s*(\d+)\s*,\s*"原因"\s*:\s*"([^"]*)"\s*\}', raw
        ):
            result["remove"].append({"index": int(m.group(1)), "原因": m.group(2)})
            found_any = True

        # 提取 compress 条目: {"index": N, "精简为": "..."}
        for m in re.finditer(
            r'\{\s*"index"\s*:\s*(\d+)\s*,\s*"精简为"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}', raw
        ):
            result["compress"].append({"index": int(m.group(1)), "精简为": m.group(2)})
            found_any = True

        # 提取 merge 条目: {"indices": [N, M, ...], "合并为": "..."}
        for m in re.finditer(
            r'\{\s*"indices"\s*:\s*\[([\d\s,]+)\]\s*,\s*"合并为"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}', raw
        ):
            indices = [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
            result["merge"].append({"indices": indices, "合并为": m.group(2)})
            found_any = True

        return result if found_any else None

    def classify_batch(
        self, batch_entries: list[str], batch_offset: int, source: str, system_prompt: str
    ) -> dict[str, Any]:
        """对一批条目调用 LLM 分类，返回 merge/remove/compress 三数组。

        失败时返回 {"error": "..."} 。
        """
        lines = [f"[{batch_offset + i}] {_truncate(text)}" for i, text in enumerate(batch_entries)]
        user_prompt = f"分类以下 {len(batch_entries)} 条：\n" + "\n\n".join(lines)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        raw = self._call(messages, max_tokens=3000, json_mode=True)
        if raw is not None:
            result = self._parse_json(raw)
            if result is not None:
                return result
        return {"error": "LLM call / JSON parse failed after 3 attempts"}

    def verify_one(
        self, idx: int, text: str, reason: str, source: str, session_snippet: str | None = None
    ) -> dict[str, Any]:
        """对单条 remove 候选进行 LLM 验证，返回 verdict/note/corrected_text。

        当 session_snippet 为 None 或空时，仅基于条目和理由判断。
        失败时保守返回 {"verdict": "keep", "note": "..."} 。
        """
        has_session = bool(session_snippet and session_snippet != "无相关会话")

        if has_session:
            judge_prompt = f"""以下是从 {source} 移除候选条目及其对应的原始对话上下文。
判断该条目的提炼是否正确（相比原始对话，MEMORY.md 的版本有没有事实性偏差或错误）。

条目原文: {_truncate(text)}
原始对话: {_truncate(session_snippet)}
标记为移除的原因: {reason}

输出 JSON（仅此格式）：
- 如果提炼正确（无事实性错误） → {{"verdict": "correct", "note": ""}}
- 如果提炼有事实性偏差（时间/数量/结论/名称等硬事实错误） → {{"verdict": "corrected", "corrected_text": "仅修正事实性错误的条目内容"}}
- 如果不应移除 → {{"verdict": "keep", "note": "保留原因"}}

重要规则：
1. corrected_text 必须遵循最小编辑原则：只修正具体的事实性错误，禁止重述、扩写、润色或改变句式
2. 如果条目只是表述不够完美但无事实错误，verdict 应为 "correct" 而非 "corrected"
3. 禁止将原始对话中的不同话题强行关联到条目中"""
        else:
            judge_prompt = f"""以下是从 {source} 移除候选条目，无原始对话上下文。
仅基于条目内容和移除理由，判断该移除理由是否成立。

条目原文: {_truncate(text)}
标记为移除的原因: {reason}

输出 JSON（仅此格式）：
- 如果移除理由合理（条目确实是业务数据/过程记录/过时信息/清理自身记录等） → {{"verdict": "correct", "note": ""}}
- 如果条目内容包含不应删除的工具特性/经验教训/架构约定/用户偏好 → {{"verdict": "keep", "note": "保留原因"}}
- 如果无法确定 → {{"verdict": "keep", "note": "无法验证，保守保留"}}

重要规则：无原始对话时采取保守策略——只有理由非常明确（如空条目、清理流程记录）才判 correct，不确定时一律 keep。"""

        messages = [
            {"role": "system", "content": "你是精确的判断者，仅输出 JSON。"},
            {"role": "user", "content": judge_prompt},
        ]

        raw = self._call(messages, max_tokens=800, json_mode=True)
        if raw is not None:
            result = self._parse_json(raw)
            if result is not None:
                return result
        return {"verdict": "keep", "note": "LLM call / JSON parse failed after 3 attempts"}
