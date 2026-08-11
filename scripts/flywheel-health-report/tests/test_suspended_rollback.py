"""验证「连续恶化触发 suspend 时把 .env 回滚到 initial_value 基线」。

覆盖：
  1. 连续 3 次 not-improved → suspended=True，且 write_env_param 以 initial_value 被调用一次
  2. 第 4 次仍 not-improved → 不重复回滚（上升沿判定，幂等）
  3. initial_value 为 None → 安全跳过，不写 .env、不抛异常
  4. rollback_param_to_baseline 的单元语义（已在基线 / 非法基线）
"""

import copy
import datetime as _dt
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from flywheel_health_report.auto_tuner import tuner  # noqa: E402
from flywheel_health_report.config import (  # noqa: E402
    CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD,
)

# 原用 KN_TOKEN_BUDGET_TOTAL（反馈键 token_exhaust_pct），该参数随「移除 token 预算控制」
# 一并从 PARAM_DEFS 下线，故改用 KN_SAG_SEARCH_TOP_K：
#   - 反馈键 sag_merge_zero_pct(down_better) + sag_total_kept(stable_ok)
#   - 两个键都是客观指标，不属于 KN Judge 主观键 → 不受 judge 样本量过滤影响
#   - 让两个键同时恶化即可确定性地得到 improved=False（ic=0 < max(tc/2,1)）
PARAM = "KN_SAG_SEARCH_TOP_K"
BASELINE = 3             # 首次调优前的值 → initial_value
TUNED = 4                # 调优后的值（与 BASELINE 不同，保证 no_change=False）
DRIFTED = "9"            # 多轮调优后 .env 里实际停留的（最差的）值

# sag_merge_zero_pct 越高越差；sag_total_kept 掉了一半（>10%）也算恶化
METRICS_BEFORE = {"sag_merge_zero_pct": 10.0, "sag_total_kept": 100}
METRICS_AFTER = {"sag_merge_zero_pct": 25.0, "sag_total_kept": 50}


class _Harness:
    """把 tuner 的所有 I/O 边界（state 文件 / .env / 日志）换成内存桩。"""

    def __init__(self, old_value=BASELINE, new_value=TUNED, env_value=DRIFTED):
        self.state = {}
        self.env = {PARAM: env_value}
        self.writes = []      # [(param, value_str)]
        self.backups = 0
        self.last_tune = {
            "parameter": PARAM,
            "date": tuner._report_date_today(),
            "old_value": old_value,
            "new_value": new_value,
            "direction": "up",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "status": "pending_restart",
            "metrics_before": dict(METRICS_BEFORE),
        }

    @property
    def pstate(self):
        return self.state.get(PARAM) or {}

    def run_round(self):
        """跑一轮 handle_pending_restart（gateway 已重启 + 指标恶化）。"""
        return tuner.handle_pending_restart()


@pytest.fixture
def harness(monkeypatch):
    h = _Harness()
    _install(monkeypatch, h)
    return h


def _fake_extract_metrics(today, yday):
    """模拟报告：日期取「调优日(today)之后一日」，满足 metrics_after 严格取调优后一日的要求。"""
    try:
        after = (_dt.date.fromisoformat(today) + _dt.timedelta(days=1)).isoformat()
    except Exception:
        after = today
    return {"today": {"date": after, "_stub": True}, "yesterday": None}


def _install(monkeypatch, h):
    monkeypatch.setattr(tuner, "_get_last_tune_any", lambda: copy.deepcopy(h.last_tune))
    monkeypatch.setattr(tuner, "verify_restart", lambda ts: True)
    monkeypatch.setattr(tuner, "update_log_entry",
                        lambda param, date, status, metrics=None: None)
    monkeypatch.setattr(tuner, "_extract_metrics_for_tuning",
                        _fake_extract_metrics)
    monkeypatch.setattr(tuner, "_extract_metrics_before",
                        lambda rec: dict(METRICS_AFTER) if rec else {})

    monkeypatch.setattr(tuner, "load_state", lambda: copy.deepcopy(h.state))

    def _save_state(s):
        h.state = copy.deepcopy(s)
    monkeypatch.setattr(tuner, "save_state", _save_state)

    monkeypatch.setattr(tuner, "read_env_param", lambda name: h.env.get(name))

    def _write_env_param(name, value):
        h.writes.append((name, value))
        h.env[name] = value
        return True
    monkeypatch.setattr(tuner, "write_env_param", _write_env_param)

    def _backup_env():
        h.backups += 1
        return f"/fake/backup/env-{h.backups}.bak"
    monkeypatch.setattr(tuner, "backup_env", _backup_env)


