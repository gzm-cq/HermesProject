"""
feedback_revision — 从 Word 批注驱动章节修订
=============================================
用法:
    python3 scripts/feedback_revision.py <报告.docx>

流程:
  1. 读取 .docx 中所有批注（comment）及批注所在的段落
  2. 定位每个批注所在的 H1 章节
  3. 加载 report_goal.json 获取写作角色定义
  4. 对有批注的章节，构造修订 prompt（原文 + 批注 + 写作要求）
  5. 调 LLM 修订 → 替换该章节内容 → 输出 xxx_revised.docx

依赖:
    python-docx (已安装)
"""
from __future__ import annotations

import json
import logging
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("feedback_revision")

# ── 项目根 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = PROJECT_ROOT / "reports"


# ── XML 命名空间 ────────────────────────────────────────────
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
}


def _find_topic_from_path(docx_path: Path) -> str:
    """从 .docx 文件路径推断报告主题（目录名）。

    Returns:
        reports/ 下的子目录名（主题名）。
        如果 docx 直接放在 reports/ 下或无法推断，回退到文件名前缀。
    """
    parent = docx_path.parent
    # 向上查找 reports/ 目录
    while parent.name != "reports" and parent != parent.parent:
        parent = parent.parent
    if parent.name == "reports":
        # docx_path.parent 是 reports/xxx/，parent 是 reports/
        if parent != docx_path.parent:
            return str(docx_path.parent.name)
    # 回退：用文件名前缀
    return docx_path.stem.split("_")[0]


def _extract_comments(docx_path: Path) -> list[dict[str, Any]]:
    """读取 .docx 中的所有批注。

    Returns:
        [{"id": "0", "author": "xxx", "text": "批注内容", "para_id": "rId8"}, ...]
    """
    comments: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(docx_path, "r") as z:
            if "word/comments.xml" not in z.namelist():
                logger.info("  文档中无批注")
                return comments

            tree = ET.parse(z.open("word/comments.xml"))
            root = tree.getroot()

            for comment_elem in root.findall(".//w:comment", NS):
                cid = comment_elem.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}id", "")
                author = comment_elem.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}author", "")
                texts = comment_elem.findall(".//w:t", NS)
                text = "".join(t.text or "" for t in texts).strip()
                comments.append({"id": cid, "author": author, "text": text})
    except Exception as e:
        logger.warning("  读取批注失败: %s", e)
    return comments


def _get_paragraph_text(p_elem: Any, ns: dict[str, str]) -> str:
    """从 w:p XML 元素提取纯文本。"""
    texts = p_elem.findall(".//w:t", ns)
    return "".join(t.text or "" for t in texts)


def _get_paragraph_style(p_elem: Any, ns: dict[str, str]) -> int | None:
    """获取段落标题级别（H1=1, H2=2, 普通段落=None）。"""
    ppr = p_elem.find("w:pPr", ns)
    if ppr is not None:
        style = ppr.find("w:pStyle", ns)
        if style is not None:
            val = style.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", "")
            if val.startswith("Heading"):
                return int(val.replace("Heading", ""))
    return None


def _extract_chapter_comments(
    docx_path: Path,
) -> list[dict[str, Any]]:
    """将批注映射到 H1 章节，按章节分组。

    Returns:
        [
            {
                "chapter_title": "顶层设计与架构布局",
                "comments": ["批注1", "批注2"],
                "full_content": "该章节的完整 markdown 内容"
            },
            ...
        ]
    """
    # 第1步：读取批注
    comments = _extract_comments(docx_path)
    if not comments:
        return []

    comment_by_id = {c["id"]: c for c in comments}

    # 第2步：扫描文档正文，将批注关联到段落和章节
    with zipfile.ZipFile(docx_path, "r") as z:
        tree = ET.parse(z.open("word/document.xml"))
        body = tree.getroot().find("w:body", NS)

        paragraphs = body.findall(".//w:p", NS) if body is not None else []

    # 第3步：遍历段落，跟踪当前章节
    chapter_comments: dict[str, dict[str, Any]] = {}
    # 建立 commentRangeStart 到 paragraph 的映射
    para_comment_map: dict[str, list[str]] = {}  # comment_id -> chapter? (我们先收集)
    current_chapter = "（标题前段落）"
    chapter_content: dict[str, list[str]] = {}
    chapter_para_count: dict[str, int] = {}

    for p in paragraphs:
        text = _get_paragraph_text(p, NS)
        style_level = _get_paragraph_style(p, NS)

        if style_level == 1 and text:
            current_chapter = text
            if current_chapter not in chapter_comments:
                chapter_comments[current_chapter] = {"comments": [], "paras": []}
                chapter_content[current_chapter] = []
                chapter_para_count[current_chapter] = 0

        if current_chapter not in chapter_content:
            chapter_content[current_chapter] = []
            chapter_para_count[current_chapter] = 0

        # 检查此段落是否有批注
        comment_refs = p.findall(".//w:commentRangeStart", NS)
        for cr in comment_refs:
            cid = cr.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}id", "")
            if cid in comment_by_id:
                if current_chapter not in chapter_comments:
                    chapter_comments[current_chapter] = {"comments": [], "paras": []}
                chapter_comments[current_chapter]["comments"].append(
                    comment_by_id[cid]["text"]
                )

        # 另一种批注格式：commentReference
        comment_refs2 = p.findall(".//w:commentReference", NS)
        for cr in comment_refs2:
            cid = cr.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}id", "")
            if cid in comment_by_id:
                if current_chapter not in chapter_comments:
                    chapter_comments[current_chapter] = {"comments": [], "paras": []}
                chapter_comments[current_chapter]["comments"].append(
                    comment_by_id[cid]["text"]
                )

        # 记录章节内容（用于章节全文提取）
        if text:
            chapter_content[current_chapter].append(text)
            chapter_para_count[current_chapter] = (
                chapter_para_count.get(current_chapter, 0) + 1
            )

    # 第4步：过滤出有批注的章节，提取全文
    result = []
    for chapter_title, data in chapter_comments.items():
        if not data["comments"]:
            continue
        full = "\n".join(chapter_content.get(chapter_title, []))
        result.append({
            "chapter_title": chapter_title,
            "comments": list(set(data["comments"])),
            "full_content": full,
        })

    return result


