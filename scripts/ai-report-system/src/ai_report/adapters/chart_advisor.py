"""
图表顾问 — 基于 data-analysis skill 的图表选型推荐
===================================================
职责：
- 根据报告类型、章节类型、数据类型，推荐最合适的图表
- 提供图表风格指南和配置（dark 主题、学术配色）
- 不直接生成图表，只输出规格供 LLM 参考生成

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── 图表定义 ────────────────────────────────────────────────

@dataclass
class ChartSpec:
    """单个图表的规格定义。

    Attributes:
        chart_type: 图表类型 (bar/line/pie/scatter/heatmap/radar/table)
        title: 图表标题
        purpose: 分析目的（为什么选这个图）
        data_requirements: 需要的数据字段
        style_hints: 风格提示（配色、尺寸等）
        priority: 推荐优先级 (1-5, 1最高)
    """
    chart_type: str
    title: str
    purpose: str
    data_requirements: list[str] = field(default_factory=list)
    style_hints: dict[str, str] = field(default_factory=dict)
    priority: int = 3

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "chart_type": self.chart_type,
            "title": self.title,
            "purpose": self.purpose,
            "data_requirements": self.data_requirements,
            "style_hints": self.style_hints,
            "priority": self.priority,
        }


@dataclass
class ChartAdvice:
    """完整图表建议。

    Attributes:
        report_type: 报告类型
        recommended_charts: 推荐的图表列表
        style_guide: 全局风格指南
        notes: 附加说明
    """
    report_type: str
    recommended_charts: list[ChartSpec] = field(default_factory=list)
    style_guide: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "report_type": self.report_type,
            "recommended_charts": [c.to_dict() for c in self.recommended_charts],
            "style_guide": self.style_guide,
            "notes": self.notes,
        }


# ── 全局风格配置 ────────────────────────────────────────────

DARK_STYLE_GUIDE: dict[str, Any] = {
    "theme": "dark",
    "background_color": "#1e1e2e",
    "text_color": "#cdd6f4",
    "font_family": "sans-serif",
    "palette": {
        "primary": ["#89b4fa", "#a6e3a1", "#f9e2af", "#f38ba8", "#cba6f7"],
        "secondary": ["#74c7ec", "#94e2d5", "#fab387", "#eba0ac", "#b4befe"],
        "accent": "#f5c2e7",
    },
    "grid": {
        "show": True,
        "color": "#313244",
        "style": "dashed",
    },
    "sizing": {
        "default_width": 800,
        "default_height": 500,
        "thumbnail_width": 400,
        "thumbnail_height": 300,
    },
    "typography": {
        "title_size": 16,
        "label_size": 12,
        "legend_size": 10,
    },
}

LIGHT_STYLE_GUIDE: dict[str, Any] = {
    "theme": "light",
    "background_color": "#ffffff",
    "text_color": "#1e1e2e",
    "font_family": "sans-serif",
    "palette": {
        "primary": ["#1e77b4", "#2ca02c", "#d62728", "#ff7f0e", "#9467bd"],
        "secondary": ["#17becf", "#98df8a", "#ffbb78", "#c5b0d5", "#8c564b"],
        "accent": "#e377c2",
    },
    "grid": {
        "show": True,
        "color": "#e0e0e0",
        "style": "solid",
    },
    "sizing": {
        "default_width": 800,
        "default_height": 500,
        "thumbnail_width": 400,
        "thumbnail_height": 300,
    },
    "typography": {
        "title_size": 16,
        "label_size": 12,
        "legend_size": 10,
    },
}


# ── 图表选型规则 ────────────────────────────────────────────

class ChartRules:
    """基于 data-analysis skill 的图表选型规则引擎。"""

    # 数据类型 → 推荐图表类型映射
    # data-analysis skill: 根据分析问题类型选图
    TYPE_MAP: dict[str, list[dict[str, Any]]] = {
        "trend": [
            {"type": "line", "purpose": "展示时间序列趋势变化", "priority": 1},
            {"type": "bar", "purpose": "对比各时间点的具体数值", "priority": 2},
        ],
        "comparison": [
            {"type": "bar", "purpose": "分类数据横向对比", "priority": 1},
            {"type": "radar", "purpose": "多维度综合对比", "priority": 2},
        ],
        "distribution": [
            {"type": "histogram", "purpose": "数据分布概览", "priority": 1},
            {"type": "box", "purpose": "分布统计特征（中位数、四分位）", "priority": 2},
        ],
        "composition": [
            {"type": "pie", "purpose": "占比构成展示", "priority": 1},
            {"type": "stacked_bar", "purpose": "构成 + 趋势双重展示", "priority": 2},
        ],
        "relationship": [
            {"type": "scatter", "purpose": "两个变量间的关系", "priority": 1},
            {"type": "heatmap", "purpose": "多变量相关性矩阵", "priority": 2},
        ],
        "hierarchy": [
            {"type": "treemap", "purpose": "层次结构 + 数值占比", "priority": 1},
            {"type": "sunburst", "purpose": "多层嵌套占比展示", "priority": 2},
        ],
        "geographic": [
            {"type": "map", "purpose": "地理分布可视化", "priority": 1},
            {"type": "choropleth", "purpose": "区域数据密度展示", "priority": 2},
        ],
        "table": [
            {"type": "table", "purpose": "精确数值展示", "priority": 1},
        ],
    }

    # 报告类型 → 常用分析问题映射
    REPORT_ANALYSIS_MAP: dict[str, list[dict[str, Any]]] = {
        "tech": [
            {"analysis": "trend", "section_types": ["trend", "history", "发展历程"], "question": "技术指标随时间如何变化？"},
            {"analysis": "comparison", "section_types": ["comparison", "对比", "benchmark"], "question": "不同技术方案之间如何对比？"},
            {"analysis": "composition", "section_types": ["composition", "构成", "architecture"], "question": "系统由哪些组件构成？"},
            {"analysis": "table", "section_types": ["data", "spec", "规格", "参数"], "question": "详细规格和技术参数"},
        ],
        "market": [
            {"analysis": "trend", "section_types": ["trend", "market_size", "市场规模"], "question": "市场规模和增长率如何变化？"},
            {"analysis": "comparison", "section_types": ["competition", "竞争", "对比"], "question": "各厂商市场份额对比？"},
            {"analysis": "composition", "section_types": ["segmentation", "segment", "细分"], "question": "市场由哪些细分领域构成？"},
            {"analysis": "table", "section_types": ["data", "company", "厂商"], "question": "厂商详细数据对比表"},
        ],
        "product": [
            {"analysis": "comparison", "section_types": ["comparison", "对比", "竞品"], "question": "产品功能特征对比？"},
            {"analysis": "trend", "section_types": ["roadmap", "路线图", "发展"], "question": "产品迭代趋势如何？"},
            {"analysis": "composition", "section_types": ["feature", "功能", "模块"], "question": "产品功能模块构成？"},
            {"analysis": "table", "section_types": ["spec", "规格", "参数"], "question": "产品规格参数对比表"},
        ],
        "research": [
            {"analysis": "trend", "section_types": ["trend", "发展", "progress"], "question": "研究进展趋势？"},
            {"analysis": "comparison", "section_types": ["comparison", "方法", "approach"], "question": "不同研究方法对比？"},
            {"analysis": "distribution", "section_types": ["distribution", "分布", "statistics"], "question": "数据分布特征？"},
            {"analysis": "table", "section_types": ["data", "paper", "文献"], "question": "研究论文数据汇总表"},
        ],
    }

    @classmethod
    def recommend_for_analysis_type(cls, analysis_type: str) -> list[dict[str, Any]]:
        """根据分析问题类型推荐图表。

        Args:
            analysis_type: 分析类型 (trend/comparison/distribution/composition/relationship)

        Returns:
            推荐的图表配置列表
        """
        return cls.TYPE_MAP.get(analysis_type, cls.TYPE_MAP["table"])

    @classmethod
    def recommend_for_section(
        cls,
        report_type: str,
        section_type: str,
        section_title: str,
    ) -> list[dict[str, Any]]:
        """根据报告类型和章节类型推荐图表。

        Args:
            report_type: 报告类型 (tech/market/product/research)
            section_type: 章节类型 (trend/comparison/analysis 等)
            section_title: 章节标题

        Returns:
            推荐的图表配置列表
        """
        report_rules = cls.REPORT_ANALYSIS_MAP.get(report_type, cls.REPORT_ANALYSIS_MAP["tech"])

        best_analysis: str | None = None
        best_score: int = 0

        for rule in report_rules:
            score = 0
            for st in rule["section_types"]:
                if st in section_type.lower() or st in section_title.lower():
                    score += 1
            if score > best_score:
                best_score = score
                best_analysis = rule["analysis"]

        analysis_type = best_analysis or "table"
        return cls.recommend_for_analysis_type(analysis_type)


# ── 主顾问类 ────────────────────────────────────────────────

class ChartAdvisor:
    """图表顾问 — 为报告生成提供图表选型建议。

    用法:
        advisor = ChartAdvisor(theme="dark")
        advice = advisor.advise(
            report_type="market",
            sections=[{"title": "市场规模", "type": "trend"}],
        )
        # advice.recommended_charts → 图表规格列表
    """

    def __init__(self, theme: str = "dark") -> None:
        """初始化图表顾问。

        Args:
            theme: 主题风格 (dark / light)
        """
        self._rules = ChartRules()
        self._style = DARK_STYLE_GUIDE if theme == "dark" else LIGHT_STYLE_GUIDE
        self._theme = theme

    def advise(
        self,
        report_type: str,
        sections: list[dict[str, str]],
        custom_data: dict[str, Any] | None = None,
    ) -> ChartAdvice:
        """为报告生成图表建议。

        Args:
            report_type: 报告类型 (tech/market/product/research)
            sections: 章节列表，每项包含 title 和 type
            custom_data: 可选的额外数据上下文

        Returns:
            完整的图表建议，包含推荐图表和风格指南
        """
        advice = ChartAdvice(
            report_type=report_type,
            style_guide=self._style,
        )

        for section in sections:
            title = section.get("title", "")
            section_type = section.get("type", "data")
            chart_options = self._rules.recommend_for_section(
                report_type, section_type, title,
            )

            for opt in chart_options:
                spec = ChartSpec(
                    chart_type=opt["type"],
                    title=f"{title} — {opt['purpose']}",
                    purpose=opt["purpose"],
                    priority=opt.get("priority", 3),
                    style_hints={
                        "theme": self._theme,
                        "color_scheme": "primary_palette",
                        "responsive": "true",
                    },
                )
                advice.recommended_charts.append(spec)

        # 按优先级排序
        advice.recommended_charts.sort(key=lambda c: c.priority)

        # 附加说明
        advice.notes = self._generate_notes(report_type, advice.recommended_charts)

        logger.info(
            "ChartAdvisor: %s report → %d charts recommended",
            report_type, len(advice.recommended_charts),
        )
        return advice

    def _generate_notes(
        self,
        report_type: str,
        charts: list[ChartSpec],
    ) -> list[str]:
        """生成附加说明。

        Args:
            report_type: 报告类型
            charts: 推荐的图表列表

        Returns:
            说明列表
        """
        notes: list[str] = []

        if len(charts) > 10:
            notes.append(f"建议控制图表数量在 5-8 个以内，当前推荐了 {len(charts)} 个")

        chart_types = set(c.chart_type for c in charts)
        if "pie" in chart_types:
            notes.append("饼图建议不超过 5 个扇区，过多可用条形图替代")
        if "radar" in chart_types:
            notes.append("雷达图适合 3-8 个维度的对比，超过可用平行坐标")

        notes.append(f"基于 data-analysis skill 方法：每个图表应为回答一个具体的分析问题而存在")
        notes.append(f"主题色已配置 {self._theme} 模式，LLM 生成图表时可直接使用")

        return notes
