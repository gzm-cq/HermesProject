"""
业务文案撰写器 — 基于 copywriting skill 的报告业务内容增强
===========================================================
职责：
- 为报告中的「业务优化建议」「项目业务价值」等内容提供写作辅助
- 结合特定业务场景补充行文
- 不负责整篇报告写作，只负责业务视角的润色增强

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .web_search import DelegationTask, Delegator, MODE_HERMES

logger = logging.getLogger(__name__)


# ── 业务场景类型 ────────────────────────────────────────────

BUSINESS_SCENARIO_TYPES: dict[str, str] = {
    "cost_reduction": "成本优化 — 降低运营成本、提高资源利用率",
    "revenue_growth": "收入增长 — 拓展收入来源、提升转化率",
    "efficiency": "效率提升 — 优化流程、减少人工、加速决策",
    "risk_management": "风险管理 — 降低业务风险、合规保障",
    "competitive_advantage": "竞争优势 — 差异化能力、市场定位",
    "customer_experience": "客户体验 — 提升满意度、留存率",
    "innovation": "创新驱动 — 新技术应用、业务模式创新",
    "scalability": "可扩展性 — 支持业务增长、弹性和容量",
}


# ── 业务文案任务 ────────────────────────────────────────────

@dataclass
class BusinessWritingTask:
    """业务文案撰写任务。

    Attributes:
        scenario: 业务场景类型 (cost_reduction / revenue_growth / ...)
        section_title: 目标章节标题
        project_context: 项目背景描述
        key_findings: 关键发现或数据
        tone: 语气 (professional / enthusiastic / conservative)
        max_paragraphs: 最大段落数
    """
    scenario: str
    section_title: str
    project_context: str = ""
    key_findings: list[str] = field(default_factory=list)
    tone: str = "professional"
    max_paragraphs: int = 3


@dataclass
class BusinessWritingResult:
    """业务文案撰写结果。

    Attributes:
        section_title: 章节标题
        scenario: 使用的业务场景
        content: 生成的文案内容
        tone: 语气风格
    """
    section_title: str
    scenario: str
    content: str
    tone: str = "professional"


# ── 场景 Prompt 模板 ────────────────────────────────────────

SCENARIO_PROMPTS: dict[str, str] = {
    "cost_reduction": (
        "从成本优化角度分析，重点说明项目/方案如何降低运营成本、"
        "提高资源利用效率。使用具体数据支撑成本节约效果。"
    ),
    "revenue_growth": (
        "从收入增长角度阐述，说明项目/方案如何开辟新的收入来源、"
        "提升现有产品的变现能力。强调可量化的收入增长潜力。"
    ),
    "efficiency": (
        "从效率提升角度撰写，说明项目/方案如何优化现有流程、"
        "减少人工干预、加速业务决策。突出效率提升的具体倍数或百分比。"
    ),
    "risk_management": (
        "从风险管理角度分析，说明项目/方案如何降低业务风险、"
        "加强合规保障、提高系统可靠性。强调风险降低的具体指标。"
    ),
    "competitive_advantage": (
        "从竞争优势角度阐述，说明项目/方案带来的差异化能力、"
        "市场定位优势、技术壁垒。与行业基准或竞品进行对比。"
    ),
    "customer_experience": (
        "从客户体验角度撰写，说明项目/方案如何提升用户满意度、"
        "留存率、NPS等关键体验指标。使用具体案例或数据支撑。"
    ),
    "innovation": (
        "从创新驱动角度分析，说明项目/方案采用的新技术、新方法、"
        "新业务模式。强调创新带来的差异化价值和行业影响力。"
    ),
    "scalability": (
        "从可扩展性角度阐述，说明项目/方案如何支持业务持续增长、"
        "应对流量波动、实现弹性扩缩。强调架构弹性和容量规划。"
    ),
}

TONE_GUIDES: dict[str, str] = {
    "professional": "语气专业、客观，用数据说话，避免过度夸张",
    "enthusiastic": "语气积极、有说服力，强调价值和前景，但仍保持可信度",
    "conservative": "语气谨慎、保守，强调风险控制和稳健实施，适合金融/合规场景",
}


# ── 业务文案撰写器 ──────────────────────────────────────────

class BusinessWriter:
    """业务文案撰写器 — 为报告业务章节提供 copywriting 增强。

    用法:
        writer = BusinessWriter()
        task = BusinessWritingTask(
            scenario="cost_reduction",
            section_title="成本效益分析",
            key_findings=["降低运营成本30%", "ROI达150%"],
        )
        # 方案 A: 构建委托任务（由 Hermes 执行）
        hermes_task = writer.prepare(task)

        # 方案 B: 直接生成（纯模板 + 格式化）
        result = writer.generate(task)
    """

    def __init__(self) -> None:
        """初始化业务文案撰写器。"""
        self._delegator = Delegator()

    def prepare(self, task: BusinessWritingTask) -> DelegationTask:
        """准备 copywriting 委托任务（默认模式）。

        Args:
            task: 业务文案撰写任务

        Returns:
            委托任务定义（由 Hermes 通过 delegate_task 执行）
        """
        context_parts: list[str] = [f"项目背景: {task.project_context}"] if task.project_context else []
        if task.key_findings:
            context_parts.append("关键数据:")
            for kf in task.key_findings:
                context_parts.append(f"  - {kf}")

        scenario_desc = BUSINESS_SCENARIO_TYPES.get(task.scenario, task.scenario)
        scenario_prompt = SCENARIO_PROMPTS.get(task.scenario, "")
        tone_guide = TONE_GUIDES.get(task.tone, TONE_GUIDES["professional"])

        goal_lines = [
            f"为报告章节「{task.section_title}」撰写业务视角内容。",
            f"业务场景: {scenario_desc}",
            f"写作方向: {scenario_prompt}",
            f"语气要求: {tone_guide}",
            f"最多 {task.max_paragraphs} 个段落，每段聚焦一个核心观点。",
            "遵循 copywriting 原则：清晰胜于巧妙，具体胜于模糊，主动语态。",
        ]

        return DelegationTask(
            skill="copywriting",
            goal="\n".join(goal_lines),
            context="\n".join(context_parts) if context_parts else "",
            mode=MODE_HERMES,
            expected_output=f"针对「{task.section_title}」的业务文案，{task.max_paragraphs} 段以内",
            metadata={
                "scenario": task.scenario,
                "tone": task.tone,
                "section_title": task.section_title,
            },
        )

    def generate(self, task: BusinessWritingTask) -> BusinessWritingResult:
        """直接生成业务文案（不依赖 Hermes 委托）。

        使用模板引擎生成初始内容。适用于快速原型。

        Args:
            task: 业务文案撰写任务

        Returns:
            生成的文案结果
        """
        scenario_label = BUSINESS_SCENARIO_TYPES.get(task.scenario, task.scenario)
        tone_guide = TONE_GUIDES.get(task.tone, TONE_GUIDES["professional"])

        paragraphs: list[str] = []
        for i in range(min(task.max_paragraphs, 3)):
            para = self._build_paragraph(
                task=task,
                paragraph_index=i,
                total_paragraphs=task.max_paragraphs,
                scenario_label=scenario_label,
            )
            paragraphs.append(para)

        content = "\n\n".join(paragraphs)

        logger.info(
            "BusinessWriter: generated '%s' (%s, %s tone, %d paragraphs)",
            task.section_title, task.scenario, task.tone, len(paragraphs),
        )

        return BusinessWritingResult(
            section_title=task.section_title,
            scenario=task.scenario,
            content=content,
            tone=task.tone,
        )

    def _build_paragraph(
        self,
        task: BusinessWritingTask,
        paragraph_index: int,
        total_paragraphs: int,
        scenario_label: str,
    ) -> str:
        """构建单段文案。

        Args:
            task: 撰写任务
            paragraph_index: 段落序号 (0-based)
            total_paragraphs: 总段数
            scenario_label: 场景标签

        Returns:
            单段文案内容
        """
        if not task.key_findings:
            return (
                f"从{scenario_label}的角度看，{task.project_context or '该方案'} "
                f"展现出显著的业务价值。建议进一步收集具体数据以量化效果。"
            )

        if paragraph_index == 0:
            # 开篇：概述 + 第一个关键数据
            finding = task.key_findings[0] if task.key_findings else ""
            return (
                f"## 业务价值分析: {scenario_label}\n\n"
                f"在{scenario_label}方面，{task.project_context or '该方案'}展现出明确的价值。"
                f"{'以' + finding + '为例，' if finding else ''}"
                f"这表明项目在业务层面上具备实际的可落地性和可衡量的投入产出比。"
            )

        if paragraph_index == 1 and len(task.key_findings) > 1:
            # 第二段：对比或具体说明
            finding = task.key_findings[1]
            return (
                f"进一步分析显示，{finding}。"
                f"这一数据从{task.scenario.replace('_', '')}维度验证了方案的有效性。"
                f"结合行业基准来看，该表现处于领先水平，具备显著的竞争优势。"
            )

        # 末段：总结和展望
        return (
            f"综合来看，{task.project_context or '该方案'}在{scenario_label}维度上，"
            f"不仅解决了当前业务痛点，也为未来的持续优化和扩展奠定了坚实基础。"
            f"建议在实际实施过程中持续跟踪关键指标，验证上述预期效果。"
        )
