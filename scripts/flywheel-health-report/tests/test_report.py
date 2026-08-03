"""format_7day_trend 单元测试。"""

from __future__ import annotations

from pathlib import Path

from flywheel_health_report.report import format_7day_trend
from flywheel_health_report.parsers import append_daily_summary


# ========== format_7day_trend ==========

class TestFormat7DayTrend:
    """测试 7 天趋势表格式化。"""

    def test_insufficient_data_returns_message(self, tmp_path: Path) -> None:
        """少于 2 天时返回提示。"""
        result = format_7day_trend(tmp_path)
        assert len(result) == 1
        assert "不足" in result[0]

    def test_table_has_header_and_separator(self, tmp_path: Path) -> None:
        """有 2 天数据时返回表头 + 分隔行 + 数据行。"""
        append_daily_summary(tmp_path, {"date": "2026-07-09"})
        append_daily_summary(tmp_path, {"date": "2026-07-10"})
        result = format_7day_trend(tmp_path)
        assert len(result) >= 3  # 表头 + 分隔 + 至少 1 数据行
        assert "日期" in result[0]
        assert "---" in result[1]
