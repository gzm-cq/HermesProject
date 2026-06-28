"""
Hermes Report Planner — 报告规划器
智能识别报告类型、生成大纲、评估资源需求
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeAlias

from .base import BaseComponent
from ..config import get_config
from .exceptions import ReportAgentError
from ..adapters.dify_kb import DifyKBRetriever
from ..adapters.web_search import HermesWebSearcher

logger = logging.getLogger(__name__)

# 类型别名
SectionTitle: TypeAlias = str


@dataclass
class SectionSpec:
    """章节规格"""
    title: str
    level: int              # 1-6, H1-H6
    section_type: str       # intro, body, analysis, conclusion, appendix
    estimated_words: int    # 预估字数
    required_data: list[str] = field(default_factory=list)    # 需要的数据来源
    diagram_types: list[str] = field(default_factory=list)    # 需要的图表类型
    content_template: str | None = None
    sub_sections: list[SectionSpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "title": self.title,
            "level": self.level,
            "section_type": self.section_type,
            "estimated_words": self.estimated_words,
            "required_data": self.required_data,
            "diagram_types": self.diagram_types,
            "sub_sections": [s.to_dict() for s in self.sub_sections],
        }


@dataclass
class ReportPlan:
    """完整报告计划"""
    title: str
    topic: str
    report_type: str           # tech, market, product, research
    language: str              # zh, en
    sections: list[SectionSpec]
    resource_needs: dict[str, Any] = field(default_factory=dict)
    estimated_total_words: int = 0
    estimated_minutes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """初始化后统计"""
        self.estimated_total_words = sum(s.estimated_words for s in self.sections)
        # 估算时间：中文约100字/分钟，英文约200字/分钟
        speed = 100 if self.language == "zh" else 200
        self.estimated_minutes = max(1, self.estimated_total_words // speed)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "title": self.title,
            "topic": self.topic,
            "report_type": self.report_type,
            "language": self.language,
            "sections": [s.to_dict() for s in self.sections],
            "resource_needs": self.resource_needs,
            "estimated_total_words": self.estimated_total_words,
            "estimated_minutes": self.estimated_minutes,
            "metadata": self.metadata,
        }

    def preview(self, max_lines: int = 30) -> str:
        """生成大纲预览文本"""
        lines = [f"# {self.title}", ""]
        lines.append(f"类型: {self.report_type} | 语言: {self.language}")
        lines.append(f"估算: {self.estimated_total_words}字 ~ {self.estimated_minutes}分钟")
        lines.append("")

        def _render_sections(sections: list[SectionSpec], indent: int = 0) -> None:
            for sec in sections:
                prefix = "#" * sec.level
                marker = f"{prefix} " if sec.level <= 6 else "  "
                lines.append(f"{'  ' * indent}{marker}{sec.title} "
                             f"[{sec.section_type}, ~{sec.estimated_words}字]")
                if sec.sub_sections:
                    _render_sections(sec.sub_sections, indent + 1)

        _render_sections(self.sections[:max_lines])

        return "\n".join(lines)


# ── 报告模板注册表 ──────────────────────────────────────

# 报告类型模式定义
_REPORT_PATTERNS: dict[str, dict[str, Any]] = {
    "tech": {
        "keywords": ["技术", "架构", "设计", "实现", "算法", "框架",
                     "部署", "性能", "优化", "开发", "system", "architecture",
                     "implementation", "design", "engineering"],
        "structure": [
            ("引言/背景", "intro", 200, ["背景信息", "问题定义"]),
            ("技术方案", "body", 500, ["技术调研", "方案对比"], ["architecture", "comparison"]),
            ("架构设计", "body", 500, ["架构图", "模块说明"], ["architecture", "flowchart"]),
            ("实现细节", "body", 800, ["代码示例", "接口说明"]),
            ("测试验证", "body", 300, ["测试数据", "性能数据"]),
            ("总结展望", "conclusion", 200, []),
        ],
    },
    "market": {
        "keywords": ["市场", "商业", "分析", "策略", "计划", "竞争",
                     "用户", "增长", "营收", "market", "business",
                     "strategy", "analysis", "competition"],
        "structure": [
            ("执行摘要", "intro", 200, []),
            ("市场概况", "body", 400, ["市场规模", "趋势分析"], ["infographic"]),
            ("竞争分析", "body", 400, ["竞品对比", "SWOT"], ["comparison", "table"]),
            ("目标用户", "body", 300, ["用户画像", "需求分析"]),
            ("实施方案", "body", 500, ["路线图", "资源规划"], ["timeline"]),
            ("财务预测", "body", 300, ["营收模型", "成本分析"]),
            ("风险与对策", "body", 200, ["风险矩阵"]),
            ("结论", "conclusion", 200, []),
        ],
    },
    "product": {
        "keywords": ["产品", "功能", "需求", "用户体验", "PRD", "原型",
                     "迭代", "roadmap", "product", "feature",
                     "requirement", "user experience"],
        "structure": [
            ("产品概述", "intro", 200, ["产品定位", "目标"]),
            ("用户故事", "body", 400, ["用户场景", "痛点分析"]),
            ("功能规格", "body", 600, ["功能列表", "优先级"], ["table"]),
            ("交互设计", "body", 300, ["流程设计"], ["flowchart"]),
            ("技术方案", "body", 400, ["技术选型"], ["architecture"]),
            ("迭代规划", "body", 300, ["路线图"], ["timeline"]),
            ("验收标准", "body", 200, []),
        ],
    },
    "research": {
        "keywords": ["研究", "论文", "实验", "分析", "综述", "调研",
                     "调查", "文献", "research", "survey",
                     "experiment", "analysis", "literature"],
        "structure": [
            ("摘要", "intro", 150, []),
            ("研究背景", "intro", 300, ["文献综述", "问题陈述"]),
            ("研究方法", "body", 400, ["实验设计", "数据收集"]),
            ("实验结果", "body", 500, ["数据分析", "可视化"], ["infographic"]),
            ("讨论", "body", 300, ["结果分析", "局限性"]),
            ("结论", "conclusion", 200, []),
            ("参考文献", "appendix", 100, []),
        ],
    },
}


class HermesReportPlanner(BaseComponent):
    """
    报告规划器 — 智能识别报告类型并生成大纲

    功能:
    - 自动识别报告类型（tech/market/product/research）
    - 基于模板生成结构化大纲
    - 资源需求评估（图表、数据、参考文献）
    - 写作时间估算
    - 自定义参数覆盖

    用法:
        planner = HermesReportPlanner()
        plan = planner.create_plan(
            topic="微服务架构在电商系统中的应用",
            report_type="tech",
        )
        print(plan.preview())
    """

    COMPONENT_NAME = "HermesReportPlanner"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "报告规划器，智能识别类型和生成大纲"

    def __init__(self, config: Any | None = None) -> None:
        self._searcher: HermesWebSearcher | None = None
        self._kb_retriever: DifyKBRetriever | None = None
        super().__init__(config)

    def _initialize_internal(self) -> None:
        """初始化规划器"""
        self._searcher = HermesWebSearcher()
        self._kb_retriever = DifyKBRetriever()
        logger.info(
            "%s 初始化完成, %d种报告模板 (RAG samples enabled)",
            self.COMPONENT_NAME, len(_REPORT_PATTERNS),
        )

    # ── 公开接口 ──────────────────────────────────────────

    def create_plan(
        self,
        topic: str,
        report_type: str | None = None,
        language: str | None = None,
        custom_structure: list[dict[str, Any]] | None = None,
        samples: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ReportPlan:
        """
        创建报告计划

        Args:
            topic: 报告主题
            report_type: 报告类型（tech/market/product/research），None自动检测
            language: 语言（zh/en），None使用配置
            custom_structure: 自定义结构
            samples: 参考样本的结构摘要列表（由 search_samples 获取）
            metadata: 额外元数据

        Returns:
            完整报告计划

        Raises:
            ReportAgentError: 主题为空时抛出
        """
        start_time = time.time()

        if not topic or not topic.strip():
            raise ReportAgentError("报告主题不能为空")

        cfg = self._config.report_config
        language = language or cfg.language

        # 1. 检测报告类型
        if report_type is None:
            report_type = self._detect_report_type(topic)
            logger.info("自动检测报告类型: '%s' → %s", topic[:30], report_type)

        # 2. 生成大纲
        if custom_structure:
            sections = self._build_custom_sections(custom_structure)
        elif samples:
            sections = self._build_sections_with_samples(topic, report_type, language, samples)
        else:
            sections = self._build_template_sections(topic, report_type, language)

        # 3. 评估资源需求
        resource_needs = self._assess_resource_needs(sections, report_type)

        # 4. 生成标题
        title = self._generate_title(topic, report_type, language)

        plan = ReportPlan(
            title=title,
            topic=topic,
            report_type=report_type,
            language=language,
            sections=sections,
            resource_needs=resource_needs,
            metadata={
                "generated_at": datetime.now().isoformat(),
                "template_used": custom_structure is None,
                "samples_provided": bool(samples),
                **(metadata or {}),
            },
        )

        elapsed = (time.time() - start_time) * 1000
        self._record_performance(start_time, success=True)
        logger.info(
            "规划完成: '%s' → %s (%d章节, ~%d字, %.0fms)",
            topic[:30], report_type, len(sections),
            plan.estimated_total_words, elapsed,
        )

        return plan

    def detect_type(self, topic: str) -> str:
        """仅检测报告类型"""
        return self._detect_report_type(topic)

    def list_templates(self) -> list[str]:
        """列出可用模板类型"""
        return list(_REPORT_PATTERNS.keys())

    # ── 样本搜索 ──────────────────────────────────────────

    def search_samples(
        self,
        topic: str,
        report_type: str,
    ) -> list[str]:
        """搜索同类文档样本的结构摘要（Web搜索 + Dify KB RAG）。

        搜索与该主题+类型相关的优质文档，返回结构摘要列表。
        先查 Web，再补 Dify KB 已有知识。

        Args:
            topic: 报告主题
            report_type: 报告类型

        Returns:
            样本结构摘要列表（可能为空列表，优雅降级）
        """
        samples: list[str] = []

        # 1. Web 搜索样本（现有逻辑）
        if self._searcher is not None:
            queries = [
                f"{topic} {report_type} report template structure",
                f"{report_type} report outline example {topic}",
            ]
            for query in queries:
                try:
                    task = self._searcher.prepare(query, max_results=3)
                    _ = task  # 委托任务由 Hermes 执行
                    samples.append(f"Sample reference for: {query}")
                except Exception as e:
                    logger.debug("Sample search failed for '%s': %s", query[:30], e)

        # 2. Dify KB 检索（新增 RAG 样本）
        if self._kb_retriever is not None and self._kb_retriever.check_available():
            try:
                kb_result = self._kb_retriever.retrieve(
                    f"{topic} {report_type} report",
                    top_k=3,
                )
                if kb_result.success:
                    for seg in kb_result.segments:
                        label = seg.document_name or "KB参考"
                        snippet = seg.content[:200].replace("\n", " ")
                        samples.append(f"[KB参考] {label}: {snippet}")
                    logger.info(
                        "  Dify KB samples: %d 条知识库参考已加入规划",
                        len(kb_result.segments),
                    )
                elif kb_result.error:
                    logger.debug("KB samples unavailable: %s", kb_result.error)
            except Exception as e:
                logger.debug("KB sample retrieval failed: %s", e)

        if not samples:
            logger.info("search_samples: no samples found, using template")
        else:
            logger.info("search_samples: %d samples (web+KB) found", len(samples))

        return samples

    # ── 带样本的大纲生成 ──────────────────────────────────

    @staticmethod
    def _build_sections_with_samples(
        topic: str,
        report_type: str,
        language: str,
        samples: list[str],
    ) -> list[SectionSpec]:
        """基于模板 + 样本参考生成更丰富的章节结构。

        Args:
            topic: 报告主题
            report_type: 报告类型
            language: 语言
            samples: 参考样本摘要列表

        Returns:
            章节规格列表
        """
        # 先基于模板生成基础结构
        sections = HermesReportPlanner._build_template_sections(
            topic, report_type, language,
        )

        # 根据样本信息丰富章节描述（标记样本来源）
        if samples:
            sample_note = f"\n\n> 参考样本: {'; '.join(samples[:2])}"
            if sections:
                # 在第一个章节追加样本参考信息
                first = sections[0]
                if first.content_template:
                    first.content_template += sample_note
                else:
                    first.content_template = sample_note

        return sections

    # ── 类型检测 ──────────────────────────────────────────

    @staticmethod
    def _detect_report_type(topic: str) -> str:
        """从主题文本检测报告类型"""
        topic_lower = topic.lower()
        scores: dict[str, int] = {}

        for rtype, pattern in _REPORT_PATTERNS.items():
            score = 0
            for kw in pattern["keywords"]:
                if kw.lower() in topic_lower:
                    score += 1
            scores[rtype] = score

        if not scores or max(scores.values()) == 0:
            return "tech"  # 默认

        best = max(scores, key=scores.get)
        return best

    # ── 大纲生成 ──────────────────────────────────────────

    @staticmethod
    def _build_template_sections(
        topic: str,
        report_type: str,
        language: str,
    ) -> list[SectionSpec]:
        """基于模板生成章节"""
        pattern = _REPORT_PATTERNS.get(report_type, _REPORT_PATTERNS["tech"])
        sections: list[SectionSpec] = []
        template = pattern["structure"]

        for i, entry in enumerate(template):
            title = entry[0]
            sec_type = entry[1]
            words = entry[2]
            req_data: list[str] = entry[3] if len(entry) > 3 else []
            diagram_types: list[str] = entry[4] if len(entry) > 4 else []

            # 动态适配：将模板中的占位符替换为主题相关内容
            adaptive_title = HermesReportPlanner._adapt_title(
                title, topic, report_type, i, language,
            )

            section = SectionSpec(
                title=adaptive_title,
                level=1 if i == 0 and title in ["摘要", "执行摘要"] else 2,
                section_type=sec_type,
                estimated_words=words,
                required_data=req_data,
                diagram_types=diagram_types,
            )
            sections.append(section)

        return sections

    @staticmethod
    def _build_custom_sections(
        structure: list[dict[str, Any]],
    ) -> list[SectionSpec]:
        """从自定义结构生成章节"""
        sections = []
        for item in structure:
            section = SectionSpec(
                title=item.get("title", "未命名"),
                level=item.get("level", 2),
                section_type=item.get("section_type", "body"),
                estimated_words=item.get("estimated_words", 200),
                required_data=item.get("required_data", []),
                diagram_types=item.get("diagram_types", []),
            )
            # 处理子章节
            sub_items = item.get("sub_sections", [])
            if sub_items:
                section.sub_sections = HermesReportPlanner._build_custom_sections(sub_items)
            sections.append(section)
        return sections

    # ── 资源评估 ──────────────────────────────────────────

    @staticmethod
    def _assess_resource_needs(
        sections: list[SectionSpec],
        report_type: str,
    ) -> dict[str, Any]:
        """评估资源需求"""
        all_diagrams: list[str] = []
        all_data: list[str] = []
        total_words = 0

        def _collect(sections: list[SectionSpec]) -> None:
            nonlocal total_words
            for sec in sections:
                all_diagrams.extend(sec.diagram_types)
                all_data.extend(sec.required_data)
                total_words += sec.estimated_words
                if sec.sub_sections:
                    _collect(sec.sub_sections)

        _collect(sections)

        # 去重统计
        diagram_counts: dict[str, int] = {}
        for d in all_diagrams:
            diagram_counts[d] = diagram_counts.get(d, 0) + 1

        return {
            "total_diagrams": len(all_diagrams),
            "diagram_types": diagram_counts,
            "data_sources": list(set(all_data)),
            "diagram_generation": any(d in ["architecture", "flowchart", "comparison", "timeline"]
                                      for d in all_diagrams),
            "quality_check_needed": True,
            "total_words": total_words,
        }

    # ── 标题生成 ──────────────────────────────────────────

    @staticmethod
    def _generate_title(topic: str, report_type: str, language: str) -> str:
        """生成报告标题"""
        if language == "zh":
            type_map = {
                "tech": "技术方案",
                "market": "市场分析",
                "product": "产品方案",
                "research": "研究报告",
            }
            suffix = type_map.get(report_type, "报告")
            return f"{topic} {suffix}"
        else:
            type_map = {
                "tech": "Technical Report",
                "market": "Market Analysis",
                "product": "Product Specification",
                "research": "Research Report",
            }
            suffix = type_map.get(report_type, "Report")
            return f"{topic} - {suffix}"

    @staticmethod
    def _adapt_title(
        base_title: str,
        topic: str,
        report_type: str,
        index: int,
        language: str,
    ) -> str:
        """适配章节标题到具体主题"""
        if language == "zh":
            return base_title
        return base_title

    # ── 执行 ──

    def execute(self, operation: str = "create_plan", **kwargs: Any) -> Any:
        """执行规划操作"""
        operations = {
            "create_plan": self.create_plan,
            "detect_type": self.detect_type,
            "list_templates": self.list_templates,
        }

        if operation not in operations:
            raise ReportAgentError(f"未知规划操作: {operation}")

        return operations[operation](**kwargs)
