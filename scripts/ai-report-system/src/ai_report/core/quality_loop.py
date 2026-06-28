"""
逐章质量闭环核心模块 — QualityLoop
======================================
三步法：搜索补充资料 → LLM 诊断 → 修正
1. Tavily/DuckDuckGo 搜索本章主题的补充资料
2. LLM 判断：跑题还是内容不足
3. 跑题 → 重写；内容不足 → 增量补充

搜索引擎：Tavily（主）+ DuckDuckGo（备），管线子进程可用。
不再依赖 HermesWebSearcher。

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .base import BaseComponent
from .workflow_state import WorkflowState
from ..config import get_env_config

logger = logging.getLogger(__name__)

# ── 搜索引擎（管线内，不依赖 hermes_tools） ─────────────────────


def _search_tavily(query: str) -> list[dict[str, str]] | None:
    """Tavily 搜索（主引擎）。"""
    api_key = get_env_config().tavily_api_key
    if not api_key:
        logger.debug("  Tavily: 无 API key")
        return None
    try:
        import httpx as _httpx
        resp = _httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": 6},
            timeout=15.0,
        )
        if resp.status_code != 200:
            logger.warning("  Tavily: HTTP %d", resp.status_code)
            return None
        results = resp.json().get("results", [])
        logger.info("  Tavily: %d results", len(results))
        return results
    except Exception as e:
        logger.warning("  Tavily failed: %s", e)
        return None


def _search_duckduckgo(query: str) -> list[dict[str, str]] | None:
    """DuckDuckGo 搜索（备用引擎）。"""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=6))
        logger.info("  DuckDuckGo: %d results", len(results))
        return results
    except Exception as e:
        logger.warning("  DuckDuckGo failed: %s", e)
        return None


def _fetch_page_text(url: str) -> str | None:
    """httpx + lxml 提取页面纯文本。"""
    try:
        import httpx as _httpx
        resp = _httpx.get(url, timeout=15.0, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        from lxml import html as _html
        tree = _html.fromstring(resp.content)
        for bad in tree.xpath("//script | //style | //nav | //footer | //header"):
            bad.getparent().remove(bad)
        text = tree.text_content()
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        return "\n".join(lines[:200])
    except Exception as e:
        logger.debug("  fetch_page_text failed: %s", e)
        return None


# ── 质量阈值 ────────────────────────────────────────────────

QUALITY_THRESHOLD: float = 0.6  # 低于此值触发诊断


# ── 诊断类型 ────────────────────────────────────────────────

DIAGNOSIS_OFF_TOPIC: str = "off_topic"
DIAGNOSIS_INSUFFICIENT: str = "insufficient"
DIAGNOSIS_GOOD: str = "good"


# ── 诊断结果 ────────────────────────────────────────────────

@dataclass
class ChapterDiagnosis:
    """单章节诊断结果。

    Attributes:
        title: 章节标题
        score_before: 诊断前的质量分
        diagnosis: off_topic / insufficient / good
        reason: 判断依据
        suggested_action: rewrite / enrich / skip
        search_data: 新搜索到的补充资料
    """
    title: str
    score_before: float
    diagnosis: str
    reason: str
    suggested_action: str
    search_data: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "title": self.title,
            "score_before": self.score_before,
            "diagnosis": self.diagnosis,
            "reason": self.reason,
            "suggested_action": self.suggested_action,
            "has_search_data": bool(self.search_data),
        }


@dataclass
class ChapterFixResult:
    """单章节修正结果。

    Attributes:
        title: 章节标题
        diagnosis: 诊断结果
        original_content: 原始内容
        fixed_content: 修正后的内容
        score_after: 修正后的质量分
        attempts: 修正尝试次数
    """
    title: str
    diagnosis: ChapterDiagnosis
    original_content: str
    fixed_content: str
    score_after: float
    attempts: int = 1

    @property
    def improved(self) -> bool:
        """修正后质量是否提升。"""
        return self.score_after > self.diagnosis.score_before

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "title": self.title,
            "diagnosis": self.diagnosis.to_dict(),
            "improved": self.improved,
            "score_before": self.diagnosis.score_before,
            "score_after": self.score_after,
            "attempts": self.attempts,
        }


# ── 诊断 Prompt 模板 ────────────────────────────────────────

DIAGNOSIS_PROMPT_TEMPLATE: str = """你是一个报告质量诊断专家。请分析以下章节内容，判断质量问题的类型。

