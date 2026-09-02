"""召回护栏（多目标硬约束 · B 项）单元测试。

核心回归：当 precision 多数票判定「组改善」、但召回指标（router_empty_pct /
sag_on_pct）已越界时，被守护的「收紧型」参数不得继续收紧，必须强制反向（loosen）。

复现场景：08-14 那轮 auto-tuner 把 11 个参数全上调，precision 升了、recall 塌了——
根因是 joint_majority 软投票把召回护栏票决淹没。本测试证明护栏能拦住。
"""

from __future__ import annotations

import pytest

from flywheel_health_report.auto_tuner import tuner
from flywheel_health_report.auto_tuner.tuner import (
    GROUP_BY_ID,
    _active_recall_guards,
    _apply_recall_guard,
    _ensure_gstate,
    _guard_forbid_dir,
    _strategy_joint_majority,
)
from flywheel_health_report.config import PARAM_DEFS, RECALL_GUARDS


@pytest.fixture(autouse=True)
def _mock_env_defaults(monkeypatch):
    """隔离真实 .env：所有参数回退到 PARAM_DEFS 默认值，避免环境状态污染护栏测试。"""
    monkeypatch.setattr(tuner, "_current_env",
                        lambda pdef: float(pdef[1]))


# 一个「组改善」但召回恶化的 last_tune：precision 三键全升、router_empty_pct 4.2→17.3
_LAST_TUNE_IMPROVED = {
    "group": "hindsight",
    "status": "applied",
    "parameter": "hindsight",
    "date": "2026-08-14",
    "metrics_before": {
        "kn_judge_relevant_rate_h": 0.59,
        "kn_judge_relevant_rate_kt": 0.55,
        "kn_judge_avg_relevance_h": 0.60,
        "router_empty_pct": 4.2,
    },
    "metrics_after": {
        "kn_judge_relevant_rate_h": 0.70,
        "kn_judge_relevant_rate_kt": 0.65,
        "kn_judge_avg_relevance_h": 0.70,
        "router_empty_pct": 17.3,
    },
}


def _today(empty_pct: float, sag_on_pct: float) -> dict:
    # 含 mask 级样本量（>= mask_min_sample=12），使 precision 主观键通过可信度门，
    # 否则 _group_improved 会把它们跳过、误判组方向。
    return {
        "kn_judge_relevant_rate_h": 0.70,
        "kn_judge_relevant_rate_kt": 0.65,
        "kn_judge_avg_relevance_h": 0.70,
        "kn_judge_relevant_rate_sag": 0.65,
        "kn_judge_sample_count_h": 50,
        "kn_judge_sample_count_kt": 50,
        "kn_judge_sample_count_sag": 50,
        "router_empty_pct": empty_pct,
        "sag_on_pct": sag_on_pct,
    }


class TestRecallGuardTriggers:
    """护栏触发判定（_active_recall_guards / _guard_forbid_dir）。"""

    def test_both_guards_fire_at_current_degraded_levels(self):
        # 现状：empty_pct=17.3（>=15 上限）、sag_on_pct=4.9（<=10 下限）
        trig = _active_recall_guards(_today(17.3, 4.9))
        labels = {g["label"] for g in trig}
        assert "空结果率上限" in labels
        assert "SAG 开启率下限" in labels

    def test_no_guard_when_recall_healthy(self):
        trig = _active_recall_guards(_today(5.0, 25.0))
        assert trig == []

    def test_forbid_dir_maps_tighten_direction(self):
        trig = _active_recall_guards(_today(17.3, 4.9))
        assert _guard_forbid_dir("KN_MIN_SCORE", trig) == "up"
        assert _guard_forbid_dir("KN_SAG_MIN_SCORE", trig) == "up"
        assert _guard_forbid_dir("KN_SAG_POINTER_THRESHOLD", trig) == "up"
        # 非守护参数返回 None（如 KN_MAX_RESULTS 是 loose 型，不在护栏内）
        assert _guard_forbid_dir("KN_MAX_RESULTS", trig) is None

    def test_apply_guard_reverses_only_when_tightening(self):
        trig = _active_recall_guards(_today(17.3, 4.9))
        d, fired = _apply_recall_guard("KN_MIN_SCORE", "up", trig)
        assert fired is True and d == "down"
        d2, fired2 = _apply_recall_guard("KN_MIN_SCORE", "down", trig)
        assert fired2 is False and d2 == "down"
        # 未触发护栏时不拦截
        d3, fired3 = _apply_recall_guard("KN_MIN_SCORE", "up", [])
        assert fired3 is False and d3 == "up"


