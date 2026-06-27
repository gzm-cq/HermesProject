"""
Markdown 图表生成器 — ChartGenerator
======================================
功能：
1. 接收 ChartAdvisor 推荐的 ChartSpec
2. LLM 按规格生成纯 Markdown 图表（表格、ASCII 柱状条等）
3. 规则校验（类型/数据/标题），不通过则 LLM 修正
4. 插入到报告对应章节

设计原则：
- 纯 Markdown 输出，不含 HTML/SVG/图片
- 规则校验免费快速，不走 LLM
- 最多 3 次修正尝试

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .chart_advisor import ChartSpec
from ..core.exceptions import ChartGenerationError

logger = logging.getLogger(__name__)

# ── 校验结果 ────────────────────────────────────────────────

@dataclass
class ChartValidationResult:
    """图表校验结果。

    Attributes:
        spec: 原始图表规格
        chart_markdown: 生成的 Markdown 内容
        passed: 规则校验是否通过
        issues: 违规项列表
        fix_attempts: 修正尝试次数
    """
    spec: ChartSpec
    chart_markdown: str
    passed: bool = False
    issues: list[str] = field(default_factory=list)
    fix_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "chart_type": self.spec.chart_type,
            "title": self.spec.title,
            "passed": self.passed,
            "issues": self.issues,
            "fix_attempts": self.fix_attempts,
            "markdown_length": len(self.chart_markdown),
        }


# ── LLM 生成 Prompt ────────────────────────────────────────

CHART_PROMPT_TEMPLATE: str = """你是一个 Markdown 图表生成专家。请根据以下规格生成一个纯 Markdown 格式的图表。

图表类型: {chart_type}
标题: {title}
分析目的: {purpose}
主题色: {style_hints}

严格要求：
1. 仅输出 Markdown 格式，不包含 HTML/SVG/IMG 标签
2. 表格使用标准 Markdown 表格语法（| 列 | 列 |）
3. 柱状图使用 ASCII 字符（█▓▒░）表示比例
4. 必须包含具体数据（数字、百分比）
5. 必须包含图表标题

输出格式如下：

## 图表: {title}

| 类别 | 数值 |
|------|------|
| 示例 | 100 |

