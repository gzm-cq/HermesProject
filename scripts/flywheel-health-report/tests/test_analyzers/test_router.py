"""analyze_router 单元测试。"""

from __future__ import annotations

from pathlib import Path

from flywheel_health_report.analyzers.router import analyze_router


# ========== analyze_router (with new error/kt_fallback metrics) ==========

class TestAnalyzeRouter:
    """测试 Router 分析含新增的 error/kt_fallback 指标。"""

    def test_no_data_when_no_masks(self) -> None:
        """无 router_mask 事件时返回 no_data。"""
        trace = {"router_mask": [], "recall_success": [], "recall_empty_results": [],
                 "recall_timeout": [], "recall_error": [], "hindsight_fail_kt_fallback": [],
                 "recall_sag": [], "multi_hop_expand": []}
        issues, metrics, _ = analyze_router(trace, Path("/tmp/nonexist"))
        assert metrics.get("status") == "no_data"

    def test_total_recall_includes_errors(self, tmp_path: Path) -> None:
        """total_recall 必须包含 error 次数，不能再漏算。"""
        trace = {
            "router_mask": [{"mask": {"h": True, "kt": True, "s": True, "sag": False}}],
            "recall_success": [{"latency_ms": 100, "score_stats": {"avg": 0.8}}],
            "recall_empty_results": [{}],
            "recall_timeout": [{}],
            "recall_error": [{"error": "RuntimeError"}, {"error": "Timeout"}],
            "hindsight_fail_kt_fallback": [{"kt_count": 1}],
            "recall_sag": [],
            "multi_hop_expand": [],
        }
        issues, metrics, _ = analyze_router(trace, tmp_path)
        # success(1) + empty(1) + timeout(1) + error(2) = 5
        assert metrics["success_count"] == 1
        assert metrics["empty_count"] == 1
        assert metrics["timeout_count"] == 1
        assert metrics["error_count"] == 2
        assert metrics["kt_fallback_count"] == 1
        # success_rate = 1/5 * 100 = 20
        assert metrics["success_rate"] == 20.0
        # error_rate = 2/5 * 100 = 40
        assert metrics["error_rate"] == 40.0

    def test_high_error_rate_triggers_issue(self, tmp_path: Path) -> None:
        """高 error_rate 应触发 P1 issue。"""
        trace = {
            "router_mask": [{"mask": {"h": True, "kt": True, "s": True, "sag": False}}
                            for _ in range(10)],
            "recall_success": [{"latency_ms": 100, "score_stats": {"avg": 0.8}} for _ in range(5)],
            "recall_empty_results": [{} for _ in range(2)],
            "recall_timeout": [{}],
            "recall_error": [{"error": "x"} for _ in range(2)],  # error_rate = 2/10 = 20%
            "hindsight_fail_kt_fallback": [],
            "recall_sag": [],
            "multi_hop_expand": [],
        }
        issues, metrics, _ = analyze_router(trace, tmp_path)
        assert metrics["error_rate"] == 20.0
        # 阈值 error_rate_high_pct=5%，20% > 5% 应触发
        error_issues = [i for i in issues if "错误率" in i.get("desc", "")]
        assert len(error_issues) >= 1
        assert error_issues[0]["severity"] == "P1"

    def test_router_decision_quality_metrics(self, tmp_path: Path) -> None:
        """router_mask 事件带 confidence/fallback_reason 时应统计决策质量指标。"""
        trace = {
            "router_mask": [
                # 一次正常决策（高置信度）
                {"mask": {"h": True, "kt": False, "s": False, "sag": False},
                 "confidence": 0.85, "fallback_reason": "success", "is_fallback": False},
                # 一次超时 fallback（is_fallback=True）
                {"mask": {"h": True, "kt": True, "s": True, "sag": False},
                 "confidence": 0.0, "fallback_reason": "api_timeout", "is_fallback": True},
                # 一次低置信度强制 fallback（confidence<0.3，is_fallback 因 reason=success 为 False）
                {"mask": {"h": True, "kt": True, "s": True, "sag": False},
                 "confidence": 0.2, "fallback_reason": "success", "is_fallback": False},
            ],
            "recall_success": [{"latency_ms": 100, "score_stats": {"avg": 0.8}}],
            "recall_empty_results": [],
            "recall_timeout": [],
            "recall_error": [],
            "hindsight_fail_kt_fallback": [],
            "recall_sag": [],
            "multi_hop_expand": [],
        }
        issues, metrics, _ = analyze_router(trace, tmp_path)
        # 平均置信度 = (0.85 + 0.0 + 0.2) / 3 ≈ 0.35
        assert metrics["router_confidence_avg"] == round((0.85 + 0.0 + 0.2) / 3, 4)
        # 低置信度数 = 2（0.0 与 0.2 均 < 0.3），低置信度率 = 2/3 ≈ 66.7
        assert metrics["router_confidence_low_pct"] == 66.7
        # fallback 仅 1 次（api_timeout），fallback 率 = 1/3 ≈ 33.3
        assert metrics["router_fallback_total"] == 1
        assert metrics["router_fallback_pct"] == 33.3
        assert metrics["router_fallback_reasons"] == {"api_timeout": 1}
        # fallback 率 > 5% 应触发 P1 issue
        fb_issues = [i for i in issues if "fallback 率" in i.get("desc", "")]
        assert len(fb_issues) == 1
        assert fb_issues[0]["severity"] == "P1"

    def test_router_decision_quality_no_meta(self, tmp_path: Path) -> None:
        """router_mask 事件无 confidence/fallback meta（旧格式）时指标应安全降级。"""
        trace = {
            "router_mask": [{"mask": {"h": True, "kt": True, "s": True, "sag": False}}],
            "recall_success": [{"latency_ms": 100, "score_stats": {"avg": 0.8}}],
            "recall_empty_results": [],
            "recall_timeout": [],
            "recall_error": [],
            "hindsight_fail_kt_fallback": [],
            "recall_sag": [],
            "multi_hop_expand": [],
        }
        _, metrics, _ = analyze_router(trace, tmp_path)
        assert metrics["router_confidence_avg"] is None
        assert metrics["router_confidence_low_pct"] == 0
        assert metrics["router_fallback_total"] == 0
        assert metrics["router_fallback_pct"] == 0
        assert metrics["router_fallback_reasons"] == {}
