"""测试 DOCX 导出功能"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ai_report.export.docx_exporter import export_to_docx

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
SAMPLE_MD = FIXTURES_DIR / "sample_report.md"

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _read_docx_text(docx_path: Path) -> str:
    with zipfile.ZipFile(docx_path, "r") as z:
        tree = ET.parse(z.open("word/document.xml"))
    texts = []
    for t in tree.getroot().findall(".//w:t", NS):
        if t.text:
            texts.append(t.text)
    return "\n".join(texts)


def _count_headings(docx_path: Path) -> dict[int, int]:
    with zipfile.ZipFile(docx_path, "r") as z:
        tree = ET.parse(z.open("word/document.xml"))
    counts: dict[int, int] = {}
    for p in tree.getroot().findall(".//w:p", NS):
        ppr = p.find("w:pPr", NS)
        if ppr is not None:
            style = ppr.find("w:pStyle", NS)
            if style is not None:
                val = style.get(f"{{{NS['w']}}}val", "")
                if val.startswith("Heading"):
                    try:
                        level = int(val.replace("Heading", "").strip())
                        counts[level] = counts.get(level, 0) + 1
                    except ValueError:
                        pass
    return counts


def _count_tables(docx_path: Path) -> int:
    with zipfile.ZipFile(docx_path, "r") as z:
        tree = ET.parse(z.open("word/document.xml"))
    return len(tree.getroot().findall(".//w:tbl", NS))


def _count_page_breaks(docx_path: Path) -> int:
    with zipfile.ZipFile(docx_path, "r") as z:
        xml = z.read("word/document.xml").decode("utf-8")
    return xml.count("w:br w:type=\"page\"") + xml.count("<w:br w:type=\"page\"/>")


@pytest.mark.integration
class TestCleanInlineMarkers:
    """测试行内标记被正确解析（通过 _add_formatted_run 间接验证）。

    历史上有 _clean_inline_markers 函数，已被 _add_formatted_run 取代，
    这里改为通过 export_to_docx 后检查文本内容验证。
    """

    def test_italic_stripped(self, tmp_path):
        """*斜体* 标记应被剥除，斜体文本保留。"""
        output = tmp_path / "out.docx"
        export_to_docx(
            title="测试", full_content="*斜体*",
            chart_images=[], output_path=output,
        )
        text = _read_docx_text(output)
        assert "斜体" in text
        assert "*" not in text

    def test_code_stripped(self, tmp_path):
        """`code` 标记应被剥除，代码文本保留。"""
        output = tmp_path / "out.docx"
        export_to_docx(
            title="测试", full_content="`代码`",
            chart_images=[], output_path=output,
        )
        text = _read_docx_text(output)
        assert "代码" in text
        assert "`" not in text

    def test_strikethrough_stripped(self, tmp_path):
        """~~text~~ 标记应被剥除，文本保留。"""
        output = tmp_path / "out.docx"
        export_to_docx(
            title="测试", full_content="~~删除~~",
            chart_images=[], output_path=output,
        )
        text = _read_docx_text(output)
        assert "删除" in text
        assert "~~" not in text

    def test_link_text_kept_only(self, tmp_path):
        """[text](url) 应保留 text，丢掉 url。"""
        output = tmp_path / "out.docx"
        export_to_docx(
            title="测试", full_content="[链接](https://example.com)",
            chart_images=[], output_path=output,
        )
        text = _read_docx_text(output)
        assert "链接" in text
        assert "https://example.com" not in text

    def test_bold_stripped(self, tmp_path):
        """**bold** 标记应被剥除，加粗文本保留。"""
        output = tmp_path / "out.docx"
        export_to_docx(
            title="测试", full_content="**保留**",
            chart_images=[], output_path=output,
        )
        text = _read_docx_text(output)
        assert "保留" in text
        assert "**" not in text

    def test_mixed_markers(self, tmp_path):
        """混合标记应都正确解析。"""
        output = tmp_path / "out.docx"
        export_to_docx(
            title="测试", full_content="普通 *斜体* `代码` 文本",
            chart_images=[], output_path=output,
        )
        text = _read_docx_text(output)
        assert "斜体" in text
        assert "代码" in text
        assert "*" not in text
        assert "`" not in text


@pytest.mark.integration
class TestExportToDocxBasic:
    """基础导出功能。"""

    def test_basic_export_creates_file(self, tmp_path):
        output = tmp_path / "output.docx"
        content = SAMPLE_MD.read_text(encoding="utf-8")
        result = export_to_docx(
            title="测试报告",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        assert result.exists()
        assert result.stat().st_size > 0

    def test_export_contains_title(self, tmp_path):
        output = tmp_path / "output.docx"
        content = SAMPLE_MD.read_text(encoding="utf-8")
        export_to_docx(
            title="测试报告标题",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "测试报告标题" in text

    def test_export_headings_levels(self, tmp_path):
        output = tmp_path / "output.docx"
        content = SAMPLE_MD.read_text(encoding="utf-8")
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        headings = _count_headings(output)
        assert headings.get(2, 0) >= 3
        assert sum(headings.values()) >= 8

    def test_export_has_tables(self, tmp_path):
        output = tmp_path / "output.docx"
        content = SAMPLE_MD.read_text(encoding="utf-8")
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        assert _count_tables(output) >= 2

    def test_export_preserves_bold_text(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "这是 **加粗文本** 的测试"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "加粗文本" in text


@pytest.mark.integration
class TestExportSubtitleAndToc:
    """测试封面副标题和目录控制。"""

    def test_subtitle_appears_in_docx(self, tmp_path):
        output = tmp_path / "output.docx"
        export_to_docx(
            title="主标题",
            full_content="# 章节\n内容",
            chart_images=[],
            output_path=output,
            subtitle="副标题XYZ",
        )
        text = _read_docx_text(output)
        assert "副标题XYZ" in text

    def test_no_subtitle_when_not_provided(self, tmp_path):
        output = tmp_path / "output.docx"
        export_to_docx(
            title="主标题",
            full_content="# 章节\n内容",
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        # 没有副标题
        assert "副标题" not in text

    def test_toc_enabled_by_default(self, tmp_path):
        output = tmp_path / "output.docx"
        export_to_docx(
            title="测试",
            full_content="# 章节\n内容",
            chart_images=[],
            output_path=output,
        )
        # 目录字段会被插入到 docx 中
        with zipfile.ZipFile(output, "r") as z:
            xml = z.read("word/document.xml").decode("utf-8")
        assert "TOC" in xml or "目录" in xml

    def test_toc_disabled(self, tmp_path):
        output = tmp_path / "output.docx"
        export_to_docx(
            title="测试",
            full_content="# 章节\n内容",
            chart_images=[],
            output_path=output,
            toc=False,
        )
        with zipfile.ZipFile(output, "r") as z:
            xml = z.read("word/document.xml").decode("utf-8")
        # 不应包含 TOC 字段
        assert "目录" not in _read_docx_text(output) or "TOC" not in xml


@pytest.mark.integration
class TestExportHorizontalRule:
    """测试水平分隔线。"""

    @pytest.mark.parametrize("hr", ["---", "----", "******", "____", "***", "___"])
    def test_hr_creates_page_break(self, tmp_path, hr):
        output = tmp_path / "output.docx"
        content = f"# 章节1\n内容A\n\n{hr}\n\n# 章节2\n内容B"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        assert _count_page_breaks(output) >= 1


@pytest.mark.integration
class TestExportOrderedList:
    """测试有序列表。"""

    def test_ordered_list_prefix_stripped(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "1. 第一项\n2. 第二项\n3. 第三项"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        # 数字前缀应被剥掉，只剩内容
        assert "第一项" in text
        assert "第二项" in text
        assert "第三项" in text
        # 不应出现 "1. 第一项" 这种字面量
        assert "1. 第一项" not in text

    def test_chinese_style_ordered_list(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "1、第一项\n2、第二项"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "第一项" in text
        assert "第二项" in text


@pytest.mark.integration
class TestExportInlineMarkers:
    """测试行内标记清理。"""

    def test_italic_cleaned(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "这是 *斜体文本* 的测试"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "斜体文本" in text
        # 不应出现字面量 *
        assert "*斜体文本*" not in text

    def test_code_cleaned(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "使用 `print()` 函数"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "print()" in text
        assert "`print()`" not in text

    def test_link_text_kept(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "参考 [文档](https://example.com) 了解详情"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "文档" in text
        assert "https://example.com" not in text


@pytest.mark.integration
class TestExportCodeBlock:
    """测试代码块支持。"""

    def test_python_code_block(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "示例代码：\n\n```python\ndef hello():\n    print('hello world')\n\nhello()\n```\n\n结束。"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "def hello():" in text
        assert "print('hello world')" in text
        assert "hello()" in text
        assert "python" in text  # 语言标记

    def test_plain_code_block_no_lang(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "```\nplain text\n```"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "plain text" in text

    def test_mermaid_code_block_notice(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "```mermaid\ngraph TD\n  A --> B\n```"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "Mermaid" in text

    def test_multiline_code_block(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "```bash\n# comment\necho line1\necho line2\necho line3\n```"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "echo line1" in text
        assert "echo line2" in text
        assert "echo line3" in text
        assert "comment" in text


@pytest.mark.integration
class TestExportImageMarkdown:
    """测试 Markdown 图片语法支持。"""

    def test_image_markdown_parsed(self, tmp_path):
        # 最小 1x1 PNG (base64)
        import base64
        png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        img_path = tmp_path / "test.png"
        img_path.write_bytes(base64.b64decode(png_b64))

        output = tmp_path / "output.docx"
        content = f"# 标题\n\n![测试图]({img_path})\n\n正文结束"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "测试图" in text  # caption 存在
        assert "正文结束" in text
        # 图片嵌入到 docx 中（media 目录）
        with zipfile.ZipFile(output, "r") as z:
            media_files = [n for n in z.namelist() if "media" in n]
            assert len(media_files) >= 1

    def test_missing_image_falls_back_to_text(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "![不存在的图](/tmp/nonexistent.png)"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        # 图片不存在时，应该当普通文本处理
        text = _read_docx_text(output)
        assert "不存在的图" in text


@pytest.mark.integration
class TestMermaidExtraction:
    """测试 Mermaid 代码块提取和替换。"""

    def test_extract_single_block(self):
        from export_docx import _extract_mermaid_blocks, _detect_mermaid_type
        md = """