*数据说明和来源*"""


# ── 图表生成器 ──────────────────────────────────────────────

class ChartGenerator:
    """Markdown 图表生成器。

    流程：
    1. LLM 按规格生成 Markdown 图表
    2. 规则校验（类型/数据/标题/语法）
    3. 不通过 → LLM 按反馈修正（最多 3 次）
    4. 通过 → 准备插入报告

    用法:
        generator = ChartGenerator()
        result = generator.generate(spec)
        if result.passed:
            report_with_chart = result.chart_markdown
    """

    MAX_FIX_ATTEMPTS: int = 3

    def __init__(
        self,
        max_attempts: int = MAX_FIX_ATTEMPTS,
        llm_caller: Callable[..., str] | None = None,
    ) -> None:
        """初始化图表生成器。

        Args:
            max_attempts: 最大修正尝试次数
            llm_caller: LLM 调用函数（签名同 call_llm），
                        None=使用默认 call_llm（向后兼容）
        """
        self._max_attempts = max_attempts
        if llm_caller is not None:
            self._llm_caller = llm_caller
        else:
            from .ai_client import call_llm as _fallback
            self._llm_caller = _fallback

    # ── 公开接口 ──────────────────────────────────────────

    def generate(self, spec: ChartSpec) -> ChartValidationResult:
        """生成并校验单个图表。

        Args:
            spec: 图表规格

        Returns:
            校验后的图表结果
        """
        # 首先生成
        try:
            chart_md = self._generate_markdown(spec)
        except ChartGenerationError:
            chart_md = f"## 图表: {spec.title}\n\n（图表生成失败）\n"
        result = self._validate(chart_md, spec)

        # 如果不通过，修正
        for attempt in range(1, self._max_attempts + 1):
            if result.passed:
                break
            try:
                chart_md = self._fix_markdown(chart_md, spec, result.issues)
            except ChartGenerationError:
                pass  # 修正失败时保留原文，不中断流程
            result = self._validate(chart_md, spec)
            result.fix_attempts = attempt

        logger.info(
            "Chart '%s' (%s): %s after %d attempt(s)",
            spec.title[:30], spec.chart_type,
            "✅ passed" if result.passed else "❌ failed",
            result.fix_attempts,
        )
        return result

    def generate_all(
        self,
        specs: list[ChartSpec],
    ) -> list[ChartValidationResult]:
        """批量生成多个图表。

        Args:
            specs: 图表规格列表

        Returns:
            校验结果列表
        """
        return [self.generate(spec) for spec in specs]

    @staticmethod
    def insert_into_report(
        full_content: str,
        results: list[ChartValidationResult],
    ) -> str:
        """将校验通过的图表插入报告末尾。

        Args:
            full_content: 原始报告内容
            results: 图表校验结果列表

        Returns:
            含图表的完整报告
        """
        parts = [full_content.rstrip(), "", "---", "## 图表附录", ""]
        for r in results:
            if r.passed and r.chart_markdown.strip():
                parts.append(r.chart_markdown)
                parts.append("")
        return "\n".join(parts)

    # ── Step 1: 生成 ─────────────────────────────────────

    def _generate_markdown(self, spec: ChartSpec) -> str:
        """LLM 生成 Markdown 图表。

        Args:
            spec: 图表规格

        Returns:
            生成的 Markdown 文本
        """
        style_hints = spec.style_hints or {}
        style_str = "; ".join(f"{k}={v}" for k, v in style_hints.items())

        prompt = CHART_PROMPT_TEMPLATE.format(
            chart_type=spec.chart_type,
            title=spec.title,
            purpose=spec.purpose,
            style_hints=style_str or "default",
        )

        try:
            result = self._llm_caller(prompt, max_iterations=1, system_prompt=None)
            if result and len(result.strip()) > 10:
                return result.strip()
        except (ValueError, RuntimeError, ConnectionError, OSError) as e:
            logger.warning("Chart LLM generation failed for '%s' (type=%s): %s", spec.title, spec.chart_type, e)
            raise ChartGenerationError(
                message=f"Chart LLM generation failed: {e}",
                chart_type=spec.chart_type,
                title=spec.title,
            ) from e

        return f"## 图表: {spec.title}\n\n（图表生成失败）\n"

    # ── Step 2: 规则校验 ─────────────────────────────────

    @staticmethod
    def _validate(chart_md: str, spec: ChartSpec) -> ChartValidationResult:
        """对生成的 Markdown 图表执行规则校验。

        Args:
            chart_md: 生成的 Markdown
            spec: 图表规格

        Returns:
            校验结果
        """
        issues: list[str] = []

        # 1. 禁止 HTML/SVG/IMG 标签
        html_tags = re.findall(r'<(html|svg|img|canvas|div|table[^m]|style)[^>]*>', chart_md, re.IGNORECASE)
        if html_tags:
            issues.append(f"包含非 Markdown 标签: {set(html_tags)}")

        # 2. 图表类型检查
        chart_type = spec.chart_type
        if chart_type == "table":
            if "|" not in chart_md or "---" not in chart_md:
                issues.append("表格类型但缺少 Markdown 表格语法（| 和 ---）")
        elif chart_type == "bar":
            if not re.search(r'[█▓▒░]', chart_md):
                issues.append("柱状图但缺少 ASCII 柱状条（█▓▒░）")
        elif chart_type == "pie":
            if not re.search(r'[█▓▒░]', chart_md) and "|" not in chart_md:
                issues.append("饼图应包含 ASCII 块或数据表格")

        # 3. 数据标签检查
        if not re.search(r'\d+', chart_md):
            issues.append("图表缺少具体数据数值")

        # 4. 标题检查
        if spec.title and spec.title not in chart_md:
            issues.append(f"缺少图表标题「{spec.title}」")

        # 5. Markdown 语法完整性
        if "##" not in chart_md and "#" not in chart_md:
            issues.append("缺少 Markdown 标题标记")

        return ChartValidationResult(
            spec=spec,
            chart_markdown=chart_md,
            passed=len(issues) == 0,
            issues=issues,
        )

    # ── Step 3: 修正 ─────────────────────────────────────

    def _fix_markdown(
        self,
        chart_md: str,
        spec: ChartSpec,
        issues: list[str],
    ) -> str:
        """根据校验反馈让 LLM 修正图表。

        Args:
            chart_md: 原 Markdown
            spec: 图表规格
            issues: 需修复的问题列表

        Returns:
            修正后的 Markdown
        """
        issues_text = "\n".join(f"- {issue}" for issue in issues)
        prompt = (
            f"请修正以下 Markdown 图表。\n\n"
            f"## 原始图表\n{chart_md}\n\n"
            f"## 需要修复的问题\n{issues_text}\n\n"
            f"## 图表规格\n"
            f"类型: {spec.chart_type}\n"
            f"标题: {spec.title}\n"
            f"目的: {spec.purpose}\n\n"
            f"请直接输出修正后的完整 Markdown（仅 Markdown，不含 HTML/SVG）。"
        )

        try:
            result = self._llm_caller(prompt, max_iterations=1, system_prompt=None)
            if result and len(result.strip()) > 10:
                return result.strip()
        except (ValueError, RuntimeError, ConnectionError, OSError) as e:
            logger.warning("Chart fix LLM failed for '%s' (type=%s): %s", spec.title, spec.chart_type, e)
            raise ChartGenerationError(
                message=f"Chart fix LLM failed: {e}",
                chart_type=spec.chart_type,
                title=spec.title,
            ) from e

        return chart_md  # 修正失败，保留原文
