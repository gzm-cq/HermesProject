"""验证 P1/P4 修复：日期对齐、metrics_unchanged、verify_restart 时区解析。"""
import sys
import os
import importlib.util

# 加载 tuner 模块（不触发 main）
SRC = r"d:\HermesProject\scripts\flywheel-health-report\src"
sys.path.insert(0, SRC)

import datetime as _dt

from flywheel_health_report.auto_tuner import tuner

# 1. P1C: 日期对齐（_report_date_today 应匹配 report.py data_window = UTC-1）
# 用固定时间验证：CN 08:00 = UTC 00:00，data_window = UTC 昨天
# 我们无法 mock _dt.datetime.now，改为验证逻辑：report_date_today 与 report 的 data_window 公式一致
# report.py: data_window = (now_utc - 1day).strftime
_utc_now = _dt.datetime.now(_dt.timezone.utc)
expected_data_window = (_utc_now - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
assert tuner._report_date_today() == expected_data_window, \
    f"_report_date_today={tuner._report_date_today()} != data_window={expected_data_window}"
expected_prev = (_utc_now - _dt.timedelta(days=2)).strftime("%Y-%m-%d")
assert tuner._report_date_yesterday() == expected_prev, \
    f"_report_date_yesterday={tuner._report_date_yesterday()} != {expected_prev}"
print("✅ P1C 日期对齐：_report_date_today == report.data_window (UTC-1)")

# 2. P1B: verify_restart 时区解析（CST = +8）
# 模拟 systemctl 输出 "Sun 2026-08-09 03:12:45 CST"（3:12 CST = 前日 19:12 UTC）
# 调优时间戳若晚于该启动时间，应返回 True
def _mock_runshell_gw(cmd, timeout):
    return 'ActiveEnterTimestamp=Sun 2026-08-09 03:12:45 CST\n', "", 0

orig = tuner._run_shell
tuner._run_shell = _mock_runshell_gw
# 逻辑：verify_restart 返回 gw_epoch > tune_epoch（gateway 启动晚于调优 = 调优后重启过 → True）
# 调优在 gateway 启动之前（02:00 CST < 03:12 CST）→ gateway 在其后启动 → 应 True
tune_ts_before = "2026-08-09T02:00:00+08:00"
assert tuner.verify_restart(tune_ts_before) is True, f"verify_restart 应 True（调优先于重启）"
# 调优在 gateway 启动之后（04:12 CST > 03:12 CST）→ 调优后未重启 → 应 False
tune_ts = "2026-08-09T04:12:45+08:00"
assert tuner.verify_restart(tune_ts) is False, f"verify_restart 应 False（调优后未重启）"
tuner._run_shell = orig
print("✅ P1B verify_restart：CST=+8 解析正确，before/after 判定正确")

# 3. P4: _metrics_unchanged 检测
mb = {"router_empty_pct": 1.3, "sag_total_kept": 108, "kn_judge_avg_relevance": 0.56}
ma_same = {"router_empty_pct": 1.3, "sag_total_kept": 108, "kn_judge_avg_relevance": 0.56}
ma_diff = {"router_empty_pct": 0.8, "sag_total_kept": 150, "kn_judge_avg_relevance": 0.62}
ma_partial = {"router_empty_pct": 1.3}  # 只有部分键
assert tuner._metrics_unchanged(mb, ma_same) is True, "同值应判 unchanged"
assert tuner._metrics_unchanged(mb, ma_diff) is False, "不同值应判 changed"
assert tuner._metrics_unchanged(mb, ma_partial) is True, "部分键同值应判 unchanged（有共同数值键）"
assert tuner._metrics_unchanged({}, ma_same) is False, "空 mb 应 False"
print("✅ P4 _metrics_unchanged：同值 True / 不同假 False / 部分键 True / 空 False")

# 4. P1B: update_log_entry metrics_after=None 清除字段
import tempfile, json
LOG_ORIG = tuner.LOG_FILE
with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as tf:
    rec0 = {"parameter": "KN_MIN_SCORE", "date": "2026-08-09", "status": "applied",
            "metrics_before": {"a": 1.0}, "metrics_after": {"a": 1.0}}
    tf.write(json.dumps(rec0) + "\n")
    tmp_path = tf.name
tuner.LOG_FILE = tmp_path
tuner.update_log_entry("KN_MIN_SCORE", "2026-08-09", "pending_restart", None)
with open(tmp_path, encoding="utf-8") as f:
    rec = json.loads(f.readline())
assert "metrics_after" not in rec, "metrics_after=None 应清除字段"
assert rec["status"] == "pending_restart"
tuner.LOG_FILE = LOG_ORIG
os.unlink(tmp_path)
print("✅ P1B update_log_entry：metrics_after=None 清除字段")

# 5. 新增：_get_last_tune_for 过滤 dry_run 记录
import tempfile as _tf, json as _json
_ORIG_LOG = tuner.LOG_FILE
with _tf.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as _tf2:
    # dry_run 记录在前，真实记录在后
    _tf2.write(_json.dumps({"parameter": "KN_MIN_SCORE", "date": "2026-08-08", "status": "applied",
                             "dry_run": True, "new_value": 0.50}) + "\n")
    _tf2.write(_json.dumps({"parameter": "KN_MIN_SCORE", "date": "2026-08-07", "status": "applied",
                             "new_value": 0.45}) + "\n")
    _tmp_path = _tf2.name
tuner.LOG_FILE = _tmp_path
last = tuner._get_last_tune_for("KN_MIN_SCORE")
assert last is not None, "应找到真实记录"
assert last.get("new_value") == 0.45, f"应为真实记录 (0.45)，实际 {last.get('new_value')}"
assert last.get("dry_run") is None or last.get("dry_run") is False, "不应返回 dry_run 记录"
tuner.LOG_FILE = _ORIG_LOG
os.unlink(_tmp_path)
print("✅ _get_last_tune_for 过滤 dry_run：正确返回非 dry_run 记录")

# 6. state 命名迁移（旧名→新名补历史）
# 场景1：旧名存在、新名不存在 → 改名，历史整体保留
_s = {"sag_max_inject": {"degradation_count": 2, "best_value": 4.0}}
_ch = tuner._migrate_state(_s)
assert _ch is True and "sag_max_inject" not in _s
assert _s["KN_SAG_MAX_INJECT"]["degradation_count"] == 2
# 场景2：新旧并存、新名 virgin → 合并旧名学习历史
_s = {"sag_max_inject": {"degradation_count": 3, "best_value": 5.0},
      "KN_SAG_MAX_INJECT": {"locked": False}}
tuner._migrate_state(_s)
assert _s["KN_SAG_MAX_INJECT"]["degradation_count"] == 3
assert _s["KN_SAG_MAX_INJECT"]["best_value"] == 5.0
assert "sag_max_inject" not in _s
# 场景3：新旧并存、新名已有历史 → 新名为准，丢弃旧名残留
_s = {"sag_max_inject": {"degradation_count": 5},
      "KN_SAG_MAX_INJECT": {"degradation_count": 1, "locked": True}}
tuner._migrate_state(_s)
assert _s["KN_SAG_MAX_INJECT"]["degradation_count"] == 1
assert "sag_max_inject" not in _s
# 场景4：已下线参数（token 预算类）→ 丢弃
_s = {"token_budget": {"x": 1}, "KN_TOKEN_BUDGET_TOTAL": {"y": 2}}
tuner._migrate_state(_s)
assert "token_budget" not in _s and "KN_TOKEN_BUDGET_TOTAL" not in _s
print("✅ state 命名迁移：改名/合并virgin/丢弃残留/丢弃下线参数 均正确")

# 7. metrics_after 闭环：必须严格取「调优后一日」的报告，否则保持 pending_restart
class _PendingCtx:
    """模拟 handle_pending_restart 依赖，记录是否写了 applied。"""
    def __init__(self, today_date, tune_date):
        self.today_date = today_date
        self.tune_date = tune_date
        self.applied_called = False
        self.today_rec = {"date": today_date, "router_empty_pct": 1.3} if today_date else {}

def _test_metrics_after_guard():
    ctx = _PendingCtx(today_date=ctx_today, tune_date=ctx_tune)
    # mock 依赖（P0 修复后 handle_pending_restart 改用 _get_all_pending_tunes 遍历全部 pending）
    _orig_pending = tuner._get_all_pending_tunes
    _orig_last = tuner._get_last_tune_any
    _orig_verify = tuner.verify_restart
    _orig_extract = tuner._extract_metrics_for_tuning
    _orig_update = tuner.update_log_entry
    _orig_report_today = tuner._report_date_today
    _orig_load = tuner.load_state
    _orig_save = tuner.save_state
    tuner._get_all_pending_tunes = lambda: [{"parameter": "KN_MAX_RESULTS", "date": ctx.tune_date,
                                        "old_value": 3.0, "new_value": 4.0, "direction": "up",
                                        "timestamp": "2026-08-09T00:00:00+08:00", "status": "pending_restart",
                                        "metrics_before": {"router_empty_pct": 1.3}}]
    tuner._get_last_tune_any = lambda: None
    tuner.verify_restart = lambda ts: True
    tuner._extract_metrics_for_tuning = lambda t, y: {"today": ctx.today_rec, "yesterday": None}
    tuner._report_date_today = lambda: ctx.today_date
    tuner.load_state = lambda: {}
    tuner.save_state = lambda s: None
    def _fake_update(param, date, status, metrics_after=None):
        ctx.applied_called = True
    tuner.update_log_entry = _fake_update
    try:
        ret = tuner.handle_pending_restart()
    finally:
        tuner._get_all_pending_tunes = _orig_pending
        tuner._get_last_tune_any = _orig_last
        tuner.verify_restart = _orig_verify
        tuner._extract_metrics_for_tuning = _orig_extract
        tuner.update_log_entry = _orig_update
        tuner._report_date_today = _orig_report_today
        tuner.load_state = _orig_load
        tuner.save_state = _orig_save
    return ret, ctx

# 场景A：报告日期 == 调优日（同名报告，fallback 复用）→ 保持 pending（True），不写 applied
ctx_today, ctx_tune = "2026-08-09", "2026-08-09"
_ret, _ctx = _test_metrics_after_guard()
assert _ret is True and _ctx.applied_called is False, f"同名报告应保持 pending，实际 ret={_ret} applied={_ctx.applied_called}"
# 场景B：报告日期 == 调优日 但无 date 字段 → 保持 pending
ctx_today, ctx_tune = "", "2026-08-09"
_ret, _ctx = _test_metrics_after_guard()
assert _ret is True and _ctx.applied_called is False, "无 date 字段应保持 pending"
# 场景C：报告日期 > 调优日（调优后一日）→ 允许确认，写 applied
ctx_today, ctx_tune = "2026-08-10", "2026-08-09"
_ret, _ctx = _test_metrics_after_guard()
assert _ctx.applied_called is True, f"调优后一日应允许确认，实际 applied={_ctx.applied_called}"
print("✅ metrics_after 闭环：同名报告/无日期保持 pending，调优后一日才确认")

print("\n🟢 全部单测通过")