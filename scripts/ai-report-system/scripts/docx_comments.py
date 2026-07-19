#!/usr/bin/env python3
"""docx_comments.py — 从 Word 文档提取批注，按章节分组

零路径依赖，任意位置可用。提取批注并输出 JSON，方便对接 LLM 修订流程。

用法:
    python3 docx_comments.py report.docx              # 打印批注摘要
    python3 docx_comments.py report.docx -o out.json  # 输出 JSON 文件
    python3 docx_comments.py report.docx --full       # 包含章节完整内容

输出 JSON 格式:
    [
      {
        "chapter_title": "章节标题",
        "comments": ["批注1", "批注2"],
        "comment_count": 2,
        "full_content": "章节完整文本（--full 时包含）"
      },
      ...
    ]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("docx_comments")

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
}


def extract_comments(docx_path: Path) -> list[dict[str, Any]]:
    """读取 .docx 中的所有批注。"""
    comments: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(docx_path, "r") as z:
            if "word/comments.xml" not in z.namelist():
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
        logger.warning("读取批注失败: %s", e)
    return comments


def _get_paragraph_text(p_elem: Any, ns: dict[str, str]) -> str:
    texts = p_elem.findall(".//w:t", ns)
    return "".join(t.text or "" for t in texts)


def _get_heading_level(p_elem: Any, ns: dict[str, str]) -> int | None:
    ppr = p_elem.find("w:pPr", ns)
    if ppr is not None:
        style = ppr.find("w:pStyle", ns)
        if style is not None:
            val = style.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", "")
            if val.startswith("Heading"):
                try:
                    return int(val.replace("Heading", "").strip())
                except ValueError:
                    return None
    return None


def extract_chapter_comments(
    docx_path: Path,
    include_content: bool = False,
) -> list[dict[str, Any]]:
    """将批注映射到 H1 章节，按章节分组。"""
    comments = extract_comments(docx_path)
    if not comments:
        return []

    comment_by_id = {c["id"]: c for c in comments}

    with zipfile.ZipFile(docx_path, "r") as z:
        tree = ET.parse(z.open("word/document.xml"))
        body = tree.getroot().find("w:body", NS)
        paragraphs = body.findall(".//w:p", NS) if body is not None else []

    chapter_comments: dict[str, dict[str, Any]] = {}
    chapter_content: dict[str, list[str]] = {}
    current_chapter = "（前言/标题前）"

    for p in paragraphs:
        text = _get_paragraph_text(p, NS)
        level = _get_heading_level(p, NS)

        if level == 1 and text:
            current_chapter = text
            if current_chapter not in chapter_comments:
                chapter_comments[current_chapter] = {"comments": []}
                chapter_content[current_chapter] = []

        if current_chapter not in chapter_content:
            chapter_content[current_chapter] = []
        if current_chapter not in chapter_comments:
            chapter_comments[current_chapter] = {"comments": []}

        for tag in ["w:commentRangeStart", "w:commentReference"]:
            for cr in p.findall(f".//{tag}", NS):
                cid = cr.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}id", "")
                if cid in comment_by_id:
                    comment_text = comment_by_id[cid]["text"]
                    # 用 dict.fromkeys 去重保序
                    seen = dict.fromkeys(chapter_comments[current_chapter]["comments"])
                    if comment_text not in seen:
                        chapter_comments[current_chapter]["comments"].append(comment_text)

        if text:
            chapter_content[current_chapter].append(text)

    result = []
    for chapter_title, data in chapter_comments.items():
        if not data["comments"]:
            continue
        item = {
            "chapter_title": chapter_title,
            "comments": list(data["comments"]),
            "comment_count": len(data["comments"]),
        }
        if include_content:
            item["full_content"] = "\n".join(chapter_content.get(chapter_title, []))
        result.append(item)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="从 Word 文档提取批注，按章节分组")
    parser.add_argument("input", type=Path, help="输入 .docx 文件路径")
    parser.add_argument("-o", "--output", type=Path, default=None, help="输出 JSON 文件路径")
    parser.add_argument("--full", action="store_true", help="包含章节完整文本内容")

    args = parser.parse_args()

    docx_path = args.input.resolve()
    if not docx_path.exists():
        logger.error("文件不存在: %s", docx_path)
        sys.exit(1)

    logger.info("读取批注: %s", docx_path)
    result = extract_chapter_comments(docx_path, include_content=args.full)

    if not result:
        print("未找到批注。请在 Word 中选中文本 → 右键 → 新建批注。")
        return

    print(f"\n发现 {len(result)} 个章节有批注：")
    for item in result:
        print(f"\n  📝 「{item['chapter_title']}」({item['comment_count']} 条)")
        for c in item["comments"]:
            print(f"      → {c[:80]}")

    if args.output:
        output_path = args.output.resolve()
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n✅ 已输出到: {output_path}")


if __name__ == "__main__":
    main()
