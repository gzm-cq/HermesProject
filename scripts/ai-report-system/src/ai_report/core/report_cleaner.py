"""报告内容清洗器 — 负责报告内容清洗和格式修正。

遵循 Hermes Code Rules 规范。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .exceptions import ContentCleanError

logger = logging.getLogger(__name__)

__all__ = ["ReportCleaner"]

# 行首需去除的 bold 前缀模式
_BOLD_PREFIX_PATTERN = re.compile(
    r"^\*\*(本章小结|关键结论|核心结论|结论|小结|总结|核心思路|核心问题|核心观点|"
    r"核心建设重点|安全管控要求|业务价值验证|方案要点|投资与预期回报|建议决策)"
    r"[：:（(]?\*\*"
)


class ReportCleaner:
    """负责报告内容清洗和格式修正。

    处理 LLM 输出常见的质量问题：
    1. 去除星号标记（**text** → text）
    2. 去除 **结论：** 等前缀
    3. 修复标题编号不连续问题
    """

    def clean_report(self, report: Any) -> None:
        """清洗报告对象（修改 full_content 和 sections）。

        Args:
            report: 报告对象，需具有 full_content 和 sections 属性
        """
        if not report or not report.full_content:
            return

        report.full_content = self.remove_artifacts(report.full_content)
        report.full_content = self.fix_headings(report.full_content)

        # 更新各章节的内容（同步清洗）
        if hasattr(report, "sections"):
            for sec in report.sections:
                if hasattr(sec, "content") and sec.content:
                    sec.content = sec.content.replace("**", "")
                    sec.content = _BOLD_PREFIX_PATTERN.sub("", sec.content)

    def clean(self, content: str, title: str | None = None) -> str:
        """清洗报告内容（纯字符串操作）。

        Args:
            content: 报告 markdown 内容
            title: 可选的标题（当前未使用，预留扩展）

        Returns:
            清洗后的内容
        """
        content = self.remove_artifacts(content)
        content = self.fix_headings(content)
        return content

    def remove_artifacts(self, content: str) -> str:
        """移除生成产物（** 标记、分隔线等）。

        Args:
            content: 报告 markdown 内容

        Returns:
            清洗后的内容
        """
        lines = content.split("\n")
        cleaned: list[str] = []

        for line in lines:
            # 跳过纯分隔线
            if line.strip() in ("---", "***", "___"):
                continue

            # 标题行：保持层级不变
            if line.startswith("#"):
                cleaned.append(line)
                continue

            # 去除行首的 **结论：** / **本章小结：** 等模式
            line = _BOLD_PREFIX_PATTERN.sub("", line)
            # 去除行首残留的 **（单独的 bold 标记）
            line = re.sub(r"^\*\*", "", line)
            # 去除行尾的 **
            line = re.sub(r"\*\*$", "", line)

            # 表格行保留原样
            if line.strip().startswith("|") and line.strip().endswith("|"):
                cleaned.append(line)
                continue

            # 普通文本：去除所有 ** 标记（将 **text** → text）
            if not line.strip().startswith("|"):
                line = line.replace("**", "")

            cleaned.append(line)

        return "\n".join(cleaned)

    def fix_headings(self, content: str) -> str:
        """修正标题层级 — 去除 LLM 生成的编号不连续的标题前缀。

        处理策略：
        - H1 编号不变（# 标题）
        - H2（##）去除原有编号，保持文本不变
        - H3（###）去除原有编号，保持文本不变

        Args:
            content: 报告 markdown 内容

        Returns:
            修正后的内容
        """
        lines = content.split("\n")
        result: list[str] = []

        for line in lines:
            if line.startswith("###"):
                # H3: 去除行首的 "X.X.X" 或 "X.X " 编号
                line = re.sub(r"^###\s*\d+\.\d+\.\d+\s+", "### ", line)
                line = re.sub(r"^###\s*\d+\.\d+\s+", "### ", line)
                line = re.sub(r"^###\s*\d+\s+", "### ", line)
                line = line.rstrip()
            elif line.startswith("##"):
                # H2: 去除行首的 "X.X " 编号前缀
                line = re.sub(r"^##\s*\d+\.\d+\s+", "## ", line)
                # 去除行首的 "X " 单一数字前缀
                line = re.sub(r"^##\s*\d+\s+", "## ", line)
                line = line.rstrip()
            result.append(line)

        return "\n".join(result)