章节标题：{title}
预估字数：{estimated_words}

写作角色背景：
{writing_role_info}

已写内容：
{content}

补充搜索资料：
{search_data}

请返回下列 JSON 格式的诊断结果（不要额外文字，只返回 JSON）：
{{
  "diagnosis": "off_topic" 或 "insufficient" 或 "good",
  "reason": "判断依据的简短说明",
  "suggested_action": "rewrite" 或 "enrich" 或 "skip"
}}

判断标准：
- off_topic: 内容偏离了标题「{title}」的主题方向，或不符合写作角色/语调要求
- insufficient: 主题正确，但内容单薄、缺乏具体数据和细节
- good: 内容充实、主题一致，且符合作者角色和语调，不需要修改

注意：重点对比标题与内容是否一致来判断是否跑题，同时参考写作角色背景判断风格是否一致。"""


# ── 逐章质量闭环主类 ────────────────────────────────────────

class QualityLoop:
    """逐章质量闭环 — 搜索→诊断→修正。

    用法:
        loop = QualityLoop()
        result = loop.run_chapter(state, "市场规模")
        if result and result.improved:
            # 使用修正后的内容
            print(result.fixed_content)
    """

    def __init__(self, threshold: float = QUALITY_THRESHOLD) -> None:
        """初始化质量闭环。

        Args:
            threshold: 质量阈值，低于此值触发诊断
        """
        self._threshold = threshold

    # ── 公开接口 ──────────────────────────────────────────

    def run_chapter(
        self,
        state: WorkflowState,
        chapter_title: str,
        anchor_hint: str = "",
    ) -> ChapterFixResult | None:
        """对单个章节执行逐章质量闭环。

        Args:
            state: WorkflowState
            chapter_title: 要检查的章节标题
            anchor_hint: 衔接上下文提示（来自 FullReportLoop）
        """
        ctx = state.chapter_contexts.get(chapter_title)
        if ctx is None or not ctx.generated_content:
            logger.warning("run_chapter: unknown or empty chapter '%s'", chapter_title)
            return None

        score_before = self._estimate_quality(ctx.generated_content, ctx.estimated_words)
        if score_before >= self._threshold:
            logger.debug(
                "  ✅ '%s' quality=%.2f >= %.2f, skip",
                chapter_title, score_before, self._threshold,
            )
            return None

        logger.info(
            "  🔍 '%s' quality=%.2f < %.2f, 开始诊断",
            chapter_title, score_before, self._threshold,
        )

        return self._diagnose_and_fix(state, chapter_title, ctx, score_before, anchor_hint)

    def _diagnose_and_fix(
        self,
        state: WorkflowState,
        chapter_title: str,
        ctx: Any,
        score_before: float,
        anchor_hint: str = "",
    ) -> ChapterFixResult | None:
        """诊断并修正单个章节（run_chapter 子步骤）。"""
        search_data = self._search_supplement(chapter_title, state.topic)
        if anchor_hint:
            search_data = f"{search_data}\n\n## 衔接上下文\n{anchor_hint}"

        writing_role_info = "(无)"
        if state.report_goal:
            wr = state.report_goal.get("writing_role", {})
            if wr:
                role = wr.get("role", "")
                tone = wr.get("tone", "")
                voice = wr.get("voice", "")
                parts_wr = []
                if role:
                    parts_wr.append(f"角色：{role}")
                if tone:
                    parts_wr.append(f"语调：{tone}")
                if voice:
                    parts_wr.append(f"叙述方式：{voice}")
                if parts_wr:
                    writing_role_info = "\n".join(parts_wr)

        diagnosis = self._diagnose(
            ctx.generated_content, chapter_title, ctx.estimated_words, search_data,
            writing_role_info=writing_role_info,
        )

        if diagnosis.diagnosis == DIAGNOSIS_GOOD:
            logger.info("  ✅ '%s' 诊断通过 (good)", chapter_title)
            return None

        fixed_content = self._fix(ctx.generated_content, diagnosis, chapter_title, state.topic)
        score_after = self._estimate_quality(fixed_content, ctx.estimated_words)
        state.set_chapter_result(chapter_title, fixed_content, "")

        logger.info(
            "  ✅ '%s' 修正完成: %.2f → %.2f (%s)",
            chapter_title, score_before, score_after, diagnosis.diagnosis,
        )
        return ChapterFixResult(
            title=chapter_title, diagnosis=diagnosis,
            original_content=ctx.generated_content,
            fixed_content=fixed_content, score_after=score_after,
        )

    # ── Step 1: 搜索补充资料 ──────────────────────────────

    @staticmethod
    def _search_supplement(
        chapter_title: str,
        topic: str,
    ) -> str:
        """搜索本章主题的补充资料。

        管线子进程可用（Tavily 主 → DuckDuckGo 备，httpx + lxml 取全文）。
        不再依赖 HermesWebSearcher。

        Args:
            chapter_title: 章节标题
            topic: 报告主题

        Returns:
            搜索到的资料文本（可能为空字符串）
        """
        query = f"{topic} {chapter_title}"
        import os as _os

        # 1. 搜索（Tavily 主 → DuckDuckGo 备）
        raw_results = _search_tavily(query) or _search_duckduckgo(query)
        if not raw_results:
            logger.info("  quality_search: 0 results for '%s'", query[:50])
            return ""

        logger.info("  quality_search: %d raw results for '%s'", len(raw_results), query[:50])

        # 2. LLM 选 URL
        items_text = "\n".join(
            f"[{i}] {r.get('title', '')}\n    URL: {r.get('url', '')}\n    摘要: {r.get('description', '')[:200]}"
            for i, r in enumerate(raw_results[:6])
        )
        from ..adapters.ai_client import call_llm as _call_llm_impl
        try:
            prompt = (
                "你是一个研究助手。需要从以下搜索结果中，选出最相关的 URL。\n\n"
                f"搜索主题: {query}\n\n"
                f"搜索结果:\n{items_text}\n\n"
                "请选出最多 2 个最相关的 URL，只输出 URL，每行一个。"
            )
            response = _call_llm_impl(prompt, max_tokens=200, temperature=0.1)
            selected_urls = []
            import re as _re
            for line in response.strip().split("\n"):
                line = line.strip().strip('"').strip("'")
                if line.startswith("[") and "](" in line:
                    m = _re.search(r'\]\(([^)]+)\)', line)
                    line = m.group(1) if m else line
                if line.startswith("http://") or line.startswith("https://"):
                    selected_urls.append(line)
            if not selected_urls:
                selected_urls = [r.get("url", "") for r in raw_results[:2]]
        except Exception:
            selected_urls = [r.get("url", "") for r in raw_results[:2]]

        # 3. 取全文（httpx + lxml）
        articles_text_parts: list[str] = []
        for url in selected_urls[:2]:
            content = _fetch_page_text(url)
            if content:
                articles_text_parts.append(f"--- {url[:50]} ---\n{content[:2000]}")

        result = "\n\n".join(articles_text_parts)
        if result:
            logger.info("  quality_search: %d articles extracted for '%s'", len(articles_text_parts), query[:50])
        return result

    # ── Step 2: LLM 诊断 ─────────────────────────────────

    @staticmethod
    def _diagnose(
        content: str,
        title: str,
        estimated_words: int,
        search_data: str,
        llm_caller: Any = None,
        writing_role_info: str = "(无)",
    ) -> ChapterDiagnosis:
        """LLM 诊断：跑题还是内容不足。

        Args:
            content: 章节内容
            title: 章节标题
            estimated_words: 预估字数
            search_data: 补充搜索资料
            llm_caller: LLM 调用函数（默认 call_llm，测试时可注入 mock）
            writing_role_info: 写作角色背景描述

        Returns:
            诊断结果
        """
        if llm_caller is None:
            from ..adapters.ai_client import call_llm as _call_impl
            llm_caller = _call_impl
        score_before = QualityLoop._estimate_quality(content, estimated_words)

        prompt = DIAGNOSIS_PROMPT_TEMPLATE.format(
            title=title,
            estimated_words=estimated_words,
            content=content[:2000],
            search_data=search_data[:1000] if search_data else "无",
            writing_role_info=writing_role_info[:500],
        )

        try:
            response = llm_caller(prompt, max_iterations=1, system_prompt=None)
            if response:
                result = QualityLoop._parse_diagnosis_response(response)
                if result:
                    return QualityLoop._build_diagnosis(
                        result, title, score_before, search_data,
                    )
        except Exception as e:
            logger.warning("LLM diagnose failed: %s, fallback to insufficient", e)

        return QualityLoop._fallback_diagnosis(title, score_before, search_data, "LLM诊断失败")

    @staticmethod
    def _build_diagnosis(
        result: dict[str, str],
        title: str,
        score_before: float,
        search_data: str,
    ) -> ChapterDiagnosis:
        """从解析结果构建诊断对象。"""
        diagnosis_type = result.get("diagnosis", DIAGNOSIS_INSUFFICIENT)
        reason = result.get("reason", "")
        action = result.get("suggested_action", "enrich")

        if diagnosis_type not in (DIAGNOSIS_OFF_TOPIC, DIAGNOSIS_INSUFFICIENT, DIAGNOSIS_GOOD):
            diagnosis_type = DIAGNOSIS_INSUFFICIENT
        if action not in ("rewrite", "enrich", "skip"):
            action = "rewrite" if diagnosis_type == DIAGNOSIS_OFF_TOPIC else "enrich"

        return ChapterDiagnosis(
            title=title, score_before=score_before,
            diagnosis=diagnosis_type, reason=reason,
            suggested_action=action, search_data=search_data,
        )

    @staticmethod
    def _fallback_diagnosis(
        title: str,
        score_before: float,
        search_data: str,
        reason: str = "",
    ) -> ChapterDiagnosis:
        """LLM 诊断失败时的降级诊断。"""
        return ChapterDiagnosis(
            title=title, score_before=score_before,
            diagnosis=DIAGNOSIS_INSUFFICIENT,
            reason=reason or "默认降级为内容不足",
            suggested_action="enrich", search_data=search_data,
        )

    @staticmethod
    def _parse_diagnosis_response(response: str) -> dict[str, str] | None:
        """解析 LLM 返回的诊断 JSON。

        Args:
            response: LLM 原始响应

        Returns:
            解析后的诊断字典，解析失败返回 None
        """
        # 尝试提取 JSON 块
        cleaned = response.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0].strip()

        try:
            result: dict[str, str] = json.loads(cleaned)
            return result
        except json.JSONDecodeError:
            pass

        # 尝试从纯文本中提取关键词
        lower = cleaned.lower()
        if "off_topic" in lower or "跑题" in lower:
            return {"diagnosis": DIAGNOSIS_OFF_TOPIC, "reason": cleaned[:200], "suggested_action": "rewrite"}
        if "good" in lower or "通过" in lower:
            return {"diagnosis": DIAGNOSIS_GOOD, "reason": cleaned[:200], "suggested_action": "skip"}
        if "insufficient" in lower or "不足" in lower:
            return {"diagnosis": DIAGNOSIS_INSUFFICIENT, "reason": cleaned[:200], "suggested_action": "enrich"}

        return None

    # ── Step 3: 修正 ──────────────────────────────────────

    @staticmethod
    def _fix(
        content: str,
        diagnosis: ChapterDiagnosis,
        chapter_title: str,
        topic: str,
        llm_caller: Any = None,
    ) -> str:
        """根据诊断结果修正章节内容。

        Args:
            content: 原始内容
            diagnosis: 诊断结果
            chapter_title: 章节标题
            topic: 报告主题
            llm_caller: LLM 调用函数（默认 call_llm，测试时可注入 mock）

        Returns:
            修正后的内容
        """
        if llm_caller is None:
            from ..adapters.ai_client import call_llm as _call_impl
            llm_caller = _call_impl
        if diagnosis.suggested_action == "skip":
            return content

        if diagnosis.suggested_action == "rewrite":
            prompt = (
                f"你是一位专业报告撰写专家。请根据以下诊断结果重写本章节。\n\n"
                f"报告主题：{topic}\n"
                f"章节标题：{chapter_title}\n"
                f"诊断问题：{diagnosis.reason}\n"
                f"补充资料：{diagnosis.search_data}\n\n"
                f"原始内容存在跑题问题，请重写为与标题完全相符的内容，"
                f"确保主题突出、内容充实。直接输出完整章节内容。"
            )
        else:
            prompt = (
                f"你是一位专业报告撰写专家。请根据补充资料丰富以下章节内容。\n\n"
                f"报告主题：{topic}\n"
                f"章节标题：{chapter_title}\n"
                f"诊断问题：{diagnosis.reason}\n"
                f"补充资料：{diagnosis.search_data}\n\n"
                f"原始内容主题正确但不够充实。请在保留原文结构的基础上，"
                f"融入补充资料中的具体信息，增加数据、细节和深度。"
                f"直接输出完整章节内容（包含原文已写部分）。\n\n"
                f"## 原始内容\n{content}"
            )

        try:
            fixed = llm_caller(prompt, max_iterations=1, system_prompt=None)
            if fixed and len(fixed.strip()) > 20:
                # 确保有标题
                if not fixed.startswith("#"):
                    fixed = f"## {chapter_title}\n\n{fixed}"
                return fixed
        except Exception as e:
            logger.warning("Fix LLM failed for '%s': %s", chapter_title, e)

        return content  # 修正失败，保留原文

    # ── 质量估算 ──────────────────────────────────────────

    @staticmethod
    def _estimate_quality(content: str, estimated_words: int) -> float:
        """快速估算内容质量（规则打分）。

        这里用轻量规则替代 LLM 评估，保持低成本。
        正式的质量由 content_generator._check_content_quality 完成。

        Args:
            content: 内容文本
            estimated_words: 预估字数

        Returns:
            质量分 (0.0 ~ 1.0)
        """
        if not content or len(content) < 30:
            return 0.0

        import re as _re

        score = 0.0

        # 长度 (40%)
        target = max(estimated_words, 100)
        len_ratio = len(content) / target
        score += min(len_ratio, 1.0) * 0.4

        # 结构 (30%)
        has_headings = bool(_re.search(r'^#{1,6}\s', content, _re.MULTILINE))
        has_lists = bool(_re.search(r'^[-*\d+\.]\s', content, _re.MULTILINE))
        has_breaks = content.count("\n\n") > 1
        struct = 0.0
        if has_headings:
            struct += 0.15
        if has_lists:
            struct += 0.1
        if has_breaks:
            struct += 0.05
        score += struct

        # 数据引用 (30%)
        if _re.search(r'\d+[%％倍]|\d+\.\d+', content):
            score += 0.3

        return min(score, 1.0)


# FullReportLoop 已移至 full_report_loop.py
