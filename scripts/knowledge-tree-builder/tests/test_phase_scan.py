"""phase/scan.py 单元测试"""

from __future__ import annotations

import pytest
from pathlib import Path

from knowledge_tree_builder.phase.scan import (
    _extract_title,
    _is_excluded_file,
    scan_input_dir,
)


class TestScanInputDir:
    """scan_input_dir 扫描测试"""

    def test_normal_directory(self, tmp_path: Path) -> None:
        (tmp_path / "article1.md").write_text("# 文章一\n这是内容，超过五十个字符的正文内容用于测试扫描功能。", encoding="utf-8")
        (tmp_path / "article2.md").write_text("# 文章二\n这是第二篇文章的正文内容，同样超过五十个字符用于测试。", encoding="utf-8")
        result = scan_input_dir(str(tmp_path))
        assert len(result["admitted_files"]) == 2
        assert result["empty_dir"] is False

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = scan_input_dir(str(tmp_path))
        assert result["admitted_files"] == []
        assert result["empty_dir"] is True

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        result = scan_input_dir(str(tmp_path / "nonexistent"))
        assert result["admitted_files"] == []
        assert result["empty_dir"] is True

    def test_excluded_filenames(self, tmp_path: Path) -> None:
        (tmp_path / "index.md").write_text("# Index\n" + "x" * 100, encoding="utf-8")
        (tmp_path / "moc.md").write_text("# MOC\n" + "x" * 100, encoding="utf-8")
        (tmp_path / "good.md").write_text("# Good\n" + "x" * 100, encoding="utf-8")
        result = scan_input_dir(str(tmp_path))
        assert len(result["admitted_files"]) == 1
        assert result["admitted_files"][0]["title"] == "Good"
        assert len(result["skipped"]) == 2

    def test_excluded_directories(self, tmp_path: Path) -> None:
        bak_dir = tmp_path / "_bak"
        bak_dir.mkdir()
        (bak_dir / "old.md").write_text("# Old\n" + "x" * 100, encoding="utf-8")
        (tmp_path / "new.md").write_text("# New\n" + "x" * 100, encoding="utf-8")
        result = scan_input_dir(str(tmp_path))
        assert len(result["admitted_files"]) == 1
        assert result["admitted_files"][0]["title"] == "New"

    def test_excluded_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n" + b"x" * 100)
        (tmp_path / "data.log").write_text("log entry " + "x" * 100, encoding="utf-8")
        (tmp_path / "good.txt").write_text("This is a good text file with enough content to pass the size check.", encoding="utf-8")
        result = scan_input_dir(str(tmp_path))
        assert len(result["admitted_files"]) == 1
        assert result["admitted_files"][0]["path"].endswith("good.txt")

    def test_empty_files_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "empty.md").write_text("", encoding="utf-8")
        (tmp_path / "tiny.md").write_text("hi", encoding="utf-8")
        (tmp_path / "good.md").write_text("# Good Article\n" + "x" * 100, encoding="utf-8")
        result = scan_input_dir(str(tmp_path))
        assert len(result["admitted_files"]) == 1

    def test_nested_directories(self, tmp_path: Path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.md").write_text("# Nested Article\n" + "x" * 100, encoding="utf-8")
        (tmp_path / "root.md").write_text("# Root Article\n" + "x" * 100, encoding="utf-8")
        result = scan_input_dir(str(tmp_path))
        assert len(result["admitted_files"]) == 2

    def test_mixed_scenario(self, tmp_path: Path) -> None:
        (tmp_path / "good.md").write_text("# Good\n" + "x" * 100, encoding="utf-8")
        (tmp_path / "index.md").write_text("# Index\n" + "x" * 100, encoding="utf-8")
        bak = tmp_path / "_archive"
        bak.mkdir()
        (bak / "old.md").write_text("# Old\n" + "x" * 100, encoding="utf-8")
        (tmp_path / "pic.jpg").write_bytes(b"\xff\xd8\xff" + b"x" * 100)
        result = scan_input_dir(str(tmp_path))
        assert len(result["admitted_files"]) == 1
        assert result["admitted_files"][0]["title"] == "Good"

    def test_skipped_reasons_populated(self, tmp_path: Path) -> None:
        (tmp_path / "index.md").write_text("# Index\n" + "x" * 100, encoding="utf-8")
        result = scan_input_dir(str(tmp_path))
        assert len(result["skipped"]) == 1
        assert "排除文件名" in result["skipped"][0]["reason"]


class TestExtractTitle:
    """_extract_title 标题提取测试"""

    def test_markdown_heading(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("# My Article Title\n\nContent here.", encoding="utf-8")
        assert _extract_title(f) == "My Article Title"

    def test_no_heading_uses_filename(self, tmp_path: Path) -> None:
        f = tmp_path / "my_article.md"
        f.write_text("", encoding="utf-8")
        assert _extract_title(f) == "my_article"

    def test_non_heading_first_line(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("Just some text without heading", encoding="utf-8")
        assert _extract_title(f) == "Just some text without heading"
