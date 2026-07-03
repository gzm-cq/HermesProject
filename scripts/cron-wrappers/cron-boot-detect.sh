#!/bin/bash
# cron-boot-detect.sh — Layer 1 开机检测
# 在 gateway 启动后 30s 执行，检测停机期间遗漏的 tick 和持续失败的 job
#
# 触发方式：systemd oneshot After=hermes-gateway.service
# 部署路径：/root/.hermes/scripts/cron-boot-detect.sh
#
# 数据流：
#   Python 分析 job 状态 → /tmp/cron-boot-analysis.json
#   Python 读分析结果生成报告文本 → 飞书推送

set -euo pipefail

_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    # shellcheck disable=SC1090
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh (${_CRON_LIB})" >&2
    exit 2
fi

cron_init "cron-boot-detect"
CRON_SKIP_FINISH_NOTIFY=true   # 脚本自发送恢复报告，不让 cron_finish 重复发

# ===== 阶段 1：分析 =====
cron_section "分析 cron job 状态"
ANALYSIS_FILE=$(mktemp /tmp/cron-boot-analysis-XXXXXX.json)
trap "rm -f '$ANALYSIS_FILE'" EXIT

python3 <<'PY' > "$ANALYSIS_FILE"
import json, os
from datetime import datetime, timezone

JOBS_FILE = os.path.expanduser("~/.hermes/cron/jobs.json")
STATE_DIR = os.path.expanduser("~/.hermes/lib/cron-state")
NOW = datetime.now(timezone.utc)

def load_jobs():
    if not os.path.exists(JOBS_FILE):
        return []
    with open(JOBS_FILE) as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("jobs", [])

def load_state(name):
    p = os.path.join(STATE_DIR, f"{name}.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)

def parse_dt(s):
    return datetime.fromisoformat(s) if s else None

def count_missed_cron(expr, last):
    from croniter import croniter
    c = croniter(expr, last)
    cnt = 0
    while True:
        nxt = c.get_next(datetime)
        if nxt > NOW:
            break
        cnt += 1
    return cnt

result = {"caught_up": [], "fast_forwarded": [], "failed_exhausted": []}
now_ts = NOW.timestamp()

for job in load_jobs():
    if not job.get("enabled", True):
        continue
    jid = job.get("id", "")
    jname = job.get("name", jid)
    last_run = job.get("last_run_at", "")
    last_status = job.get("last_status", "")
    sched = job.get("schedule", {})
    kind = sched.get("kind", "")
    expr = sched.get("expr", "")

    st = load_state(jname)
    exhausted = st.get("overall_retries_exhausted", False)

    # 失败重试耗尽
    if exhausted and last_status == "error":
        result["failed_exhausted"].append({
            "job_name": jname, "job_id": jid,
            "last_run": last_run,
            "last_error": st.get("last_error", ""),
        })
        continue

    if not last_run:
        continue
    last_dt = parse_dt(last_run)
    if not last_dt:
        continue

    diff = now_ts - last_dt.timestamp()

    # 5 分钟内跑过 → scheduler 已自动补跑
    if diff < 300:
        result["caught_up"].append({
            "job_name": jname, "job_id": jid, "ran_at": last_run,
        })
        continue

    # 错过未补跑
    missed = 0
    if kind == "cron" and expr:
        missed = count_missed_cron(expr, last_dt)
    elif kind == "interval":
        mins = sched.get("minutes", 60)
        expected = mins * 60
        if expected > 0:
            missed = max(0, int(diff / expected) - 1)

    if missed > 0:
        result["fast_forwarded"].append({
            "job_name": jname, "job_id": jid,
            "last_run": last_run, "missed_ticks": missed,
        })

print(json.dumps(result, ensure_ascii=False))
PY

if [[ ! -s "$ANALYSIS_FILE" ]]; then
    cron_err "状态分析失败"
    cron_finish
    exit 1
fi

# ===== 阶段 2：生成报告 =====
cron_section "生成恢复报告"
REPORT_HEADER_TS=$(date '+%Y-%m-%d %H:%M')
REPORT_TEXT=$(ANALYSIS_FILE="$ANALYSIS_FILE" REPORT_HEADER_TS="$REPORT_HEADER_TS" python3 <<'PY'
import json
import os

analysis_path = os.environ["ANALYSIS_FILE"]
header_ts = os.environ.get("REPORT_HEADER_TS", "")

with open(analysis_path) as f:
    data = json.load(f)

lines = [f'⚡ 系统恢复报告 — {header_ts}', '']

lines.append('━━━━━ 补跑成功 — Hermes scheduler 已自动处理 ━━━━━')
if data.get('caught_up'):
    for j in data['caught_up']:
        lines.append(f"  ✅ {j['job_name']} 补跑于 {j['ran_at']}")
else:
    lines.append('  （无补跑执行）')
lines.append('')

lines.append('━━━━━ 错过未补跑 — fast-forward，下次正常排程 ━━━━━')
if data.get('fast_forwarded'):
    for j in data['fast_forwarded']:
        lines.append(f"  ⚪ {j['job_name']} 上次 {j['last_run']}, 错过了 {j['missed_ticks']} 个 tick")
else:
    lines.append('  （无错过）')
lines.append('')

exh = data.get('failed_exhausted', [])
lines.append('━━━━━ 失败重试耗尽 — 需人工介入 ━━━━━')
if exh:
    for j in exh:
        lines.append(f"  ❌ {j['job_name']}")
        lines.append(f"     ├ 失败时间: {j.get('last_run', '未知')}")
        lines.append(f"     └ 原因: {j.get('last_error', '未知')}")
else:
    lines.append('  （无失败重试耗尽）')

print('\n'.join(lines))
# 最后一行是状态标记
print('ALERT' if exh else 'OK')
PY
)

STATUS=$(echo "$REPORT_TEXT" | tail -1)
BODY=$(echo "$REPORT_TEXT" | head -n -1)

# ===== 阶段 3：飞书推送 =====
cron_section "推送恢复报告"
if [[ "$STATUS" == "ALERT" ]]; then
    cron_notify "⚠️ [Cron 自愈] 系统恢复 — 有 job 需人工介入" "$BODY"
    cron_finish
    exit 1
else
    cron_notify "✅ [Cron 自愈] 系统恢复 — 无异常" "$BODY"
    cron_finish
    exit 0
fi