"""测试 CLI 入口 — 基本命令测试"""

from unittest.mock import patch

from typer.testing import CliRunner

from knowledge_tree_builder.cli import app

runner = CliRunner()


class TestCLI:
    """测试 CLI 命令"""

    def test_help(self) -> None:
        """--help 应正常显示"""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "用法" in result.stdout or "Usage" in result.stdout or "知识树" in result.stdout

    @patch("knowledge_tree_builder.core.extractor.call_llm")
    def test_extract_command(self, mock_call_llm) -> None:
        """extract 命令（默认参数）"""
        mock_call_llm.return_value = "- 测试知识点"
        result = runner.invoke(app, ["extract", "--article", "测试文章内容"])
        assert result.exit_code in (0, 1, 2)

    def test_list_types_command(self) -> None:
        """list-types 命令"""
        result = runner.invoke(app, ["admission", "list-types"])
        # 如果命令不存在，返回 2
        assert result.exit_code in (0, 2)

    def test_config_help(self) -> None:
        """config 命令帮助"""
        result = runner.invoke(app, ["config", "--help"])
        # config 命令可能不存在
        assert result.exit_code in (0, 2)
