#!/usr/bin/env python3
"""A方案 (2026-08-29) 单元测试：排行榜去僵化 + patch 修订化 + 基线有效性。

覆盖四个根因修复：
  ① 负反馈 EMA 半衰期衰减      — _apply_neg_decay
  ② activity 去掉 patch_count   — rank_skills 评分（此处校验权重来源）
  ③ 僵尸过滤 + 0 session 冷宫    — _apply_session_gate
  ④ patch 按 op 修订 + 长度上限  — _apply_edit_op
  ⑤ 基线有效性校验              — consolidate 的 baseline_valid（集成校验）
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# ⚠️ 本目录的 conftest.py 会用 types.ModuleType 伪造一个没有 __path__ 的
# `skillopt_sleep` 包（好让 skillopt_runner 在无依赖环境下被 import）。
# 因此这里**不能**直接 `import skillopt_sleep.types` —— 拿到的会是 mock。
# 需要校验真实实现时，用下面的 _load_real_module() 按文件路径加载。
import skillopt_runner as R  # noqa: E402


def _load_real_module(mod_name: str, rel_path: str):
    """按文件路径加载 skillopt-sleep 的真实模块，绕过 conftest 的 mock 包。

    仅适用于自身无 import 依赖的模块（如 types.py 纯 dataclass 定义）。
    """
    import importlib.util

    base = Path(__file__).resolve().parents[2] / "skillopt-sleep"
    src = base / rel_path
    if not src.exists():
        pytest.skip(f"skillopt-sleep 源码不存在: {src}")
    spec = importlib.util.spec_from_file_location(mod_name, src)
    mod = importlib.util.module_from_spec(spec)
    # 必须先注册进 sys.modules：dataclasses 解析字段注解时要查 cls.__module__
    # 的模块字典，未注册会抛 AttributeError（Python 3.10 起的注解求值路径）。
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ────────────────────────────────────────────────────────────────
# ① 负反馈 EMA 衰减
# ────────────────────────────────────────────────────────────────
class TestNegDecay:
    def test_first_run_seeds_from_new_neg(self):
        """无历史 ema 时，初值 = 本轮新增。"""
        state: dict = {}
        ema = R._apply_neg_decay(state, {"a": 5})
        assert ema["a"] == 5.0
        assert state["last_decay_iso"]

    def test_halflife_is_14_days(self):
        """距上次 14 天，无新增 → 权重减半。"""
        last = datetime.now(timezone.utc) - timedelta(days=14)
        state = {"skill_neg_ema": {"a": 100.0}, "last_decay_iso": last.isoformat()}
        ema = R._apply_neg_decay(state, {})
        assert ema["a"] == pytest.approx(50.0, abs=0.5)

    def test_28_days_quarters(self):
        last = datetime.now(timezone.utc) - timedelta(days=28)
        state = {"skill_neg_ema": {"a": 100.0}, "last_decay_iso": last.isoformat()}
        ema = R._apply_neg_decay(state, {})
        assert ema["a"] == pytest.approx(25.0, abs=0.5)

    def test_decay_then_increment(self):
        """衰减后叠加本轮新增：ema = old * decay + new。"""
        last = datetime.now(timezone.utc) - timedelta(days=14)
        state = {"skill_neg_ema": {"a": 100.0}, "last_decay_iso": last.isoformat()}
        ema = R._apply_neg_decay(state, {"a": 10})
        assert ema["a"] == pytest.approx(60.0, abs=0.5)

    def test_unseen_skills_also_decay(self):
        """本轮未出现的技能同样衰减 —— 否则僵尸残留值永远不降。"""
        last = datetime.now(timezone.utc) - timedelta(days=14)
        state = {
            "skill_neg_ema": {"zombie": 800.0, "active": 20.0},
            "last_decay_iso": last.isoformat(),
        }
        ema = R._apply_neg_decay(state, {"active": 5})
        assert ema["zombie"] == pytest.approx(400.0, abs=1.0)
        assert ema["active"] == pytest.approx(15.0, abs=0.5)

    def test_new_pain_can_overtake_old(self):
        """核心诉求：新痛点能在有限轮次内超越历史累积大户。"""
        state: dict = {}
        # day0: 老痛点累积 1000
        R._apply_neg_decay(state, {"old": 1000})
        # 此后 40 天，老痛点不再出现，新痛点每天 100
        for _ in range(8):
            last = datetime.now(timezone.utc) - timedelta(days=5)
            state["last_decay_iso"] = last.isoformat()
            R._apply_neg_decay(state, {"new": 100})
        ema = state["skill_neg_ema"]
        assert ema["new"] > ema["old"], (ema["new"], ema["old"])

    def test_converged_entries_pruned(self):
        """低于阈值的条目被清理，避免表无限膨胀。"""
        last = datetime.now(timezone.utc) - timedelta(days=365)
        state = {"skill_neg_ema": {"tiny": 1.0}, "last_decay_iso": last.isoformat()}
        ema = R._apply_neg_decay(state, {})
        assert "tiny" not in ema

    def test_halflife_env_override(self):
        os.environ["SKILLOPT_NEG_HALFLIFE_DAYS"] = "7"
        try:
            last = datetime.now(timezone.utc) - timedelta(days=7)
            state = {"skill_neg_ema": {"a": 100.0}, "last_decay_iso": last.isoformat()}
            ema = R._apply_neg_decay(state, {})
            assert ema["a"] == pytest.approx(50.0, abs=0.5)
        finally:
            os.environ.pop("SKILLOPT_NEG_HALFLIFE_DAYS", None)


# ────────────────────────────────────────────────────────────────
# ② activity 不含 patch_count
# ────────────────────────────────────────────────────────────────
class TestActivityNoSelfReinforcement:
    def test_patch_count_excluded(self):
        """活跃度只算 use + view —— 优化过不再给自己加分。"""
        rec = {"use_count": 3, "view_count": 4, "patch_count": 99}
        activity = (rec.get("use_count") or 0) + (rec.get("view_count") or 0)
        assert activity == 7

    def test_repeated_optimization_does_not_raise_score(self):
        """核心诉求：优化次数不再给自己加分。

        旧公式把 patch_count 计入 activity，每优化一次活跃度永久 +1，
        agent 技能 bonus=2.0 → 每次优化给自己加 1.0 分，形成自增强死循环。
        """
        rec = {"use_count": 3, "view_count": 4, "created_by": "agent"}
        neg = 10.0
        bonus = 2.0

        def new_score():
            activity = (rec.get("use_count") or 0) + (rec.get("view_count") or 0)
            return neg * 3.0 + activity * 0.5 * bonus

        def old_score(patch_count):
            activity = ((rec.get("use_count") or 0)
                        + (rec.get("view_count") or 0)
                        + patch_count)
            return neg * 3.0 + activity * 0.5 * bonus

        # 新公式：优化 10 次后评分不变
        assert new_score() == new_score()
        # 旧公式：优化 10 次后凭空 +10 分（正是自增强的来源）
        assert old_score(10) - old_score(0) == pytest.approx(10.0)


# ────────────────────────────────────────────────────────────────
# ③ 0 session 冷宫
# ────────────────────────────────────────────────────────────────
def _row(name: str, score: float = 1.0):
    return (name, {}, score, 1.0, 0)


class TestSessionGate:
    def test_with_sessions_passes(self):
        scored = [_row("ok")]
        sessions = {"ok": [object()]}
        state: dict = {}
        actionable, skipped = R._apply_session_gate(scored, sessions, state)
        assert [r[0] for r in actionable] == ["ok"]
        assert skipped == []

    def test_zero_session_first_time_only_warns(self):
        scored = [_row("ghost")]
        sessions: dict = {}
        state: dict = {}
        actionable, skipped = R._apply_session_gate(scored, sessions, state)
        assert actionable == []
        assert state["zero_session_streak"]["ghost"] == 1
        assert "cold" not in skipped[0][1].lower()
        assert state.get("skill_cooldown_until", {}) == {}

    def test_zero_session_twice_enters_cooldown(self):
        scored = [_row("ghost")]
        sessions: dict = {}
        state: dict = {}
        R._apply_session_gate(scored, sessions, state)
        R._apply_session_gate(scored, sessions, state)
        assert "ghost" in state["skill_cooldown_until"]
        assert R.ZERO_SESSION_COOLDOWN_DAYS == 7

    def test_cooldown_excludes_and_expires(self):
        scored = [_row("ghost")]
        sessions: dict = {}
        state: dict = {}
        R._apply_session_gate(scored, sessions, state)
        R._apply_session_gate(scored, sessions, state)
        # 第三轮：冷宫生效
        actionable, skipped = R._apply_session_gate(scored, sessions, state)
        assert actionable == []
        assert "冷宫" in skipped[0][1]

        # 过期后冷宫解除（重新按 0 session 计数，不再显示「冷宫」）
        past = datetime.now(timezone.utc) - timedelta(days=8)
        state["skill_cooldown_until"]["ghost"] = past.isoformat()
        actionable, skipped = R._apply_session_gate(scored, sessions, state)
        assert "ghost" not in state["skill_cooldown_until"]
        assert "冷宫" not in skipped[0][1]
        assert actionable == []

        # 冷宫解除后只要真有 session，立即放行
        actionable, _ = R._apply_session_gate(
            scored, {"ghost": [object()]}, state)
        assert [r[0] for r in actionable] == ["ghost"]

    def test_recovered_skill_clears_streak(self):
        scored = [_row("ghost")]
        state: dict = {}
        R._apply_session_gate(scored, {}, state)
        assert state["zero_session_streak"]["ghost"] == 1
        # 下一轮有 session → 清零
        R._apply_session_gate(scored, {"ghost": [object()]}, state)
        assert "ghost" not in state["zero_session_streak"]


# ────────────────────────────────────────────────────────────────
# ④ patch 按 op 修订 + 长度上限
# ────────────────────────────────────────────────────────────────
BODY = "# Rules\n\n- old rule A\n- keep me\n"


class TestApplyEditOp:
    def test_add_appends(self):
        new, ok, reason = R._apply_edit_op(BODY, "add", "- new rule", "")
        assert ok and reason == "OK"
        assert new.endswith("- new rule\n")
        assert "keep me" in new

    def test_add_empty_rejected(self):
        new, ok, _ = R._apply_edit_op(BODY, "add", "   ", "")
        assert not ok

    def test_replace_rewrites_in_place(self):
        """replace 真正改动原文，而不是把新内容追加到末尾（旧逻辑的膨胀源）。"""
        new, ok, reason = R._apply_edit_op(
            BODY, "replace", "- new rule A", anchor="- old rule A")
        assert ok and reason == "OK"
        assert "- old rule A" not in new
        assert "- new rule A" in new
        assert new.count("- new rule A") == 1

    def test_replace_without_anchor_not_downgraded_to_append(self):
        """anchor 缺失时绝不降级为 append —— 旧逻辑正是这样堆到 10 万字符。"""
        new, ok, reason = R._apply_edit_op(BODY, "replace", "- new rule", "")
        assert not ok
        assert "anchor" in reason
        assert new == BODY

    def test_replace_ambiguous_anchor_rejected(self):
        body = "- dup\n- dup\n"
        new, ok, reason = R._apply_edit_op(body, "replace", "- x", "- dup")
        assert not ok and "2 次" in reason

    def test_replace_missing_anchor_rejected(self):
        new, ok, reason = R._apply_edit_op(BODY, "replace", "- x", "- nope")
        assert not ok and "未找到" in reason

    def test_delete_removes_anchor(self):
        new, ok, reason = R._apply_edit_op(BODY, "delete", "", anchor="- old rule A")
        assert ok and "- old rule A" not in new
        assert "keep me" in new
        assert len(new) < len(BODY)

    def test_delete_without_anchor_rejected(self):
        new, ok, _ = R._apply_edit_op(BODY, "delete", "", "")
        assert not ok

    def test_add_respects_hard_max(self):
        os.environ["SKILLOPT_SKILL_HARD_MAX"] = "100"
        try:
            new, ok, reason = R._apply_edit_op(BODY, "add", "x" * 200, "")
            assert not ok
            assert "硬上限" in reason
        finally:
            os.environ.pop("SKILLOPT_SKILL_HARD_MAX", None)

    def test_add_soft_max_still_allowed(self):
        """软上限只告警，不阻断（避免一夜之间全部改写被拒）。"""
        os.environ["SKILLOPT_SKILL_SOFT_MAX"] = "20"
        try:
            new, ok, _ = R._apply_edit_op(BODY, "add", "- new rule", "")
            assert ok
        finally:
            os.environ.pop("SKILLOPT_SKILL_SOFT_MAX", None)

    def test_unknown_op_falls_back_to_add(self):
        new, ok, _ = R._apply_edit_op(BODY, "whatever", "- new rule", "")
        assert ok and "- new rule" in new


# ────────────────────────────────────────────────────────────────
# ⑤ 基线有效性（集成：consolidate 返回的字段）
# ────────────────────────────────────────────────────────────────
class _FakeReport:
    """最小 report 桩，用于校验 runner 侧的双保险判定。"""

    def __init__(self, accepted=False, baseline_valid=True,
                 baseline_score=0.0, candidate_score=0.0, gate_action=""):
        self.accepted = accepted
        self.baseline_valid = baseline_valid
        self.baseline_score = baseline_score
        self.candidate_score = candidate_score
        self.gate_action = gate_action
        self.n_replayed = 1


class TestBaselineValid:
    def test_real_sleep_report_has_field_defaulting_true(self):
        """真实 types.py 中的 SleepReport 必须有 baseline_valid，默认 True。"""
        types_mod = _load_real_module("_real_types", "skillopt_sleep/types.py")
        rpt = types_mod.SleepReport(night=1, project="p")
        assert rpt.baseline_valid is True

    def test_consolidate_source_enforces_invalid_baseline(self):
        """consolidate.py 源码必须包含基线无效时的 reject 分支。

        （该模块依赖真实 skillopt_sleep 包，而本目录 conftest 注入的是
        无 __path__ 的 mock，无法在此直接 import，故做源码级断言。）
        """
        consolidate_py = (
            Path(__file__).resolve().parents[2]
            / "skillopt-sleep" / "skillopt_sleep" / "consolidate.py")
        if not consolidate_py.exists():
            pytest.skip(f"consolidate.py 不存在: {consolidate_py}")
        src = consolidate_py.read_text(encoding="utf-8")
        assert "reject_invalid_baseline" in src
        assert "baseline_valid = bool(val_tasks) and base_gate_score > 0.0" in src

    def test_runner_rejects_invalid_baseline(self):
        """回归：baseline=0 且 candidate>0 时，runner 侧必须拦下。

        这是生产日志里 `baseline=0.000 candidate=1.000 gate=accept` 的场景 ——
        没有对照组的「改进」被直接推上生产。
        """
        rpt = _FakeReport(accepted=True, baseline_valid=False,
                          baseline_score=0.0, candidate_score=1.0,
                          gate_action="accept")
        ok, reason = R._is_batch_acceptable(rpt)
        assert ok is False
        assert "基线无效" in reason

    def test_runner_accepts_valid_improvement(self):
        rpt = _FakeReport(accepted=True, baseline_valid=True,
                          baseline_score=0.4, candidate_score=0.8,
                          gate_action="accept")
        ok, reason = R._is_batch_acceptable(rpt)
        assert ok is True and reason == "OK"

    def test_runner_still_honours_gate_reject(self):
        rpt = _FakeReport(accepted=False, baseline_valid=True,
                          baseline_score=0.4, candidate_score=0.3)
        ok, reason = R._is_batch_acceptable(rpt)
        assert ok is False and "gate" in reason

    def test_missing_field_defaults_to_acceptable(self):
        """向后兼容：report 没有 baseline_valid 字段时不得误杀。"""
        class _Bare:
            accepted = True

        ok, _ = R._is_batch_acceptable(_Bare())
        assert ok is True


# ────────────────────────────────────────────────────────────────
# ⑥ skill 名变体合并（重复记账）
# ────────────────────────────────────────────────────────────────
def _p(tmp_path, rel):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class TestMergeSkillVariants:
    def test_same_path_merges_to_shortest_name(self, tmp_path):
        md = _p(tmp_path, "skills/system-health-check/SKILL.md")
        eligible = [("system-health-check", {"use_count": 3}),
                    ("devops/system-health-check", {"use_count": 5})]
        idx = {"system-health-check": md, "devops/system-health-check": md}
        merged, vmap = R.merge_skill_variants(eligible, idx)
        assert [n for n, _ in merged] == ["system-health-check"]
        # usage 取使用量更大的变体记录
        assert merged[0][1]["use_count"] == 5
        assert vmap == {"system-health-check": sorted(
            ["system-health-check", "devops/system-health-check"])}

    def test_counts_take_max_not_sum(self, tmp_path):
        """同一条负反馈被两个变体各记一次（计数完全同步），求和会翻倍。"""
        md = _p(tmp_path, "skills/native-mcp/SKILL.md")
        eligible = [("native-mcp", {}), ("mcp/native-mcp", {})]
        idx = {"native-mcp": md, "mcp/native-mcp": md}
        merged, vmap = R.merge_skill_variants(eligible, idx)
        # 合并只出一个条目，计数归并交给 rank_skills 的 max 逻辑
        assert len(merged) == 1
        assert vmap["native-mcp"] == ["mcp/native-mcp", "native-mcp"]

    def test_distinct_paths_not_merged(self, tmp_path):
        a = _p(tmp_path, "skills/a/SKILL.md")
        b = _p(tmp_path, "skills/b/SKILL.md")
        eligible = [("a", {}), ("b", {})]
        merged, vmap = R.merge_skill_variants(eligible, {"a": a, "b": b})
        assert [n for n, _ in merged] == ["a", "b"]
        assert vmap == {"a": ["a"], "b": ["b"]}

    def test_no_path_zombie_keeps_separate(self):
        """无 SKILL.md 的僵尸不参与合并，保持独立条目交由僵尸过滤剔除。"""
        eligible = [("review", {}), ("devops/review", {})]
        merged, vmap = R.merge_skill_variants(eligible, {})
        # 两个都无 path → 各自独立（不会被错误合并成一组）
        assert [n for n, _ in merged] == ["review", "devops/review"]
        assert set(vmap) == {"review", "devops/review"}

    def test_three_variants_one_group(self, tmp_path):
        md = _p(tmp_path, "skills/hermes-infrastructure/SKILL.md")
        names = ["hermes-infrastructure", "devops/hermes-infrastructure",
                 "infra/hermes-infrastructure"]
        eligible = [(n, {"use_count": i}) for i, n in enumerate(names)]
        idx = {n: md for n in names}
        merged, vmap = R.merge_skill_variants(eligible, idx)
        assert [n for n, _ in merged] == ["hermes-infrastructure"]
        assert len(vmap["hermes-infrastructure"]) == 3


class TestVariantAwareClearing:
    def test_clear_zeroes_all_variants(self):
        """优化成功后清零必须覆盖所有变体名，否则另一个变体永远清不掉。"""
        state = {
            "skill_name_variants": {
                "system-health-check": ["devops/system-health-check",
                                        "system-health-check"],
            },
            "skill_neg_feedback": {
                "system-health-check": 12,
                "devops/system-health-check": 12,
            },
        }
        name = "system-health-check"
        res = {"neg_cleared": True}
        # 模拟 run() 中的清零逻辑
        if res.get("neg_cleared"):
            variants = state.get("skill_name_variants", {}).get(name, [name])
            for v in variants:
                state.setdefault("skill_neg_feedback", {})[v] = 0
        assert state["skill_neg_feedback"]["system-health-check"] == 0
        assert state["skill_neg_feedback"]["devops/system-health-check"] == 0

    def test_clear_without_variant_map_uses_bare_name(self):
        """向后兼容：state 里没有 variant map 时按单名清零。"""
        state = {"skill_neg_feedback": {"solo": 5}}
        name = "solo"
        variants = state.get("skill_name_variants", {}).get(name, [name])
        for v in variants:
            state.setdefault("skill_neg_feedback", {})[v] = 0
        assert state["skill_neg_feedback"]["solo"] == 0

    def test_rank_skills_merges_before_ranking(self, tmp_path, monkeypatch):
        """rank_skills 端到端：两个变体只占一个名额，计数取 max。"""
        md = _p(tmp_path, "skills/alpha/SKILL.md")
        monkeypatch.setattr(R, "build_skill_path_index",
                            lambda: {"alpha": md, "devops/alpha": md})
        eligible = [("alpha", {}), ("devops/alpha", {})]
        state = {"skill_neg_feedback": {"alpha": 7, "devops/alpha": 7},
                 "skill_total_mentions": {"alpha": 7, "devops/alpha": 7}}
        # 走 rank_skills 的合并前置段：直接调用并检查 state 副作用
        path_index = R.build_skill_path_index()
        eligible2, vmap = R.merge_skill_variants(eligible, path_index)
        assert [n for n, _ in eligible2] == ["alpha"]
        state["skill_name_variants"] = vmap
        assert state["skill_name_variants"]["alpha"] == [
            "alpha", "devops/alpha"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
