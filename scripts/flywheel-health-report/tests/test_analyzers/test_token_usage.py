"""analyze_token_usage 单元测试（纯消耗观测，无预算语义）。"""

from __future__ import annotations

from flywheel_health_report.analyzers.token_usage import analyze_token_usage


class TestAnalyzeTokenUsage:
    """测试 Token 实际消耗观测。"""

    def test_no_data_returns_empty(self) -> None:
        """无 token_usage 事件时返回 no_data。"""
        issues, metrics, trend = analyze_token_usage({})
        assert metrics.get("status") == "no_data"
        assert issues == []
        assert trend == {}

    def test_four_source_stats(self) -> None:
        """四路 + total 各自统计 avg/max/p50/p90。"""
        trace = {"token_usage": [
            {"hs_tokens": 100, "sag_tokens": 0, "kt_tokens": 200,
             "skill_tokens": 700, "total_tokens": 1000},
            {"hs_tokens": 300, "sag_tokens": 0, "kt_tokens": 400,
             "skill_tokens": 2300, "total_tokens": 3000},
        ]}
        issues, metrics, trend = analyze_token_usage(trace)

        assert issues == [], "纯观测 analyzer 不应产生任何告警"
        assert "status" not in metrics
        assert metrics["event_count"] == 2
        for key in ("hs_stats", "sag_stats", "kt_stats", "skill_stats", "total_stats"):
            assert key in metrics
            assert set(metrics[key]) == {"avg", "max", "p50", "p90"}

        assert metrics["hs_stats"] == {"avg": 200, "max": 300, "p50": 200, "p90": 280}
        assert metrics["total_stats"]["avg"] == 2000
        assert metrics["total_stats"]["max"] == 3000
        assert metrics["grand_total_tokens"] == 4000

    def test_source_share_pct(self) -> None:
        """各路占比按总量加权计算，四路之和为 100%。"""
        trace = {"token_usage": [
            {"hs_tokens": 100, "sag_tokens": 100, "kt_tokens": 300,
             "skill_tokens": 500, "total_tokens": 1000},
        ]}
        _, metrics, _ = analyze_token_usage(trace)
        share = metrics["source_share_pct"]
        assert share == {"hs": 10.0, "sag": 10.0, "kt": 30.0, "skill": 50.0}
        assert abs(sum(share.values()) - 100.0) < 1e-6

    def test_skill_dominates_like_production(self) -> None:
        """生产实测形态：skill 路占比可达 98%。"""
        trace = {"token_usage": [
            {"hs_tokens": 72, "sag_tokens": 0, "kt_tokens": 0,
             "skill_tokens": 4266, "total_tokens": 4338},
        ]}
        _, metrics, _ = analyze_token_usage(trace)
        assert metrics["source_share_pct"]["skill"] > 95.0

    def test_sag_non_zero_counted(self) -> None:
        """sag 是独立一路，非零时必须被统计（旧 analyzer 漏了这一路）。"""
        trace = {"token_usage": [
            {"hs_tokens": 0, "sag_tokens": 125, "kt_tokens": 0,
             "skill_tokens": 0, "total_tokens": 125},
            {"hs_tokens": 0, "sag_tokens": 375, "kt_tokens": 0,
             "skill_tokens": 0, "total_tokens": 375},
        ]}
        _, metrics, _ = analyze_token_usage(trace)
        assert metrics["sag_stats"]["avg"] == 250
        assert metrics["sag_stats"]["max"] == 375
        assert metrics["source_share_pct"]["sag"] == 100.0

    def test_total_tokens_missing_falls_back_to_sum(self) -> None:
        """事件缺 total_tokens 时按四路求和兜底（兼容旧插件写入格式）。"""
        trace = {"token_usage": [
            {"hs_tokens": 72, "sag_tokens": 8, "kt_tokens": 20, "skill_tokens": 100},
        ]}
        _, metrics, _ = analyze_token_usage(trace)
        assert metrics["total_stats"]["avg"] == 200
        assert metrics["grand_total_tokens"] == 200

    def test_missing_and_null_fields_treated_as_zero(self) -> None:
        """字段缺失 / 为 null 时按 0 处理，不抛异常。"""
        trace = {"token_usage": [
            {"hs_tokens": None, "skill_tokens": 50},
            {},
        ]}
        _, metrics, _ = analyze_token_usage(trace)
        assert metrics["event_count"] == 2
        assert metrics["hs_stats"]["avg"] == 0
        assert metrics["skill_stats"]["max"] == 50
        assert metrics["source_share_pct"]["skill"] == 100.0

    def test_all_zero_events_no_division_error(self) -> None:
        """全零事件时占比为 0，不出现除零。"""
        trace = {"token_usage": [
            {"hs_tokens": 0, "sag_tokens": 0, "kt_tokens": 0,
             "skill_tokens": 0, "total_tokens": 0},
        ]}
        issues, metrics, _ = analyze_token_usage(trace)
        assert issues == []
        assert metrics["source_share_pct"] == {"hs": 0.0, "sag": 0.0, "kt": 0.0, "skill": 0.0}
        assert metrics["grand_total_tokens"] == 0
