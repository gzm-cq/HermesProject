"""analyze_sag_contribution 单元测试。"""

from __future__ import annotations

from flywheel_health_report.analyzers.sag import analyze_sag_contribution


# ========== analyze_sag_contribution ==========

class TestAnalyzeSagContribution:
    """测试 SAG 贡献分析。"""

    def test_no_data_returns_empty(self) -> None:
        """无 sag 事件时返回 no_data。"""
        issues, metrics, trend = analyze_sag_contribution({})
        assert metrics.get("status") == "no_data"

    def test_normal_metrics(self) -> None:
        """正常 SAG 数据应统计 recall/merge。"""
        trace = {
            "recall_sag": [
                {"count": 5, "latency_ms": 200},
                {"count": 0, "latency_ms": 150},
                {"count": 3, "latency_ms": 180},
            ],
            "sag_merge": [
                {"count": 2},
                {"count": 0},
                {"count": 1},
            ],
        }
        issues, metrics, _ = analyze_sag_contribution(trace)
        # 正常情况返回的 metrics 不含 "status" 键
        assert "status" not in metrics
        assert metrics["recall_count"] == 3
        assert metrics["merge_count"] == 3
        # 零结果率：1/3 = 33.3%
        assert "merge_zero_pct" in metrics
        assert 30 <= metrics["merge_zero_pct"] <= 35

    def test_zero_merge_triggers_warning(self) -> None:
        """SAG merge 全零时触发 issue。"""
        trace = {
            "recall_sag": [{"count": 5}, {"count": 3}, {"count": 4}],
            "sag_merge": [{"count": 0}, {"count": 0}, {"count": 0}],
        }
        issues, _, _ = analyze_sag_contribution(trace)
        # merge_zero_pct = 100%，应触发 issue
        assert any("零结果" in i.get("desc", "") or "merge" in i.get("desc", "").lower()
                   for i in issues)

    def test_error_events_not_counted_as_recall_zero(self) -> None:
        """SAG 召回异常（error 字段）不应计入 recall_zero，应单独统计。"""
        trace = {
            # 3 次召回：1 次成功(count=0) + 2 次异常(count=0, error=...)
            "recall_sag": [
                {"count": 0, "latency_ms": 100},
                {"count": 0, "latency_ms": 50, "error": "ConnectionError"},
                {"count": 0, "latency_ms": 80, "error": "Timeout"},
            ],
            "sag_merge": [],
        }
        issues, metrics, _ = analyze_sag_contribution(trace)
        # recall_success_count = 1（只有无 error 的那次）
        assert metrics["recall_success_count"] == 1
        # recall_error_count = 2
        assert metrics["recall_error_count"] == 2
        # recall_zero = 1（只基于成功召回统计）
        assert metrics["recall_zero"] == 1
        # 不应触发"全部召回为 0 section"（因为只有 1 次成功召回，不是"全部"）
        # 但 recall_zero == recall_success_count，会触发该 issue
        # 这里验证的是 error 场景被正确分离
        assert any("召回异常" in i.get("desc", "") for i in issues)
