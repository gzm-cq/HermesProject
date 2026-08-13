"""LLM 调用适配器 — 封装 HTTP 调用、JSON 解析重试逻辑。

所有 LLM 调用护栏（thinking 禁用 / JSON-only 系统约束 / max_tokens 钳制 / 健壮 JSON 解析 /
重试 / 429 退避 / 超时不再重试 / 限速 / 空内容重试）统一由 hermes_common.llm_guard 的
``guarded_chat_completion`` **单一实现** 提供；本文件仅做薄封装，并保留业务层 JSON 正则兜底。
"""

import importlib.util
import json
import logging
import os
import re
import sys
from typing import Any

import requests

from memory_cleanup.config import AppConfig, CONFIG

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
        self._post_fn = make_requests_post(self._url, self._key)
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0

        if self._key and self._url.startswith("http://"):
            logger.warning("LLM API key transmitted over HTTP (not HTTPS)")

    def _call(self, messages: list[dict[str, str]], max_tokens: int = 16384, json_mode: bool = False) -> str | None:
        """执行单次 LLM HTTP 调用（传输层统一由 common.guarded_chat_completion 处理重试/退避）。

        失败（限流/超时/网络/空内容耗尽）返回 None，由调用方降级处理。
        """
        try:
            resp = guarded_chat_completion(
                self._post_fn,
                model=self._model,
                messages=messages,
                temperature=0.05,
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

        # 部分 API 在 HTTP 200 时仍于顶层携带 error 字段，按失败处理
        if isinstance(resp, dict) and resp.get("error"):
            logger.warning("LLM response contains error field: %s", str(resp["error"])[:300])
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
        """JSON 解析：先走公共健壮解析（markdown/思考前缀/括号级），失败再用领域正则兜底。"""
        try:
            return parse_json_response(raw)
        except (ValueError, json.JSONDecodeError):
            pass
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

        raw = self._call(messages, max_tokens=16384, json_mode=True)
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

        raw = self._call(messages, max_tokens=16384, json_mode=True)  # min 16384 for sensenova-6.8-flash-lite fallback JSON output
        if raw is not None:
            result = self._parse_json(raw)
            if result is not None:
                return result
        return {"verdict": "keep", "note": "LLM call / JSON parse failed after 3 attempts"}
