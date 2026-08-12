"""工具函数单元测试。

直接调用模块级辅助函数（_parse_alignment_row / _add_formatted_run /
_download_image / _detect_mermaid_type），不经过 export_to_docx 全链路。
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_report.export.docx_exporter import (
    _add_formatted_run,
    _download_image,
    _parse_alignment_row,
)
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH


# ── _parse_alignment_row ────────────────────────────────


@pytest.mark.unit
class TestParseAlignmentRow:
    def test_left_align(self):
        aligns = _parse_alignment_row("|---|---|")
        assert aligns == [WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT]

    def test_center_align(self):
        aligns = _parse_alignment_row("|:---:|:---:|")
        assert aligns == [WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.CENTER]

    def test_right_align(self):
        aligns = _parse_alignment_row("|---:|---:|")
        assert aligns == [WD_ALIGN_PARAGRAPH.RIGHT, WD_ALIGN_PARAGRAPH.RIGHT]

    def test_mixed_align(self):
        aligns = _parse_alignment_row("|:---|---:|:---:|")
        assert aligns == [
            WD_ALIGN_PARAGRAPH.LEFT,
            WD_ALIGN_PARAGRAPH.RIGHT,
            WD_ALIGN_PARAGRAPH.CENTER,
        ]

    def test_with_spaces(self):
        aligns = _parse_alignment_row("| --- | ---: |")
        assert aligns == [WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.RIGHT]

    def test_empty_cell_allowed(self):
        """空单元格应允许（默认 LEFT）。"""
        aligns = _parse_alignment_row("|---||---|")
        assert aligns == [
            WD_ALIGN_PARAGRAPH.LEFT,
            WD_ALIGN_PARAGRAPH.LEFT,
            WD_ALIGN_PARAGRAPH.LEFT,
        ]

    def test_no_trailing_pipe(self):
        aligns = _parse_alignment_row("|---|---")
        assert aligns == [WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT]

    def test_data_row_returns_none(self):
        """非分隔行返回 None。"""
        assert _parse_alignment_row("|data|data|") is None

    def test_no_dash_returns_none(self):
        """无 - 或 : 时返回 None。"""
        assert _parse_alignment_row("|abc|def|") is None

    def test_not_starting_with_pipe_returns_none(self):
        """不以 | 开头时返回 None。"""
        assert _parse_alignment_row("---|---") is None

    def test_empty_string_returns_none(self):
        assert _parse_alignment_row("") is None

    def test_single_dash_min(self):
        """单 - 也合法。"""
        aligns = _parse_alignment_row("|-|-|")
        assert aligns == [WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT]

    def test_only_colons_returns_left(self):
        """只有 : 不合法（需要至少 1 个 - 或 : 配合）— 但 : 算 [-:]+，
        所以全 : 也能匹配为 LEFT（无尾 : 时）。
        """
        aligns = _parse_alignment_row("|::|::|")
        # `::` startswith ":" 且 endswith ":" → CENTER
        assert aligns == [WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.CENTER]


# ── _add_formatted_run ──────────────────────────────────


@pytest.mark.unit
class TestAddFormattedRun:
    """直接调用 _add_formatted_run 验证各标记类型。"""

    def _render(self, text: str):
        """渲染文本到 Document，返回段落对象。"""
        doc = Document()
        p = doc.add_paragraph()
        _add_formatted_run(p, text)
        return p

    def test_plain_text(self):
        p = self._render("hello world")
        assert p.text == "hello world"
        assert len(p.runs) == 1

    def test_bold_asterisk(self):
        p = self._render("**bold**")
        bold_runs = [r for r in p.runs if r.font.bold]
        assert len(bold_runs) == 1
        assert bold_runs[0].text == "bold"

    def test_bold_underscore(self):
        """__bold__ 应被识别为加粗（Bug5 修复）。"""
        p = self._render("__bold__")
        bold_runs = [r for r in p.runs if r.font.bold]
        assert len(bold_runs) == 1
        assert bold_runs[0].text == "bold"

    def test_italic_asterisk(self):
        p = self._render("*italic*")
        italic_runs = [r for r in p.runs if r.font.italic]
        assert len(italic_runs) == 1
        assert italic_runs[0].text == "italic"

    def test_italic_underscore(self):
        """_italic_ 应被识别为斜体（Bug5 修复）。"""
        p = self._render("_italic_")
        italic_runs = [r for r in p.runs if r.font.italic]
        assert len(italic_runs) == 1
        assert italic_runs[0].text == "italic"

    def test_underscore_no_false_positive_in_word(self):
        """snake_case_var 不应被误判为 italic。"""
        p = self._render("snake_case_var")
        italic_runs = [r for r in p.runs if r.font.italic]
        assert len(italic_runs) == 0
        assert p.text == "snake_case_var"

    def test_underscore_italic_with_boundary(self):
        """_italic_ 两侧有非单词字符时应被识别。"""
        p = self._render("hello _italic_ world")
        italic_runs = [r for r in p.runs if r.font.italic]
        assert len(italic_runs) == 1
        assert italic_runs[0].text == "italic"

    def test_inline_code(self):
        p = self._render("`code`")
        # 找到 Consolas 字体的 run
        code_runs = [r for r in p.runs if r.font.name == "Consolas"]
        assert len(code_runs) == 1
        assert code_runs[0].text == "code"

    def test_strikethrough(self):
        p = self._render("~~strike~~")
        strike_runs = [r for r in p.runs if r.font.strike]
        assert len(strike_runs) == 1
        assert strike_runs[0].text == "strike"

    def test_link_text_and_url(self):
        """[text](url) 应保留 text，并通过 OOXML hyperlink 关系保存 url。"""
        p = self._render("[点击](https://example.com)")
        # hyperlink 的 text 通过 OOXML 直接 append，不在 p.runs 中
        # 改为从段落 XML 中检查
        xml_str = p._element.xml
        assert "点击" in xml_str
        # hyperlink 关系应在段落 part 的 rels 中
        rels = p.part.rels
        has_hyperlink = any(
            "hyperlink" in str(rel.reltype)
            for rel in rels.values()
        )
        assert has_hyperlink
        # url 应被外部关系引用
        has_url = any(
            "https://example.com" == str(rel.target_ref)
            for rel in rels.values()
            if rel.is_external
        )
        assert has_url

    def test_html_br(self):
        p = self._render("第一行<br>第二行")
        # 应有 break 元素
        breaks = p._element.findall(
            ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}br"
        )
        assert len(breaks) == 1
        # 文本应分两个 run
        run_texts = [r.text for r in p.runs if r.text]
        assert "第一行" in run_texts
        assert "第二行" in run_texts

    def test_mixed_markers(self):
        """混合标记应都正确解析。"""
        p = self._render("**bold** and *italic* and `code`")
        bold_runs = [r for r in p.runs if r.font.bold]
        italic_runs = [r for r in p.runs if r.font.italic]
        code_runs = [r for r in p.runs if r.font.name == "Consolas"]
        assert len(bold_runs) == 1
        assert bold_runs[0].text == "bold"
        assert len(italic_runs) == 1
        assert italic_runs[0].text == "italic"
        assert len(code_runs) == 1
        assert code_runs[0].text == "code"

    def test_no_markers_plain(self):
        """无标记的纯文本应作为单个 plain run。"""
        p = self._render("just plain text")
        assert p.text == "just plain text"
        # 所有 run 应既不 bold 也不 italic
        for r in p.runs:
            assert not r.font.bold
            assert not r.font.italic

    def test_bold_with_underscore_in_content(self):
        """**bold_with_under** 内容含下划线应正常加粗。"""
        p = self._render("**bold_with_under**")
        bold_runs = [r for r in p.runs if r.font.bold]
        assert len(bold_runs) == 1
        assert bold_runs[0].text == "bold_with_under"


# ── _download_image ─────────────────────────────────────


@pytest.mark.integration
class TestDownloadImage:
    def test_invalid_url_returns_none(self, tmp_path):
        """无效 URL 应返回 None，不抛异常。"""
        result = _download_image("http://nonexistent.invalid/doom.png", tmp_path)
        assert result is None

    def test_cached_returns_existing(self, tmp_path):
        """已缓存的文件应直接返回，不再下载。"""
        url = "https://example.com/test_cached.png"
        # 预先创建缓存文件（需大于 32 字节才被当作有效缓存）
        import hashlib
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        cached = tmp_path / f"downloaded_{url_hash}.png"
        # 写入一个最小但合法大小的 PNG 文件头
        cached.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        result = _download_image(url, tmp_path)
        assert result == cached
        # 内容应未被修改
        assert cached.read_bytes() == b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    def test_cached_too_small_re_downloads(self, tmp_path):
        """缓存文件过小（<32 bytes）应被视为损坏，触发重新下载。"""
        url = "https://example.com/test_too_small.png"
        import hashlib
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        cached = tmp_path / f"downloaded_{url_hash}.png"
        cached.write_bytes(b"tiny")  # 4 bytes

        # 应当删除损坏的缓存并尝试重新下载
        result = _download_image(url, tmp_path)
        # 由于 example.com 返回 404，重新下载会失败，返回 None
        assert result is None
        # 损坏的缓存文件应已被删除
        assert not cached.exists()

    def test_url_extension_inference(self, tmp_path):
        """URL 末尾扩展名应被推断。"""
        # 通过预先缓存验证扩展名被正确使用（扩展名保留原大小写）
        url = "https://example.com/path/to/image.JPEG"
        import hashlib
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        cached = tmp_path / f"downloaded_{url_hash}.JPEG"
        cached.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)  # 大于 32 字节

        result = _download_image(url, tmp_path)
        assert result is not None
        assert result.suffix.lower() == ".jpeg"

    def test_unknown_extension_defaults_to_png(self, tmp_path):
        """无扩展名或非图片扩展名默认使用 .png。"""
        url = "https://example.com/image_no_ext"
        import hashlib
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        cached = tmp_path / f"downloaded_{url_hash}.png"
        cached.write_bytes(b"\x89PNG" + b"\x00" * 100)  # 大于 32 字节

        result = _download_image(url, tmp_path)
        assert result is not None
        assert result.suffix == ".png"

    def test_size_limit_returns_none(self, tmp_path):
        """超过 20MB 应返回 None。"""
        from ai_report.export.docx_exporter import _MAX_DOWNLOAD_BYTES

        # Mock requests.get 返回 stream 响应，不断吐 chunk 直到超过限制
        class _FakeResp:
            def __init__(self):
                self.history = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size=65536):
                # 持续返回 chunk，直到调用方主动 return None
                while True:
                    yield b"x" * chunk_size

        with patch("ai_report.export.docx_exporter._requests") as mock_req:
            mock_req.get.return_value = _FakeResp()
            mock_req.RequestException = Exception
            url = "https://example.com/huge.png"
            result = _download_image(url, tmp_path)
            assert result is None

    def test_redirect_limit(self, tmp_path):
        """重定向超过 _MAX_REDIRECTS 次应返回 None。"""
        from ai_report.export.docx_exporter import _MAX_REDIRECTS

        # Mock requests.get 返回 history 长度超限的响应
        class _FakeResp:
            def __init__(self, history_len):
                self.history = [object() for _ in range(history_len)]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size=65536):
                return iter([])

        with patch("ai_report.export.docx_exporter._requests") as mock_req:
            mock_req.get.return_value = _FakeResp(_MAX_REDIRECTS + 1)
            mock_req.RequestException = Exception
            result = _download_image("https://example.com/redirect.png", tmp_path)
            assert result is None

    def test_redirect_within_limit_allowed(self, tmp_path):
        """重定向次数等于上限（_MAX_REDIRECTS）应被允许（> 才超限）。"""
        from ai_report.export.docx_exporter import _MAX_REDIRECTS

        class _FakeResp:
            def __init__(self, history_len):
                self.history = [object() for _ in range(history_len)]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size=65536):
                # 返回一段有效数据（> 32 字节以满足缓存完整性检查）
                yield b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        with patch("ai_report.export.docx_exporter._requests") as mock_req:
            mock_req.get.return_value = _FakeResp(_MAX_REDIRECTS)
            mock_req.RequestException = Exception
            # 不应返回 None（不抛异常即视为合法）
            result = _download_image("https://example.com/ok.png", tmp_path)
            assert result is not None
            assert result.exists()


# ── _detect_mermaid_type（边界场景补充） ──────────────────


@pytest.mark.unit
class TestDetectMermaidTypeBoundary:
    def test_empty_string(self):
        from export_docx import _detect_mermaid_type
        assert _detect_mermaid_type("") == "diagram"

    def test_whitespace_only(self):
        from export_docx import _detect_mermaid_type
        assert _detect_mermaid_type("   \n\n  ") == "diagram"

    def test_case_insensitive(self):
        """关键词应大小写不敏感。"""
        from export_docx import _detect_mermaid_type
        assert _detect_mermaid_type("GRAPH TD\n  A --> B") == "flowchart"
        assert _detect_mermaid_type("Flowchart LR\n  X --> Y") == "flowchart"

    def test_first_line_with_leading_whitespace(self):
        """首行带前导空格也应识别。"""
        from export_docx import _detect_mermaid_type
        assert _detect_mermaid_type("  graph TD\n  A --> B") == "flowchart"

    def test_mixed_case_keyword(self):
        from export_docx import _detect_mermaid_type
        assert _detect_mermaid_type("SequenceDiagram\n  A->>B: hi") == "sequence"
        assert _detect_mermaid_type("PIE title Test") == "pie"

    def test_unknown_keyword(self):
        from export_docx import _detect_mermaid_type
        assert _detect_mermaid_type("someRandomText\n  foo bar") == "diagram"

    def test_state_diagram(self):
        from export_docx import _detect_mermaid_type
        assert _detect_mermaid_type("stateDiagram\n  [*] --> A") == "state"

    def test_timeline(self):
        from export_docx import _detect_mermaid_type
        assert _detect_mermaid_type("timeline\n  title History") == "timeline"