# ============================================================
# 1. 连续 3 次恶化 → suspended + 回滚到 initial_value
# ============================================================

def test_rollback_on_suspend_rising_edge(harness):
    h = harness
    threshold = CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD

    # 前 threshold-1 轮：还没到暂停阈值，不应该动 .env
    for _ in range(threshold - 1):
        h.run_round()
        assert h.pstate.get("suspended") is not True
        assert h.writes == [], "未触发 suspend 时不应回滚 .env"

    # 第 threshold 轮：触发 suspend → 回滚
    h.run_round()

    assert h.pstate.get("suspended") is True
    assert h.pstate.get("initial_value") == float(BASELINE)
    assert h.writes == [(PARAM, str(BASELINE))], \
        f"应恰好用 initial_value 回滚一次，实际 writes={h.writes}"
    assert h.env[PARAM] == str(BASELINE)
    assert h.backups == 1, "回滚前应备份 .env 一次"

    # 审计字段
    assert h.pstate.get("rollback_ok") is True
    assert h.pstate.get("rolled_back_to") == float(BASELINE)
    assert h.pstate.get("rolled_back_at")


# ============================================================
# 2. 幂等：保持 suspended 的后续轮次不重复回滚
# ============================================================

def test_rollback_is_idempotent_across_rounds(harness):
    h = harness
    threshold = CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD

    for _ in range(threshold):
        h.run_round()
    assert h.pstate.get("suspended") is True
    assert len(h.writes) == 1

    # 模拟运行时把参数又改回了漂移值，验证「不是靠值相等去重，而是靠上升沿」
    h.env[PARAM] = DRIFTED

    for _ in range(3):
        h.run_round()
        assert h.pstate.get("suspended") is True

    assert len(h.writes) == 1, \
        f"suspended 保持 True 期间不应重复回滚，实际 writes={h.writes}"
    assert h.backups == 1


# ============================================================
# 3. initial_value 为 None → 安全跳过
# ============================================================

def test_no_rollback_when_initial_value_missing(monkeypatch):
    # old_value / new_value 都无法转 float → update_state 无法填 initial_value
    h = _Harness(old_value="n/a", new_value="also-n/a")
    _install(monkeypatch, h)

    for _ in range(CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD):
        h.run_round()

    assert h.pstate.get("suspended") is True
    assert h.pstate.get("initial_value") is None
    assert h.writes == [], "无基线时必须跳过回滚，不能瞎写 .env"
    assert h.backups == 0
    assert h.pstate.get("rollback_ok") is False
    assert h.env[PARAM] == DRIFTED, ".env 应保持原样"


# ============================================================
# 4. rollback_param_to_baseline 单元语义
# ============================================================

def test_rollback_returns_false_on_bad_baseline(harness):
    h = harness
    assert tuner.rollback_param_to_baseline(PARAM, None) is False
    assert tuner.rollback_param_to_baseline(PARAM, "not-a-number") is False
    assert h.writes == []
    assert h.backups == 0


def test_rollback_skips_when_already_at_baseline(harness):
    h = harness
    h.env[PARAM] = str(BASELINE)
    assert tuner.rollback_param_to_baseline(PARAM, BASELINE) is True
    assert h.writes == [], "已处于基线时应跳过写入"
    assert h.backups == 0


# 原 test_rollback_normalizes_ratio_trio 已删除：
# 它验证的是「回滚 KN_TOKEN_BUDGET_*_RATIO 之一后把三比例重新归一化到和为 1.0」。
# 随着 token 预算控制下线，这三个参数已从 PARAM_DEFS 移除，调参表里不再存在任何
# ratio 三元组，tuner 中的 normalize_ratio_trio / _rebalance_ratio_trio_after_rollback
# 也已作为死代码删除，故该用例无对应被测行为。
# 若将来重新引入「需保持总和恒定的参数组」，应连同归一化逻辑一起补回本用例。