def _load_report_goal(topic: str) -> dict[str, Any] | None:
    """从 reports/<topic>/report_goal.json 加载目标定义。

    查找策略：
      1. 精确匹配：reports/<topic>/report_goal.json
      2. 模糊匹配：reports/ 下含 topic 名词的子目录
      3. 回退到 reports/ 下第一个可用的 report_goal.json
    """
    if not REPORTS_DIR.exists():
        logger.warning("  reports/ 目录不存在")
        return None

    # 1. 精确匹配
    for subdir in REPORTS_DIR.iterdir():
        if not subdir.is_dir():
            continue
        if subdir.name == topic:
            goal_file = subdir / "report_goal.json"
            if goal_file.exists():
                try:
                    with open(goal_file, encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            break  # 精确匹配到目录但文件不存在，不再搜索

    # 2. 模糊匹配：主题包含关系
    for subdir in REPORTS_DIR.iterdir():
        if not subdir.is_dir():
            continue
        if topic in subdir.name or subdir.name in topic:
            goal_file = subdir / "report_goal.json"
            if goal_file.exists():
                try:
                    with open(goal_file, encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass

    # 3. 回退：仅当只有一个报告目录时才取第一个
    all_dirs = [d for d in REPORTS_DIR.iterdir() if d.is_dir()]
    if len(all_dirs) == 1:
        goal_file = all_dirs[0] / "report_goal.json"
        if goal_file.exists():
            try:
                with open(goal_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

    logger.warning("  report_goal.json 未找到（topic=%s）", topic)
    return None


def _build_revision_prompt(
    chapter_title: str,
    full_content: str,
    comments: list[str],
    writing_role: dict[str, Any] | None,
) -> str:
    """构建修订 prompt。"""
    parts: list[str] = []

    # 写作角色
    if writing_role:
        role = writing_role.get("role", "")
        tone = writing_role.get("tone", "")
        voice = writing_role.get("voice", "")
        conventions = writing_role.get("output_conventions", "")
        parts.append("## 写作规范")
        if role:
            parts.append(f"角色：{role}")
        if tone:
            parts.append(f"语调：{tone}")
        if voice:
            parts.append(f"叙述方式：{voice}")
        if conventions:
            parts.append(f"输出规范：{conventions}")
        parts.append("")

    # 用户反馈
    comment_lines = "\n".join(f"- {c}" for c in comments)
    parts.append(f"## 用户反馈意见（请逐一落实）\n{comment_lines}\n")

    # 原文
    parts.append(f"## 当前章节内容（标题：「{chapter_title}」）\n{full_content}\n")

    # 修订要求
    parts.append(
        "## 修订要求\n"
        "请根据用户反馈意见修订上述章节。\n"
        "1. 保持原文的标题层级和整体结构\n"
        "2. 落实所有反馈意见\n"
        "3. 保持与其他章节一致的写作风格和语调\n"
        "4. 不要添加新的 H1 标题（章节标题保持不变）\n"
        "5. 输出完整的修订后章节内容（不要只输出修改部分）\n"
    )

    return "\n".join(parts)


def _extract_chapter_content(
    full_content: str,
    title: str,
) -> str:
    """从完整 markdown 中提取指定 H1 章节的内容。"""
    lines = full_content.split("\n")
    result: list[str] = []
    in_target = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("##"):
            h1_title = stripped.lstrip("# ").strip()
            if h1_title == title:
                in_target = True
                result.append(line)
                continue
            elif in_target:
                # 遇到下一个 H1，停止
                break

        if in_target:
            result.append(line)

    return "\n".join(result)


def _replace_chapter_content(
    full_content: str,
    old_title: str,
    new_content: str,
) -> str:
    """将旧章节内容替换为新内容。"""
    # 找到旧章节的开始和结束
    lines = full_content.split("\n")
    start_idx = -1
    end_idx = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            h1_title = stripped.lstrip("# ").strip()
            if h1_title == old_title:
                start_idx = i
            elif start_idx >= 0:
                end_idx = i
                break

    if start_idx < 0:
        logger.warning("  未找到章节 ' %s'", old_title)
        return full_content

    # 替换
    before = "\n".join(lines[:start_idx])
    after = "\n".join(lines[end_idx:])
    
    # 清理新内容的头部行（可能包含 H1 标题，保留它）
    new_lines = new_content.strip().split("\n")
    
    return before.strip() + "\n" + new_content.strip() + "\n" + after.strip()


def main() -> None:
    """主函数。"""
    if len(sys.argv) < 2:
        print("用法: python3 feedback_revision.py <报告.docx>")
        print("示例: python3 feedback_revision.py reports/xxx/xxx.docx")
        sys.exit(1)

    docx_path = Path(sys.argv[1])
    if not docx_path.exists():
        logger.error("文件不存在: %s", docx_path)
        sys.exit(1)

    # ── Step 1: 读取批注 ──
    logger.info("📖 读取批注: %s", docx_path)
    chapter_data = _extract_chapter_comments(docx_path)

    if not chapter_data:
        logger.info("  未找到批注。请在 Word 中选中文本 → 右键 → 新建批注。")
        return

    logger.info("  发现 %d 个章节有批注", len(chapter_data))
    for cd in chapter_data:
        logger.info("    📝 「%s」: %d 条反馈", cd["chapter_title"][:30], len(cd["comments"]))
        for c in cd["comments"]:
            logger.info("      → %s", c[:60])

    # ── Step 2: 加载写作角色 ──
    topic = _find_topic_from_path(docx_path)
    goal = _load_report_goal(topic)
    writing_role = goal.get("writing_role") if goal else None
    if writing_role:
        logger.info("  已加载写作角色: %s", writing_role.get("role", "")[:30])

    # ── Step 3: 逐章修订 ──
    sys.path.insert(0, str(PROJECT_ROOT))
    from ai_report.adapters.ai_client import call_llm

    # 读取当前 .docx 的全文 markdown（用 python-docx 读段落拼接）
    import docx
    doc = docx.Document(str(docx_path))
    doc_lines: list[str] = []
    for p in doc.paragraphs:
        style = p.style.name if p.style else ""
        text = p.text.strip()
        if not text:
            doc_lines.append("")
            continue
        if style.startswith("Heading"):
            level = style.replace("Heading ", "")
            doc_lines.append(f"{'#' * int(level)} {text}")
        else:
            doc_lines.append(text)
    full_md = "\n".join(doc_lines)

    revisions: list[tuple[str, str]] = []

    for cd in chapter_data:
        logger.info("  🔧 修订: 「%s」", cd["chapter_title"][:30])

        # 提取该章节的完整内容
        chapter_md = _extract_chapter_content(full_md, cd["chapter_title"])

        prompt = _build_revision_prompt(
            cd["chapter_title"],
            chapter_md,
            cd["comments"],
            writing_role,
        )

        try:
            revised = call_llm(prompt, max_iterations=1, temperature=0.3)
            if not revised or len(revised.strip()) < 50:
                logger.warning("    修订失败（输出过短），保留原文")
                continue

            revisions.append((cd["chapter_title"], revised.strip()))
            logger.info("    ✅ 修订完成: %d chars", len(revised))
        except Exception as e:
            logger.warning("    修订失败: %s，保留原文", e)

    if not revisions:
        logger.info("  无章节被修订")
        return

    # ── Step 4: 合并修订到全文 ──
    for title, new_content in revisions:
        full_md = _replace_chapter_content(full_md, title, new_content)

    # ── Step 5: 输出新 .docx（保留原图表） ──
    output_path = docx_path.with_stem(docx_path.stem + "_revised")

    # 查找原文档的图表图片
    chart_images: list[tuple[int, Path]] = []
    charts_dir = docx_path.parent / "charts"
    if charts_dir.exists():
        import re as _re_chart
        for f in sorted(charts_dir.iterdir()):
            m = _re_chart.match(r"chart_(\d+)_.+\.png", f.name)
            if m:
                chart_images.append((int(m.group(1)), f))
        logger.info("  找到 %d 个图表图片", len(chart_images))

    # 重新导出为 docx
    from ai_report.export.docx_exporter import export_to_docx

    # 重新提取标题
    first_line = full_md.split("\n")[0]
    report_title = first_line.lstrip("# ").strip() if first_line.startswith("#") else topic

    # 从原文档复制 chart_images（如果有）
    export_to_docx(
        title=report_title,
        full_content=full_md,
        chart_images=chart_images,
        output_path=output_path,
    )
    logger.info("  ✅ 输出: %s", output_path)
    logger.info("  🎯 完成！请在 Word 中打开查看修订效果。")


if __name__ == "__main__":
    main()