# 标题

```mermaid
graph TD
  A --> B
```

正文
""".strip()
        blocks = _extract_mermaid_blocks(md)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "flowchart"
        assert "graph TD" in blocks[0]["code"]
        assert "A --> B" in blocks[0]["code"]

    def test_extract_multiple_blocks(self):
        from export_docx import _extract_mermaid_blocks
        md = """
```mermaid
pie title 占比
  "A": 40
  "B": 60
```

正文

```mermaid
flowchart LR
  X --> Y
```
""".strip()
        blocks = _extract_mermaid_blocks(md)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "pie"
        assert blocks[1]["type"] == "flowchart"

    def test_detect_mermaid_type(self):
        from export_docx import _detect_mermaid_type
        assert _detect_mermaid_type("graph TD\n  A --> B") == "flowchart"
        assert _detect_mermaid_type("flowchart LR\n  X --> Y") == "flowchart"
        assert _detect_mermaid_type("pie title Test\n  A: 10") == "pie"
        assert _detect_mermaid_type("sequenceDiagram\n  A->>B: hello") == "sequence"
        assert _detect_mermaid_type("classDiagram\n  class A") == "class"
        assert _detect_mermaid_type("erDiagram\n  CUSTOMER ||--o{ ORDER") == "er"
        assert _detect_mermaid_type("gantt\n  title Test") == "gantt"
        assert _detect_mermaid_type("mindmap\n  root((根))") == "mindmap"
        assert _detect_mermaid_type("journey\n  title My Day") == "journey"
        assert _detect_mermaid_type("unknown xyz") == "diagram"

    def test_replace_mermaid_with_images(self, tmp_path):
        from export_docx import replace_mermaid_with_images
        md = """
