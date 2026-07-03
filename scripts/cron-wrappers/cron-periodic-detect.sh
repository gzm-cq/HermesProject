#!/bin/bash
# cron-periodic-detect.sh — Layer 1 周期检测（每 60min）
# 检测执行失败的 job + 无异常时静默跳过飞书推送
#
# 功能：
#   - 每次执行出一份摘要：检查了多少 job，各状态统计
#   - 对持续失败的 job → 告警（去重 1h）
#   - 对从 error 恢复的 job → 恢复通知
#
# 部署路径：/root/.hermes/scripts/cron-periodic-detect.sh
# 调度方式：Hermes cron job, */30 * * * *

set -euo pipefail

_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    # shellcheck disable=SC1090
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh (${_CRON_LIB})" >&2
    exit 2
fi

cron_init "cron-periodic-detect"
CRON_SKIP_FINISH_NOTIFY=true   # 脚本自发送摘要通知，不让 cron_finish 重复发

# ===== 去重状态文件 =====
# 放置在持久化目录，避免系统重启后 /tmp 清空导致重复告警
DEDUP_DIR="${CRON_STATE_DIR:-${HERMES_HOME:-/root/.hermes}/lib/cron-state}"
mkdir -p "$DEDUP_DIR"
DEDUP_FILE="${DEDUP_DIR}/cron-periodic-dedup.json"
[[ -f "$DEDUP_FILE" ]] || echo '{}' > "$DEDUP_FILE"

# ===== 阶段 1：Python 分析 =====
cron_section "扫描 job 状态"
ANALYSIS_FILE=$(mktemp /tmp/cron-periodic-analysis-XXXXXX.json)
trap "rm -f '$ANALYSIS_FILE'" EXIT

python3 <<PY > "$ANALYSIS_FILE"
import json, os
from datetime import datetime, timezone

JOBS_FILE = os.path.expanduser("~/.hermes/cron/jobs.json")
STATE_DIR = os.path.expanduser("~/.hermes/lib/cron-state")
DEDUP_FILE = os.path.expanduser("$DEDUP_FILE")
NOW = datetime.now(timezone.utc)
now_ts = NOW.timestamp()

def load_jobs():
    with open(JOBS_FILE) as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("jobs", [])

