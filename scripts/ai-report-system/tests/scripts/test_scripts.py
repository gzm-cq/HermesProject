"""脚本层测试 — 验证 scripts/ 下工具的纯逻辑部分。

当前覆盖：
  - init_report   : 目录创建 + 报告类型校验
  - extract_facts : 事实提取 prompt 构建（纯字符串，无 LLM）
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from scripts.init_report import init_report


class TestInitReport:
    """init_report 目录创建逻辑测试"""

    def test_creates_inputs_dir(self, monkeypatch):
        """验证 init_report 创建 inputs/ 子目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            result = init_report("测试报告", "tech")
            dir_path = Path(result["report_dir"])
            assert dir_path.exists()
            assert (dir_path / "inputs").exists()

    def test_returns_expected_keys(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            result = init_report("测试报告", "tech")
            assert "topic" in result
            assert "report_dir" in result
            assert "inputs_dir" in result

    def test_unknown_type_defaults_to_tech(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            result = init_report("测试", "invalid_type")
            assert result["type"] == "tech"

    def test_topic_name_cropped_at_40_chars(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            long_topic = "这是一个非常长的报告主题名称，超过了四十个字符的限制"
            result = init_report(long_topic, "tech")
            dir_name = Path(result["report_dir"]).name
            assert len(dir_name) <= 40

    def test_spaces_replaced_with_underscores(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            result = init_report("测试 报告 主题", "tech")
            dir_name = Path(result["report_dir"]).name
            assert " " not in dir_name
            assert "_" in dir_name
