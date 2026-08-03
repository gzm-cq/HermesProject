"""analyze_global_errors 单元测试。"""

from __future__ import annotations

from pathlib import Path

from flywheel_health_report.analyzers.global_errors import analyze_global_errors


# ========== analyze_global_errors ==========

class TestAnalyzeGlobalErrors:
    """测试全局错误日志分析。"""

    def test_no_file_returns_no_data(self, tmp_path: Path) -> None:
        """errors.log 不存在时返回 no_data。"""
        issues, metrics, _ = analyze_global_errors(tmp_path / "missing.log", "2026-07-10")
        assert metrics.get("status") == "no_data"

    def test_parses_errors(self, tmp_path: Path) -> None:
        """解析 ERROR/WARNING 行并统计。"""
        log_path = tmp_path / "errors.log"
        # 格式必须匹配正则: (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}[,\d]* (\w+) ([\w\.]+):
        # 即 "date time LEVEL module:" — module 后必须有冒号
        log_path.write_text(
            "2026-07-10 10:00:00 ERROR knowledge_navigation.core.hooks: recall failed: timeout\n"
            "2026-07-10 10:01:00 WARNING knowledge_navigation.core.router: mask fallback used\n"
            "2026-07-10 10:02:00 ERROR knowledge_navigation.core.sag: SAG request failed\n"
            "2026-07-11 10:00:00 ERROR should.be.filtered: this is next day\n",
            encoding="utf-8",
        )
        issues, metrics, _ = analyze_global_errors(log_path, "2026-07-10")
        # 正常情况返回的 metrics 不含 "status" 键
        assert "status" not in metrics
        assert metrics["error_count"] == 2
        assert metrics["warning_count"] == 1
        # 应识别出 top 模块（top_modules 是 list）
        assert "top_modules" in metrics
        assert isinstance(metrics["top_modules"], list)
        assert len(metrics["top_modules"]) > 0

    def test_restart_cascade_noise_filtered(self, tmp_path: Path) -> None:
        """重启级联噪音应被过滤，不计入 ERROR 统计。"""
        log_path = tmp_path / "errors.log"
        log_path.write_text(
            # asyncio 未关闭连接 - 应被过滤
            "2026-07-10 07:37:00 ERROR asyncio: Unclosed client session\n"
            "2026-07-10 07:37:01 ERROR asyncio: Unclosed connector\n"
            # asyncio Task exception（实际日志不含 ConnectionClosedOK）- 应被过滤
            "2026-07-10 07:37:02 ERROR asyncio: Task exception was never retrieved\n"
            # Lark WS 正常关闭（实际格式 sent 1000 (OK)）- 应被过滤
            "2026-07-10 07:38:00 ERROR lark: receive message loop exit, err: sent 1000 (OK); then received 1000 (OK) bye\n"
            # Weixin 限流 - 应被过滤
            "2026-07-10 07:39:00 ERROR gateway.platforms.weixin: rate limited\n"
            # MCP SSE reader 错误（实际格式 Error in sse_reader）- 应被过滤
            "2026-07-10 07:40:00 ERROR mcp.client.sse: Error in sse_reader\n"
            # Hindsight daemon 未就绪 - 应被过滤
            "2026-07-10 07:41:00 ERROR knowledge_navigation: Hindsight daemon not ready\n"
            # 真正的 ERROR - 不应被过滤
            "2026-07-10 10:00:00 ERROR knowledge_tree.extract: LLM extraction failed\n"
            "2026-07-10 11:00:00 ERROR knowledge_navigation.skill: skill_matcher timeout\n",
            encoding="utf-8",
        )
        issues, metrics, _ = analyze_global_errors(log_path, "2026-07-10")
        assert metrics["filtered_errors"] == 7
        assert metrics["error_count"] == 2