# 章节1

```mermaid
graph TD
  A --> B
```

正文

```mermaid
pie title 占比
  "A": 40
```
""".strip()

        # 创建两张 fake 图片
        img0 = tmp_path / "m0.png"
        img0.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        img1 = tmp_path / "m1.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        result = replace_mermaid_with_images(md, [img0, img1])

        # 应包含图片标记
        assert "![" in result
        assert "m0.png" in result
        assert "m1.png" in result
        # 不应再有 mermaid 代码块
        assert "```mermaid" not in result
        # 其他内容保留
        assert "章节1" in result
        assert "正文" in result

    def test_replace_mermaid_failed_keeps_code(self, tmp_path):
        from export_docx import replace_mermaid_with_images
        md = """
```mermaid
graph TD
  A --> B
```
""".strip()

        # None 表示生成失败
        result = replace_mermaid_with_images(md, [None])

        # 失败时保留原代码块
        assert "```mermaid" in result
        assert "graph TD" in result


@pytest.mark.integration
class TestExportEdgeCases:
    """边缘情况。"""

    def test_empty_content(self, tmp_path):
        output = tmp_path / "output.docx"
        export_to_docx(
            title="空报告",
            full_content="",
            chart_images=[],
            output_path=output,
        )
        assert output.exists()
        assert output.stat().st_size > 0

    def test_only_headings(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "# H1标题\n## H2标题\n### H3标题"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "H1标题" in text
        assert "H2标题" in text
        assert "H3标题" in text

    def test_only_table(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "| A | B |\n|---|---|\n| 1 | 2 |"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        assert _count_tables(output) >= 1

    def test_only_blockquote(self, tmp_path):
        output = tmp_path / "output.docx"
        content = "> 引用内容"
        export_to_docx(
            title="测试",
            full_content=content,
            chart_images=[],
            output_path=output,
        )
        text = _read_docx_text(output)
        assert "引用内容" in text

    def test_chart_map_json_format(self, tmp_path):
        """测试 chart_map.json 格式正确。"""
        chart_map_path = FIXTURES_DIR / "chart_map.json"
        chart_map = json.loads(chart_map_path.read_text(encoding="utf-8"))
        assert "系统架构" in chart_map
        assert chart_map["系统架构"] == "架构图.png"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