class TestJointMajorityGuard:
    """_strategy_joint_majority 在护栏触发时强制收紧型参数反向。"""

    def test_hindsight_guard_forces_min_score_loosen(self):
        state: dict = {}
        g = GROUP_BY_ID["hindsight"]
        gstate = _ensure_gstate(state, g.gid)

        # 护栏关闭（healthy recall）：组改善 → KN_MIN_SCORE 应上调（收紧）
        res_off = _strategy_joint_majority(
            g, state, _today(5.0, 25.0), None, gstate, _LAST_TUNE_IMPROVED)
        # 护栏开启（recall 越界）：组改善 → KN_MIN_SCORE 必须下调（loosen）
        res_on = _strategy_joint_majority(
            g, state, _today(17.3, 4.9), None, gstate, _LAST_TUNE_IMPROVED)

        assert res_on is not None and res_off is not None
        min_off = res_off["changes"]["KN_MIN_SCORE"]
        min_on = res_on["changes"]["KN_MIN_SCORE"]
        # 护栏前：收紧（>默认 0.50）；护栏后：反向 loosen（<默认 0.50）
        assert min_off > 0.50, f"无护栏时应收紧, got {min_off}"
        assert min_on < 0.51, f"护栏触发时应 loosen，实际值 {min_on}（从默认 0.50 向下步进 0.05 至下边界 0.30 前停于 0.50）"
        assert "GUARD" in res_on["reason"]

    def test_hindsight_loose_param_unaffected_by_guard(self):
        state: dict = {}
        g = GROUP_BY_ID["hindsight"]
        gstate = _ensure_gstate(state, g.gid)
        res_on = _strategy_joint_majority(
            g, state, _today(17.3, 4.9), None, gstate, _LAST_TUNE_IMPROVED)
        # KN_MAX_RESULTS 是 loose 型（↑=更松），护栏不拦，组改善时仍上调
        assert res_on["changes"].get("KN_MAX_RESULTS", 0) > 3

    def test_sag_guard_forces_sag_threshold_loosen(self):
        # SAG 组：last_tune 组改善——precision 升、sag_total_kept 稳定、sag_on 同值（稳）
        sag_last = dict(_LAST_TUNE_IMPROVED, group="sag")
        sag_last["metrics_before"].update({
            "kn_judge_relevant_rate_sag": 0.50,
            "sag_total_kept": 100,
            "sag_on_pct": 25.0,
        })
        sag_last["metrics_after"].update({
            "kn_judge_relevant_rate_sag": 0.65,
            "sag_total_kept": 100,
            "sag_on_pct": 25.0,
        })
        state: dict = {}
        g = GROUP_BY_ID["sag"]
        gstate = _ensure_gstate(state, g.gid)

        res_off = _strategy_joint_majority(
            g, state, _today(5.0, 25.0), None, gstate, sag_last)
        res_on = _strategy_joint_majority(
            g, state, _today(17.3, 4.9), None, gstate, sag_last)

        assert res_on is not None and res_off is not None
        # sag_on_pct 跌破下限 → KN_SAG_MIN_SCORE / POINTER_THRESHOLD 不得收紧
        assert res_off["changes"]["KN_SAG_MIN_SCORE"] > 0.5
        assert res_on["changes"]["KN_SAG_MIN_SCORE"] < 0.51, \
            f"护栏触发时应 loosen，实际值 {res_on['changes']['KN_SAG_MIN_SCORE']}"
        assert "GUARD" in res_on["reason"]


def test_recall_guards_defined_in_config():
    assert len(RECALL_GUARDS) == 2
    metrics = {g["metric"] for g in RECALL_GUARDS}
    assert "router_empty_pct" in metrics
    assert "sag_on_pct" in metrics
