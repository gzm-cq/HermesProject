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

print("\n🟢 全部单测通过")