"""generate_recommendations 单元测试。"""

from __future__ import annotations

from flywheel_health_report.recommendations import generate_recommendations


# ========== generate_recommendations ==========

class TestGenerateRecommendations:
    """测试推荐生成函数。"""

    def _empty_metrics(self) -> tuple:
        """构造空的 metrics 元组，模拟 no_data 状态。"""
        router_m = {"status": "no_data"}
        skill_m = {"status": "no_data"}
        kn_m = {"status": "no_data", "dim_summary": {}}
        kt_m = {"status": "no_data"}
        cluster_m = {"status": "no_data"}
        token_m = {"status": "no_data"}
        sag_contr_m = {"status": "no_data"}
        skill_usage_m = {"status": "no_data"}
        error_m = {"status": "no_data", "error_count": 0, "warning_count": 0}
        return (router_m, skill_m, kn_m, kt_m, cluster_m,
                token_m, sag_contr_m, skill_usage_m, error_m)

    def test_no_data_returns_empty(self) -> None:
        """全部 no_data 时应返回空列表（无推荐）。"""
        (router_m, skill_m, kn_m, kt_m, cluster_m,
         token_m, sag_contr_m, skill_usage_m, error_m) = self._empty_metrics()
        recs = generate_recommendations(
            router_m, skill_m, kn_m, kt_m, cluster_m,
            [], {}, [], [],
            token_m, sag_contr_m, skill_usage_m, error_m,
        )
        assert recs == []

    def test_high_error_rate_recommendation(self) -> None:
        """高 error_rate 应生成 Router 优化建议。"""
        (router_m, skill_m, kn_m, kt_m, cluster_m,
         token_m, sag_contr_m, skill_usage_m, error_m) = self._empty_metrics()
        router_m.update({
            "status": "ok",
            "total_masks": 10,
            "full_off_pct": 5.0,
            "empty_rate": 10.0,
            "error_rate": 25.0,
            "avg_latency_ms": 200,
            "avg_score": 0.7,
            "sag_on_pct": 50.0,
            "sag_total_kept": 5,
            "sag_avg_latency_ms": 300,
        })
        recs = generate_recommendations(
            router_m, skill_m, kn_m, kt_m, cluster_m,
            [], {}, [], [],
            token_m, sag_contr_m, skill_usage_m, error_m,
        )
        # 高 error_rate 应触发 Router 相关建议
        router_recs = [r for r in recs if r.get("flywheel") == "Router"]
        assert len(router_recs) >= 1

    def test_token_exhaustion_recommendation(self) -> None:
        """Token 耗尽率高应生成优化建议。"""
        (router_m, skill_m, kn_m, kt_m, cluster_m,
         token_m, sag_contr_m, skill_usage_m, error_m) = self._empty_metrics()
        token_m.update({
            "status": "ok",
            "total_budget": 8000,
            "event_count": 10,
            "exhaust_pct": 60.0,
            "exhaust_count": 6,
            "total_stats": {"avg": 6000, "p50": 6000, "p90": 7500, "max": 7800},
            "hs_stats": {"avg": 3000, "p50": 3000, "p90": 3500, "max": 3600},
            "kt_stats": {"avg": 1500, "p50": 1500, "p90": 1800, "max": 1900},
            "skill_stats": {"avg": 200, "p50": 200, "p90": 300, "max": 300},
        })
        recs = generate_recommendations(
            router_m, skill_m, kn_m, kt_m, cluster_m,
            [], {}, [], [],
            token_m, sag_contr_m, skill_usage_m, error_m,
        )
        # 应有 Token 相关建议
        token_recs = [r for r in recs if "Token" in r.get("flywheel", "") or "token" in r.get("desc", "").lower()]
        assert len(token_recs) >= 1

    def test_sag_zero_merge_recommendation(self) -> None:
        """SAG merge 零结果率高应生成优化建议。"""
        (router_m, skill_m, kn_m, kt_m, cluster_m,
         token_m, sag_contr_m, skill_usage_m, error_m) = self._empty_metrics()
        # SAG 推荐规则要求 sag_on_pct > 10（来自 router_m）
        router_m.update({
            "status": "ok",
            "total_masks": 10,
            "sag_on_pct": 50.0,  # > 10，满足推荐触发条件
        })
        sag_contr_m.update({
            "status": "ok",
            "recall_count": 10,
            "merge_count": 10,
            "merge_zero_pct": 70.0,  # 超阈值 50%
            "recall_stats": {"avg": 3, "total": 30},
            "merge_stats": {"avg": 0.5, "total": 5},
        })
        recs = generate_recommendations(
            router_m, skill_m, kn_m, kt_m, cluster_m,
            [], {}, [], [],
            token_m, sag_contr_m, skill_usage_m, error_m,
        )
        sag_recs = [r for r in recs if "SAG" in r.get("flywheel", "") or "sag" in r.get("desc", "").lower()]
        assert len(sag_recs) >= 1

    def test_kn_low_score_dims_merged(self) -> None:
        """多个低分维度应合并为一条推荐，并标注样本量不足的维度。"""
        (router_m, skill_m, kn_m, kt_m, cluster_m,
         token_m, sag_contr_m, skill_usage_m, error_m) = self._empty_metrics()
        kn_m.update({
            "status": "ok",
            "unknown_dim_pct": 10.0,
            "dim_summary": {
                "entity": {"count": 10, "avg_score": 0.4386},   # 样本 < 20，应标注
                "debug": {"count": 23, "avg_score": 0.4445},
            },
        })
        recs = generate_recommendations(
            router_m, skill_m, kn_m, kt_m, cluster_m,
            [], {}, [], [],
            token_m, sag_contr_m, skill_usage_m, error_m,
        )
        kn_recs = [r for r in recs if r.get("flywheel") == "KN"]
        dim_recs = [r for r in kn_recs if "均分" in r.get("desc", "")]
        # 应只有 1 条合并的低分维度推荐
        assert len(dim_recs) == 1, f"应合并为 1 条，实际 {len(dim_recs)}: {dim_recs}"
        desc = dim_recs[0]["desc"]
        # 合并描述应包含两个维度
        assert "entity" in desc and "debug" in desc
        # 样本不足的 entity 应被标注（debug 样本充足不应标注）
        assert "样本量偏小" in desc
        assert "entity样本量偏小" in desc or "entity, " in desc.split("样本量偏小")[0] or "其中entity" in desc
        assert "debug" not in desc.split("样本量偏小")[0].split("其中")[-1]

    def test_kn_single_low_score_dim(self) -> None:
        """单个低分维度应生成单条推荐并带样本量。"""
        (router_m, skill_m, kn_m, kt_m, cluster_m,
         token_m, sag_contr_m, skill_usage_m, error_m) = self._empty_metrics()
        kn_m.update({
            "status": "ok",
            "unknown_dim_pct": 10.0,
            "dim_summary": {
                "entity": {"count": 30, "avg_score": 0.4386},
            },
        })
        recs = generate_recommendations(
            router_m, skill_m, kn_m, kt_m, cluster_m,
            [], {}, [], [],
            token_m, sag_contr_m, skill_usage_m, error_m,
        )
        kn_recs = [r for r in recs if r.get("flywheel") == "KN"]
        dim_recs = [r for r in kn_recs if "均分" in r.get("desc", "")]
        assert len(dim_recs) == 1
        assert "entity" in dim_recs[0]["desc"]
        assert "30 条查询" in dim_recs[0]["desc"]
