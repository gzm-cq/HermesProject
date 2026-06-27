"""
DocxExporter — 报告导出为 Word (.docx) 文件
=========================================
将 GeneratedReport 的 markdown 内容 + chart 图片嵌入为 Word 文档。
使用 Word 内置 Heading 样式（支持自动生成目录）。

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml
from docx.shared import Inches, Pt, RGBColor

logger = logging.getLogger(__name__)


# ── 目录字段 XML（Word TOC） ────────────────────────────

_TOC_XML = (
    '<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:sdtPr>'
    '<w:docPartObj><w:docPartGallery w:val="Table of Contents"/></w:docPartObj>'
    '</w:sdtPr>'
    '<w:sdtContent>'
    '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
    '<w:r><w:t>（打开 Word 后在目录上右键 → 更新域 → 更新整个目录）</w:t></w:r>'
    '</w:p>'
    '<w:p><w:r><w:br/></w:r></w:p>'
    '</w:sdtContent></w:sdt>'
)


def _add_heading(doc: Document, text: str, level: int) -> None:
    """使用 Word 内置 Heading 样式添加标题。"""
    level = min(level, 9)
    p = doc.add_heading(text, level=level)
    for run in p.runs:
        run.font.name = "Microsoft YaHei"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")


def _add_body(doc: Document, text: str) -> None:
    """添加正文段落（支持 **bold** 内联标记）。"""
    if not text.strip():
        return
    p = doc.add_paragraph()
    _add_formatted_run(p, text.strip())
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = Pt(18)


def _add_blockquote(doc: Document, text: str) -> None:
    """添加引用块（灰色背景、缩进）。"""
    if not text.strip():
        return
    p = doc.add_paragraph()
    _add_formatted_run(p, text.strip())
    p.paragraph_format.left_indent = Inches(0.4)
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    # 添加左侧灰色边框（模拟引用样式）
    pPr = p._element.get_or_add_pPr()
    pBdr = parse_xml(
        '<w:pBdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:left w:val="single" w:sz="12" w:space="8" w:color="999999"/>'
        '</w:pBdr>'
    )
    pPr.append(pBdr)


def _add_formatted_run(p: Any, text: str) -> None:
    """向段落添加文本，解析 **bold** 标记为加粗运行。

    简单规则：交替切换 bold 状态。**...** 之间的文本 bold=true，其余 bold=false。
    不支持嵌套。
    """
    parts = re.split(r"(\*\*.*?\*\*)", text)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            inner = part[2:-2]
            run = p.add_run(inner)
            run.font.size = Pt(11)
            run.font.bold = True
            run.font.name = "Microsoft YaHei"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        else:
            run = p.add_run(part)
            run.font.size = Pt(11)
            run.font.name = "Microsoft YaHei"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")


def _add_bullet(doc: Document, text: str) -> None:
    """添加列表项（支持 **bold** 内联标记）。"""
    p = doc.add_paragraph(style="List Bullet")
    _add_formatted_run(p, text)


def _parse_markdown_table(doc: Any, line: str, lines: list[str], idx: int) -> int:
    """解析 markdown 表格，返回消耗的行数。"""
    rows_text: list[list[str]] = []
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped.startswith("|"):
            break
        if re.match(r'^\|[-:]+(\|[-:]+)*\|$', stripped):
            idx += 1
            continue
        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        rows_text.append(cells)
        idx += 1

    if len(rows_text) < 2:
        return idx

    max_cols = max(len(r) for r in rows_text)
    table = doc.add_table(rows=len(rows_text), cols=max_cols)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Light List Accent 1"
    table.autofit = True

    for i, row_data in enumerate(rows_text):
        for j, cell_text in enumerate(row_data):
            cell = table.rows[i].cells[j]
            # 使用 _add_formatted_run 渲染（支持 **bold**）
            p = cell.paragraphs[0]
            _add_formatted_run(p, cell_text)
            for run in p.runs:
                run.font.size = Pt(9)
                run.font.name = "Microsoft YaHei"
            if i == 0:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in p.runs:
                    run.font.bold = True
    return idx


# ── 主函数 ──────────────────────────────────────────────


def export_to_docx(
    title: str,
    full_content: str,
    chart_images: list[tuple[int, Path]],
    output_path: Path,
) -> Path:
    """将报告内容导出为 .docx 文件。

    使用 Word 内置 Heading 样式，支持自动生成目录。

    Args:
        title: 报告标题
        full_content: 报告全文（markdown 格式）
        chart_images: [(chapter_index, image_path), ...]
        output_path: 输出 .docx 文件路径

    Returns:
        输出文件路径
    """
    doc = Document()

    # 默认字体
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Microsoft YaHei"
    font.size = Pt(11)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    # ── 封面标题 ──
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(title)
    run.font.size = Pt(22)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0x1a, 0x1a, 0x2e)
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    p.paragraph_format.space_before = Pt(60)
    p.paragraph_format.space_after = Pt(20)

    # ── 目录 ──
    doc.add_paragraph("")  # 空行
    toc_heading = doc.add_heading("目录", level=1)
    for run in toc_heading.runs:
        run.font.name = "Microsoft YaHei"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    # 插入 TOC 字段
    doc.element.body.append(parse_xml(_TOC_XML))
    doc.add_paragraph("").add_run().add_break(WD_BREAK.PAGE)

    # ── 正文 ──
    chart_map: dict[int, Path] = {}
    for prompt_idx, path in chart_images:
        chart_map[prompt_idx + 2] = path       # +2: 执行摘要 + 1-indexed

    lines = full_content.split("\n")
    chapter_count = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 分隔线 → 分页符
        if stripped.startswith("---") and len(stripped) <= 4:
            p = doc.add_paragraph()
            run = p.add_run()
            run.add_break(WD_BREAK.PAGE)
            i += 1
            continue

        # 引用块（blockquote）
        if stripped.startswith(">"):
            text = re.sub(r"^>\s*", "", stripped)
            _add_blockquote(doc, text)
            i += 1
            continue

        # markdown 表格
        if stripped.startswith("|") and stripped.endswith("|"):
            i = _parse_markdown_table(doc, stripped, lines, i)
            continue

        # 标题行（用 Word Heading 样式）
        if stripped.startswith("#"):
            level = min(len(stripped) - len(stripped.lstrip("#")), 9)
            text = stripped.lstrip("#").strip()
            _add_heading(doc, text, level)
            i += 1
            if level <= 2:
                chapter_count += 1
                if chapter_count in chart_map:
                    img_path = chart_map[chapter_count]
                    if img_path.exists():
                        try:
                            caption = doc.add_paragraph()
                            caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            run = caption.add_run()
                            run.add_picture(str(img_path), width=Inches(5.5))
                            # 图表下方添加 caption
                            cap_p = doc.add_paragraph()
                            cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            cap_run = cap_p.add_run(f"图 {chapter_count}: {text}")
                            cap_run.font.size = Pt(9)
                            cap_run.font.name = "Microsoft YaHei"
                            cap_run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
                        except Exception as e:
                            logger.warning("  embed chart failed: %s", e)
            continue

        # 列表项
        if stripped.startswith("- ") or stripped.startswith("* "):
            _add_bullet(doc, stripped[2:])
            i += 1
            continue
        if re.match(r"^\d+[.、]", stripped):
            _add_bullet(doc, stripped)
            i += 1
            continue

        # 普通正文（支持 **bold**）
        _add_body(doc, stripped)
        i += 1

    doc.save(str(output_path))
    logger.info("docx exported: %s", output_path)
    return output_path