def load_state(name):
    p = os.path.join(STATE_DIR, f"{name}.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)

def load_dedup():
    with open(DEDUP_FILE) as f:
        return json.load(f)

def save_dedup(dedup):
    with open(DEDUP_FILE, "w") as f:
        json.dump(dedup, f, ensure_ascii=False, indent=2)

dedup = load_dedup()

# 收集 job 状态
jobs_data = load_jobs()
status_count = {"ok": 0, "error": 0, "never": 0, "other": 0}
exhausted_jobs = []
all_jobs = []

for job in jobs_data:
    if not job.get("enabled", True):
        continue
    jname = job.get("name", job.get("id", ""))
    ls = job.get("last_status", "")
    lr = job.get("last_run_at", "") or "从未运行"
    sched = job.get("schedule_display", "")
    st = load_state(jname)
    exhausted = st.get("overall_retries_exhausted", False)
    last_error = st.get("last_error", "")

    if ls == "ok":
        status_count["ok"] += 1
    elif ls == "error":
        status_count["error"] += 1
    else:
        status_count["never"] += 1

    all_jobs.append({
        "name": jname,
        "status": ls,
        "last_run": lr,
        "schedule": sched,
        "exhausted": exhausted,
        "last_error": last_error,
    })

# alert/recovery 检测
alert = []
recovered = []

for j in all_jobs:
    if j["status"] == "error" and j["exhausted"]:
        error_key = j["last_error"][:60]
        prev = dedup.get(j["name"], {})
        prev_key = prev.get("error_key", "")
        prev_ts = prev.get("alerted_at", 0)
        if isinstance(prev_ts, str):
            try:
                prev_ts = datetime.fromisoformat(prev_ts).timestamp()
            except Exception:
                prev_ts = 0
        if not (error_key == prev_key and (now_ts - prev_ts) < 3600):
            alert.append(j)
            dedup[j["name"]] = {"error_key": error_key, "alerted_at": NOW.isoformat()}

    elif prev := dedup.get(j["name"], {}):
        if prev.get("error_key") and (j["status"] != "error" or not j["exhausted"]):
            recovered.append(j)
            dedup.pop(j["name"], None)

save_dedup(dedup)

print(json.dumps({
    "alert": alert,
    "recovered": recovered,
    "status_count": status_count,
    "all_jobs": all_jobs,
}, ensure_ascii=False))
PY

# ===== 阶段 2：生成摘要报告 =====
cron_section "生成检查报告"

# 用 heredoc 避免 bash 展开 $total 等变量
REPORT_FILE=$(mktemp /tmp/cron-periodic-report-XXXXXX.txt)
trap "rm -f '$REPORT_FILE'" EXIT

export ANALYSIS_FILE
python3 <<'PY' > "$REPORT_FILE"
import json, os

d = json.load(open(os.environ["ANALYSIS_FILE"]))
total = len(d["all_jobs"])
ok = d["status_count"]["ok"]
err = d["status_count"]["error"]
never = d["status_count"]["never"]

from datetime import datetime
now_str = datetime.now().strftime("%H:%M")

lines = [f"Cron 健康检查 — {now_str}  |  共 {total} 个 job"]
lines.append(f"✅ {ok} 正常  ❌ {err} 异常  ⚪ {never} 未运行")
lines.append("")

abnormal = [j for j in d["all_jobs"] if j["status"] == "error"]
if abnormal:
    lines.append("━━━ 异常 job ━━━")
    for j in abnormal:
        emoji = "❌" if j["exhausted"] else "⚠️"
        lines.append(f"{emoji} {j['name']}")
        lines.append(f"     ├ 上次: {j['last_run']}")
        if j["exhausted"]:
            lines.append(f"     └ 重试耗尽: {j.get('last_error', '')[:80]}")
        else:
            lines.append(f"     └ 等待自动重试")
    lines.append("")

print("\n".join(lines))
print("---ALERT---")
print(len(d["alert"]))
print("---RECOVERED---")
print(len(d["recovered"]))
PY

SUMMARY_TEXT=$(sed -n '/^---/,$!p' "$REPORT_FILE")
ALERT_COUNT=$(grep -A999 -- '---ALERT---' "$REPORT_FILE" | tail -n +2 | head -1 || echo 0)
RECOVERED_COUNT=$(grep -A999 -- '---RECOVERED---' "$REPORT_FILE" | tail -n +2 | head -1 || echo 0)

# 从 REPORT_FILE 取 job 数
JOB_OK=$(python3 -c "import json; d=json.load(open('$ANALYSIS_FILE')); print(d['status_count']['ok'])")
JOB_ERR=$(python3 -c "import json; d=json.load(open('$ANALYSIS_FILE')); print(d['status_count']['error'])")
JOB_NEVER=$(python3 -c "import json; d=json.load(open('$ANALYSIS_FILE')); print(d['status_count']['never'])")
JOB_TOTAL=$(( JOB_OK + JOB_ERR + JOB_NEVER ))

# 追加状态明细到 _STEP_RESULTS，让 cron_finish 的飞书通知包含内容
_STEP_RESULTS+=("💠 检查 $JOB_TOTAL 个 job: ✅$JOB_OK ❌$JOB_ERR ⚪$JOB_NEVER")

# ===== 阶段 3+4+5：恢复/告警/摘要（互斥，只发一条） =====
if [[ "$ALERT_COUNT" -gt 0 ]]; then
    cron_notify "⚠️ [Cron 自愈] ${ALERT_COUNT} 个 job 需人工介入" "$SUMMARY_TEXT"
    cron_finish
    exit 1

elif [[ "$RECOVERED_COUNT" -gt 0 ]]; then
    cron_notify "✅ [Cron 自愈] Job 恢复正常" "$SUMMARY_TEXT"
    cron_finish
    exit 0

else
    # 无异常 → 静默退出，不推送飞书
    cron_finish
    exit 0
fi