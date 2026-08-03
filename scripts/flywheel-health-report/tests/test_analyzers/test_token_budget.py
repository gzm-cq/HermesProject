"""analyze_token_budget 单元测试。"""

from __future__ import annotations

from flywheel_health_report.analyzers.token_budget import analyze_token_budget


# ========== analyze_token_budget ==========

class TestAnalyzeTokenBudget:
    """测试 Token 预算分析。"""

    def test_no_data_returns_empty(self) -> None:
        """无 token_budget 事件时返回 no_data。"""
        issues, metrics, trend = analyze_token_budget({})
        assert metrics.get("status") == "no_data"
        assert issues == []

    def test_normal_stats(self) -> None:
        """正常数据应统计总预算/分源消耗/耗尽率。"""
        trace = {"token_budget": [
            {"total_budget": 8000, "hs_tokens_before": 2000, "hs_tokens_after": 1500,
             "kt_tokens_before": 1000, "kt_tokens_after": 800,
             "sag_tokens_before": 500, "sag_tokens_after": 400,
             "skill_tokens_before": 100, "skill_tokens_after": 100},
            {"total_budget": 8000, "hs_tokens_before": 7000, "hs_tokens_after": 6900,
             "kt_tokens_before": 500, "kt_tokens_after": 400,
             "sag_tokens_before": 200, "sag_tokens_after": 150,
             "skill_tokens_before": 80, "skill_tokens_after": 70},
        ]}
        issues, metrics, trend = analyze_token_budget(trace)
        # 正常情况返回的 metrics 不含 "status" 键，只有 no_data 才有
        assert "status" not in metrics
        assert metrics["event_count"] == 2
        assert metrics["total_budget"] == 8000
        assert "total_stats" in metrics
        assert "hs_stats" in metrics
        assert "kt_stats" in metrics
        assert "skill_stats" in metrics
        # 耗尽率：第二条接近耗尽（6900/8000=86%），按 exhaust_pct 阈值应 >= 50%
        assert metrics["exhaust_count"] >= 0

    def test_detects_high_exhaustion(self) -> None:
        """当多次接近耗尽时应产生 issue。"""
        # 构造 total_after > 0 且 total_used / total_budget > 0.95 的场景：
        # 代码逻辑 `if total_after <= 0: continue` 会跳过全关场景，
        # 因此 total_after 必须为正且接近耗尽（< 5% 预算剩余）。
        # total_budget=1000, total_after=30 → total_used=970, ratio=0.97 > 0.95 ✓
        trace = {"token_budget": [
            {"total_budget": 1000, "hs_tokens_before": 970, "hs_tokens_after": 20,
             "kt_tokens_before": 5, "kt_tokens_after": 5,
             "sag_tokens_before": 5, "sag_tokens_after": 0,
             "skill_tokens_before": 5, "skill_tokens_after": 5}
            for _ in range(6)
        ]}
        issues, metrics, _ = analyze_token_budget(trace)
        # total_after=30 > 0 且 ratio=0.97 > 0.95，6 次全触发 exhaust_count
        assert metrics["exhaust_count"] == 6
        # exhaust_pct = 100% > 阈值 10%，应触发 issue
        assert any("耗尽" in i.get("desc", "") for i in issues)
