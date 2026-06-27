"""
串行写作上下文管理器 — WorkflowState
========================================
职责：
- 管理串行写作的全局状态（整体目标、上章摘要、搜索资料池）
- 为每章构建三要素 prompt：整体目标 + 上章概要 + 本章搜索资料
- 自动识别业务章节，触发 copywriting 补充

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── 章节类型定义 ────────────────────────────────────────────

SECTION_TYPE_REGULAR: str = "regular"
SECTION_TYPE_BUSINESS: str = "business"
SECTION_TYPE_SUMMARY: str = "summary"

# 章节标题 → 业务场景映射
BUSINESS_SECTION_KEYWORDS: dict[str, str] = {
    "成本": "cost_reduction",
    "效益": "cost_reduction",
    "收入": "revenue_growth",
    "增长": "revenue_growth",
    "效率": "efficiency",
    "风险": "risk_management",
    "竞争": "competitive_advantage",
    "客户": "customer_experience",
    "创新": "innovation",
    "扩展": "scalability",
    "roi": "revenue_growth",
    "投资回报": "revenue_growth",
}


# ── 章节上下文 ──────────────────────────────────────────────

@dataclass
class ChapterContext:
    """单个章节的完整上下文。

    Attributes:
        title: 章节标题
        section_type: 章节类型 (regular/business/summary)
        search_data: 搜索阶段预填的资料
        generated_content: 写作阶段生成的文字内容
        summary: 自动提取的章节摘要（供下一章引用）
        estimated_words: 预估字数
    """
    title: str
    section_type: str = SECTION_TYPE_REGULAR
    search_data: str = ""
    generated_content: str = ""
    summary: str = ""
    estimated_words: int = 500

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "title": self.title,
            "section_type": self.section_type,
            "search_data_length": len(self.search_data),
            "content_length": len(self.generated_content),
            "has_summary": bool(self.summary),
            "estimated_words": self.estimated_words,
        }


# ── 串行写作状态 ────────────────────────────────────────────

@dataclass
class WorkflowState:
    """串行写作的全局上下文。

    用法:
        state = WorkflowState(topic="AI市场调研", report_type="market")
        state.set_main_context("分析市场规模和发展趋势")

        # 搜索阶段：并行填入各章节资料
        state.set_chapter_search("市场规模", "搜索到的资料...")

        # 写作阶段：逐章获取 prompt → LLM → 填入结果
        prompt = state.get_chapter_prompt("市场规模")
        # prompt 包含: 整体目标 + (上章摘要) + 本章搜索资料

        content = call_llm(prompt)
        summary = extract_summary(content)
        state.set_chapter_result("市场规模", content, summary)
    """
    topic: str
    report_type: str
    main_context: str = ""                          # LLM 生成的整体目标
    chapter_contexts: dict[str, ChapterContext] = field(default_factory=dict)
    _chapter_order: list[str] = field(default_factory=list)  # 章节写入顺序
    _prev_summary: str = ""                         # 上章摘要（串行时更新）
    report_goal: dict[str, Any] | None = None       # 已确认的报告目标（供质量闭环使用）

    # ── 初始化 ──────────────────────────────────────────

    def init_from_plan(
        self,
        sections: list[Any],
        main_context: str = "",
        report_goal: dict[str, Any] | None = None,
    ) -> None:
        """从 ReportPlan.sections 初始化。

        Args:
            sections: ReportPlan.sections 列表
            main_context: 整体目标描述
            report_goal: 已确认的报告目标（含 writing_role）
        """
        self.main_context = main_context or f"撰写关于「{self.topic}」的{self.report_type}报告"
        self.chapter_contexts = {}
        self._chapter_order = []
        self._prev_summary = ""
        self.report_goal = report_goal

        for sec in sections:
            title = sec.title
            section_type = self._detect_section_type(title, getattr(sec, "section_type", "body"))
            ctx = ChapterContext(
                title=title,
                section_type=section_type,
                estimated_words=getattr(sec, "estimated_words", 500),
            )
            self.chapter_contexts[title] = ctx
            self._chapter_order.append(title)

        logger.info(
            "WorkflowState: %d sections (%s) | main_context=%s",
            len(sections), self.report_type, self.main_context[:30],
        )

    # ── 搜索阶段 ──────────────────────────────────────────

    def set_chapter_search(self, title: str, data: str) -> None:
        """设置某个章节的搜索资料（搜索阶段调用）。

        Args:
            title: 章节标题
            data: 搜索到的资料文本
        """
        ctx = self.chapter_contexts.get(title)
        if ctx is None:
            logger.warning("Unknown chapter: %s", title)
            return
        ctx.search_data = data
        logger.debug("Search data set for '%s': %d chars", title, len(data))

    def has_search_data(self, title: str) -> bool:
        """检查章节是否有搜索资料。"""
        ctx = self.chapter_contexts.get(title)
        return bool(ctx and ctx.search_data)

    # ── 写作阶段 ──────────────────────────────────────────

    def get_chapter_prompt(self, title: str) -> str:
        """构建三要素 prompt：整体目标 + 上章概要 + 本章搜索资料。

        Args:
            title: 章节标题

        Returns:
            格式化的 prompt 字符串

        Raises:
            ValueError: 未知章节
        """
        ctx = self.chapter_contexts.get(title)
        if ctx is None:
            raise ValueError(f"Unknown chapter: {title}")

        parts: list[str] = []

        # ① 整体目标
        parts.append(f"## 整体目标")
        parts.append(self.main_context)
        parts.append("")

        # ② 上章概要（如果有）
        if self._prev_summary:
            parts.append(f"## 上一章概要")
            parts.append(f"上一章节讨论了：{self._prev_summary}")
            parts.append(f"请在此基础上自然衔接，保持叙事连贯。")
            parts.append("")

        # ③ 本章任务
        parts.append(f"## 本章任务")
        parts.append(f"章节标题：{title}")
        parts.append(f"章节类型：{ctx.section_type}")
        parts.append(f"预估字数：{ctx.estimated_words} 字")
        parts.append("")

        # ④ 本章搜索资料
        if ctx.search_data:
            parts.append(f"## 搜索资料")
            parts.append(f"以下是与本章相关的网络搜索资料：")
            parts.append(ctx.search_data[:3000])  # 限制长度避免超 token
            parts.append("")

        # ⑤ 业务章节特殊指引
        if ctx.section_type == SECTION_TYPE_BUSINESS:
            parts.append(f"## 业务视角要求")
            parts.append(
                "本章属于业务分析章节。请从业务价值角度撰写，"
                "关注成本、收益、效率、竞争等实际业务影响。"
                f"{self._get_business_guidance(title)}"
            )
            parts.append("")

        # ⑥ 写作要求
        parts.append(f"## 写作要求")
        parts.append("1. 基于搜索资料中的具体信息，不要编造")
        parts.append("2. 内容充实，段落之间有逻辑衔接")
        parts.append("3. 使用markdown格式的标题和列表")
        parts.append("4. 避免空洞的套话和模板化表述")
        parts.append("5. 直接输出内容，不要额外解释")
        parts.append("6. 当前阶段仅输出文字，不含图表")

        return "\n".join(parts)

    def set_chapter_result(
        self,
        title: str,
        content: str,
        summary: str = "",
    ) -> None:
        """设置章节生成结果，并更新上一章摘要。

        Args:
            title: 章节标题
            content: 生成的文字内容
            summary: 章节摘要（供下一章引用），为空则自动提取
        """
        ctx = self.chapter_contexts.get(title)
        if ctx is None:
            logger.warning("set_chapter_result: unknown chapter '%s'", title)
            return

        ctx.generated_content = content

        # 自动提取摘要
        if not summary:
            summary = self._auto_extract_summary(content)
        ctx.summary = summary

        # 更新上一章摘要（供下一章使用）
        self._prev_summary = summary

        logger.info(
            "Chapter '%s' done: %d chars, summary=%s",
            title, len(content), summary[:40],
        )

    # ── 章节类型检测 ──────────────────────────────────────

    @staticmethod
    def _detect_section_type(title: str, original_type: str) -> str:
        """检测章节类型。

        Args:
            title: 章节标题
            original_type: ReportPlan 中的原始 section_type

        Returns:
            标准化的章节类型 (regular/business/summary)
        """
        # 如果原始类型已经是业务类
        if original_type in ("business", "analysis", "recommendation"):
            return SECTION_TYPE_BUSINESS

        # 根据标题关键词检测
        title_lower = title.lower()
        for keyword in BUSINESS_SECTION_KEYWORDS:
            if keyword in title_lower:
                return SECTION_TYPE_BUSINESS

        # 总结类
        if any(kw in title_lower for kw in ["总结", "结论", "展望", "summary", "conclusion"]):
            return SECTION_TYPE_SUMMARY

        return SECTION_TYPE_REGULAR

    @staticmethod
    def _get_business_guidance(title: str) -> str:
        """获取业务章节的特定写作指导。

        Args:
            title: 章节标题

        Returns:
            业务写作指导文本
        """
        title_lower = title.lower()
        for keyword, scenario in BUSINESS_SECTION_KEYWORDS.items():
            if keyword in title_lower:
                guides = {
                    "cost_reduction": (
                        "重点分析成本节约的具体方式和量化效果，"
                        "如资源利用率提升、运维成本降低等。"
                    ),
                    "revenue_growth": (
                        "重点阐述收入增长路径和商业价值，"
                        "如新市场拓展、变现能力提升等。"
                    ),
                    "efficiency": (
                        "重点说明效率提升的具体倍数和流程优化，"
                        "如自动化率提升、决策周期缩短等。"
                    ),
                    "risk_management": "重点关注风险降低指标和合规保障。",
                    "competitive_advantage": (
                        "重点对比竞品，突出差异化优势和壁垒。"
                    ),
                    "customer_experience": "重点关注用户满意度和体验提升指标。",
                    "innovation": "重点关注技术/模式创新的差异化价值。",
                    "scalability": "重点关注架构弹性和业务增长支撑能力。",
                }
                return guides.get(scenario, "")
        return ""

    # ── 摘要提取 ──────────────────────────────────────────

    @staticmethod
    def _auto_extract_summary(content: str) -> str:
        """从生成内容中自动提取摘要。

        Args:
            content: 章节内容

        Returns:
            摘要文本（约 100 字）
        """
        if not content:
            return ""

        # 取第一段非空文本
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        for p in paragraphs:
            # 跳过标题行
            if p.startswith("#"):
                continue
            # 取前 100 字作为摘要
            clean = p.replace("\n", " ").strip()
            if clean:
                return clean[:100]

        # 没有找到合适的段落，取内容前 100 字
        return content[:100].replace("\n", " ").strip()

    # ── 状态查询 ──────────────────────────────────────────

    @property
    def current_section(self) -> str | None:
        """当前正在处理的章节标题。"""
        for title in self._chapter_order:
            ctx = self.chapter_contexts[title]
            if not ctx.generated_content:
                return title
        return None

    @property
    def completed_sections(self) -> list[str]:
        """已完成写作的章节列表。"""
        return [
            title for title in self._chapter_order
            if self.chapter_contexts[title].generated_content
        ]

    @property
    def pending_sections(self) -> list[str]:
        """待写作的章节列表。"""
        return [
            title for title in self._chapter_order
            if not self.chapter_contexts[title].generated_content
        ]

    @property
    def is_complete(self) -> bool:
        """所有章节是否已完成。"""
        return len(self.pending_sections) == 0

    def get_full_text(self) -> str:
        """按章节顺序拼接完整报告文字。"""
        parts: list[str] = []
        for title in self._chapter_order:
            ctx = self.chapter_contexts.get(title)
            if ctx and ctx.generated_content:
                parts.append(ctx.generated_content)
        return "\n\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "topic": self.topic,
            "report_type": self.report_type,
            "main_context": self.main_context[:50],
            "total_sections": len(self._chapter_order),
            "completed": len(self.completed_sections),
            "pending": len(self.pending_sections),
            "has_search_data": sum(1 for t in self._chapter_order if self.has_search_data(t)),
            "chapters": [self.chapter_contexts[t].to_dict() for t in self._chapter_order],
        }
