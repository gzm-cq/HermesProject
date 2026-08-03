"""analyze_memory_cleanup 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

from flywheel_health_report.analyzers.memory_cleanup import analyze_memory_cleanup
from flywheel_health_report.parsers import _save_json


# ========== analyze_memory_cleanup ==========

class TestAnalyzeMemoryCleanup:
    """测试记忆清理分析。"""

    def test_no_dir_returns_no_data(self, tmp_path: Path) -> None:
        """目录不存在时返回 no_data。"""
        issues, metrics, _ = analyze_memory_cleanup(tmp_path / "missing", "2026-07-10")
        assert metrics.get("status") == "no_data"
        assert issues == []

    def test_no_report_files_returns_no_data(self, tmp_path: Path) -> None:
        """目录存在但无报告时返回 no_data。"""
        issues, metrics, _ = analyze_memory_cleanup(tmp_path, "2026-07-10")
        assert metrics.get("status") == "no_data"

    def test_parses_report_correctly(self, tmp_path: Path) -> None:
        """正确解析 cleanup-report JSON。"""
        report = {
            "version": "6",
            "timestamp": "2026-07-10T13:05:00+0000",
            "mode": "apply",
            "total_time_s": 120.5,
            "tokens": {"prompt": 5000, "completion": 2000},
            "sources": {
                "MEMORY.md": {
                    "total_entries": 50,
                    "phase1_merge": 3,
                    "phase1_compress": 8,
                    "phase1_hindsight": 5,
                    "phase1_remove": 10,
                    "phase1_flagged": 0,
                    "after_cleanup": {"keep": 35, "keep_chars": 35000},
                    "phase2": {"correct": 8, "corrected": 1, "keep": 1},
                },
                "USER.md": {
                    "total_entries": 20,
                    "phase1_merge": 1,
                    "phase1_compress": 2,
                    "phase1_hindsight": 0,
                    "phase1_remove": 3,
                    "phase1_flagged": 0,
                    "after_cleanup": {"keep": 15, "keep_chars": 10000},
                    "phase2": {"correct": 2, "corrected": 0, "keep": 1},
                },
            },
        }
        (tmp_path / "cleanup-report-20260710_130500.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        issues, metrics, trend = analyze_memory_cleanup(tmp_path, "2026-07-10")
        assert metrics["memory_usage_pct"] == 70.0  # 35000/50000
        assert metrics["user_usage_pct"] == 66.7    # 10000/15000
        assert metrics["total_compress"] == 10       # 8 + 2
        assert metrics["total_hindsight"] == 5       # 5 + 0
        assert metrics["total_remove"] == 13         # 10 + 3
        assert metrics["mode"] == "apply"
        assert metrics["date_matched"] is True
        # v2_correct_rate 聚合 MEMORY + USER: correct=10, total=13 (mem:8+1+1=10, user:2+0+1=3)
        assert metrics["v2_correct_rate"] == round(10 / 13 * 100, 1)
        # 首次运行无 prev，trend 应为空 dict
        assert trend == {}
        # 占用 < 90%，不应触发 issue
        assert not any("占用" in i.get("desc", "") for i in issues)

    def test_trend_with_prev_snapshot(self, tmp_path: Path) -> None:
        """有 memory_prev.json 时应生成趋势字符串。"""
        # 先写入 prev snapshot
        _save_json(tmp_path / "memory_prev.json", {
            "memory_usage_pct": 60.0,
            "user_usage_pct": 50.0,
        })
        report = {
            "version": "6",
            "timestamp": "2026-07-10T13:05:00+0000",
            "mode": "apply",
            "total_time_s": 100,
            "tokens": {"prompt": 1000, "completion": 500},
            "sources": {
                "MEMORY.md": {
                    "after_cleanup": {"keep_chars": 35000},
                    "phase2": {"correct": 0, "corrected": 0, "keep": 0},
                },
                "USER.md": {
                    "after_cleanup": {"keep_chars": 7500},
                    "phase2": {"correct": 0, "corrected": 0, "keep": 0},
                },
            },
        }
        (tmp_path / "cleanup-report-20260710_130500.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        issues, metrics, trend = analyze_memory_cleanup(tmp_path, "2026-07-10")
        # trend 应包含趋势字符串而非 raw float
        assert "MEMORY占用率" in trend
        assert "→" in trend["MEMORY占用率"]
        assert "USER占用率" in trend
        assert "→" in trend["USER占用率"]
        # MEMORY: 60% → 70%, delta = +10
        assert "+10.0%" in trend["MEMORY占用率"]

    def test_high_usage_triggers_issue(self, tmp_path: Path) -> None:
        """字符占用超过阈值时触发 P1 issue。"""
        report = {
            "version": "6",
            "timestamp": "2026-07-10T13:05:00+0000",
            "mode": "apply",
            "total_time_s": 100,
            "tokens": {"prompt": 1000, "completion": 500},
            "sources": {
                "MEMORY.md": {
                    "total_entries": 100,
                    "phase1_merge": 0,
                    "phase1_compress": 0,
                    "phase1_hindsight": 0,
                    "phase1_remove": 0,
                    "phase1_flagged": 0,
                    "after_cleanup": {"keep": 100, "keep_chars": 48000},
                    "phase2": {"correct": 0, "corrected": 0, "keep": 0},
                },
                "USER.md": {
                    "total_entries": 30,
                    "phase1_merge": 0,
                    "phase1_compress": 0,
                    "phase1_hindsight": 0,
                    "phase1_remove": 0,
                    "phase1_flagged": 0,
                    "after_cleanup": {"keep": 30, "keep_chars": 14500},
                    "phase2": {"correct": 0, "corrected": 0, "keep": 0},
                },
            },
        }
        (tmp_path / "cleanup-report-20260710_130500.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        issues, metrics, _ = analyze_memory_cleanup(tmp_path, "2026-07-10")
        # MEMORY: 48000/50000 = 96% > 90%
        assert any("MEMORY.md 字符占用" in i.get("desc", "") for i in issues)
        # USER: 14500/15000 = 96.7% > 90%
        assert any("USER.md 字符占用" in i.get("desc", "") for i in issues)
