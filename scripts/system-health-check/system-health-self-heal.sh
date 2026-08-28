#!/bin/bash
# system-health-self-heal.sh - No-agent cron job for system health monitoring + self-healing.
#
# Replaces agent-driven cron job 9536bea31957 which had drift_skip errors when model changed.
# Pure bash + python3 one-liners - zero LLM token cost, immune to model drift.

set -euo pipefail

source /root/.hermes/lib/cron_common.sh

cron_init "system-health-self-heal"

SCRIPT_DIR="/root/.hermes/scripts"
STATE_DIR="/root/.hermes/lib/cron-state"
LOG_DIR="/root/.hermes/logs/cron"
RL_FILE="${STATE_DIR}/self-heal-ratelimit.json"
REPORT_FILE="${LOG_DIR}/system-health-self-heal-latest.json"

mkdir -p "${STATE_DIR}" "${LOG_DIR}"

NOW_EPOCH="$(date +%s)"
NOW_ISO="$(date '+%Y-%m-%dT%H:%M:%S%z')"

ACTIONS_TAKEN=""
NEEDS_MANUAL=""
INFRA_STATUS="ok"

push_action() { ACTIONS_TAKEN="${ACTIONS_TAKEN}${ACTIONS_TAKEN:+ }$1"; }
push_manual() { NEEDS_MANUAL="${NEEDS_MANUAL}${NEEDS_MANUAL:+ }$1"; }

cron_section "Step A: Run full infra check via health-check-all.py"

# health-check-all.py outputs JSON to stdout, logs go to stderr
HEALTH_JSON=""
HEALTH_JSON="$(python3 "${SCRIPT_DIR}/health-check-all.py" 2>/dev/null || echo '{}')"

if [ -z "${HEALTH_JSON}" ] || [ "${HEALTH_JSON}" = "{}" ]; then
    cron_warn "health-check-all.py returned empty JSON"
    HEALTH_JSON="{}"
    INFRA_STATUS="warn"
fi

cron_section "Step B: Parse health JSON for fail/warn services"

# Extract service names with status=fail or status=warn using python3 one-liner
FAILED_SERVICES=""
WARN_SERVICES=""

FAILED_SERVICES="$(python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
except:
    print(''); sys.exit(0)
results = []
for name, info in data.items():
    if name.startswith('_'): continue
    if isinstance(info, dict) and info.get('status') == 'fail':
        results.append(name)
print(' '.join(results))
" <<< "${HEALTH_JSON}" 2>/dev/null || echo "")"

WARN_SERVICES="$(python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
except:
    print(''); sys.exit(0)
results = []
for name, info in data.items():
    if name.startswith('_'): continue
    if isinstance(info, dict) and info.get('status') == 'warn':
        results.append(name)
print(' '.join(results))
" <<< "${HEALTH_JSON}" 2>/dev/null || echo "")"

if [ -n "${FAILED_SERVICES}" ]; then
    cron_err "Failed services: ${FAILED_SERVICES}"
    INFRA_STATUS="fail"
fi

if [ -n "${WARN_SERVICES}" ]; then
    cron_warn "Warning services: ${WARN_SERVICES}"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

cron_section "Step C: Attempt self-healing (systemctl restart with rate limit)"

# Map health-check service names to systemd unit names
declare -A SVC_MAP=(
    ["hermes"]="hermes-gateway"
    ["bifrost"]="bifrost"
    ["hindsight"]="hindsight-daemon"
    ["sag"]="sag"
    ["postgres"]="docker"
    ["dashboard"]="hermes-dashboard"
)

# Rate limit: 10 minutes (600 seconds) per service
RATE_LIMIT_SECS=600

check_rate_limit() {
    local svc="$1"
    local last_ts=0
    
    if [ -f "${RL_FILE}" ]; then
        last_ts="$(python3 -c "
import json, sys
try:
    data = json.load(open('${RL_FILE}'))
except:
    print(0); sys.exit(0)
print(data.get('${svc}', {}).get('ts', 0))
" 2>/dev/null || echo 0)"
    fi
    
    local elapsed=$(( NOW_EPOCH - last_ts ))
    if [ "${elapsed}" -lt "${RATE_LIMIT_SECS}" ]; then
        return 1  # Rate limited
    fi
    return 0  # OK to proceed
}

update_rate_limit() {
    local svc="$1"
    
    python3 -c "
import json, sys, os
rl_file = '${RL_FILE}'
try:
    data = json.load(open(rl_file))
except:
    data = {}
data['${svc}'] = {'ts': ${NOW_EPOCH}, 'iso': '${NOW_ISO}'}
os.makedirs(os.path.dirname(rl_file), exist_ok=True)
with open(rl_file, 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null || true
}

for svc in ${FAILED_SERVICES}; do
    unit="${SVC_MAP[$svc]:-$svc}"
    
    if ! check_rate_limit "${unit}"; then
        cron_warn "Rate-limited: skipping restart of ${unit} (within 10min window)"
        push_manual "rate_limited:${unit}"
        continue
    fi
    
    cron_ok "Attempting systemctl restart of ${unit}..."
    
case "${unit}" in

    docker)
        # For postgres/docker, restart the container instead of systemd unit
        docker restart shared-postgres 2>/dev/null && {
            cron_ok "Restarted docker container: shared-postgres"
            push_action "restarted:shared-postgres"
            update_rate_limit "${unit}"
        } || {
            cron_err "Failed to restart shared-postgres container"
            push_manual "restart_failed:shared-postgres"
        }
        ;;
    *)
        # Standard systemctl restart
        if systemctl restart "${unit}" 2>/dev/null; then
            sleep 3
            if systemctl is-active --quiet "${unit}" 2>/dev/null; then
                cron_ok "Successfully restarted ${unit}"
                push_action "restarted:${unit}"
                update_rate_limit "${unit}"
            else
                cron_err "${unit} still inactive after restart"
                push_manual "still_inactive:${unit}"
                update_rate_limit "${unit}"
            fi
        else
            cron_err "systemctl restart failed for ${unit}"
            push_manual "restart_failed:${unit}"
            update_rate_limit "${unit}"
        fi
        ;;
esac

done

cron_section "Step D: Extra checks (smb-mounts, wsl-keepalive, mountpoints, sag-mcp-bridge, Bifrost LLM, Hindsight recall)"

EXTRA_CHECKS=""

# D1: smb-mounts.service
if systemctl is-active --quiet smb-mounts.service 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} smb-mounts:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} smb-mounts:fail"
    cron_warn "smb-mounts.service not active"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

# D2: wsl-keepalive.service
if systemctl is-active --quiet wsl-keepalive.service 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} wsl-keepalive:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} wsl-keepalive:fail"
    cron_warn "wsl-keepalive.service not active"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

# D3: /mnt/c mountpoint check
if mountpoint -q /mnt/c 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} mnt_c:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} mnt_c:fail"
    cron_err "/mnt/c is not a valid mountpoint"
    INFRA_STATUS="fail"
fi

# D4: /mnt/d mountpoint check (may not exist on all systems)
if [ -d /mnt/d ]; then
    if mountpoint -q /mnt/d 2>/dev/null; then
        EXTRA_CHECKS="${EXTRA_CHECKS} mnt_d:ok"
    else
        EXTRA_CHECKS="${EXTRA_CHECKS} mnt_d:not_mounted"
        cron_warn "/mnt/d exists but is not a valid mountpoint (may be normal)"
        [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
    fi
else
    EXTRA_CHECKS="${EXTRA_CHECKS} mnt_d:not_present"
fi

# D5: sag-mcp-bridge.service
if systemctl is-active --quiet sag-mcp-bridge.service 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} sag-mcp-bridge:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} sag-mcp-bridge:fail"
    cron_warn "sag-mcp-bridge.service not active, attempting restart..."
    if check_rate_limit "sag-mcp-bridge"; then
        if systemctl restart sag-mcp-bridge.service 2>/dev/null; then
            sleep 3
            if systemctl is-active --quiet sag-mcp-bridge.service 2>/dev/null; then
                cron_ok "Restarted sag-mcp-bridge.service"
                push_action "restarted:sag-mcp-bridge"
                update_rate_limit "sag-mcp-bridge"
            else
                cron_err "sag-mcp-bridge still inactive after restart"
                push_manual "still_inactive:sag-mcp-bridge"
                update_rate_limit "sag-mcp-bridge"
            fi
        else
            cron_err "Failed to restart sag-mcp-bridge.service"
            push_manual "restart_failed:sag-mcp-bridge"
        fi
    else
        cron_warn "Rate-limited: skipping sag-mcp-bridge restart"
        push_manual "rate_limited:sag-mcp-bridge"
    fi
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

# D6: Bifrost LLM check — 复用 health-check-all.py 权威结果（config.json 统计模型数，16 个）
# 不再直接 curl /v1/models：该接口因 Bifrost 路由配置只返回部分模型（2 个），与权威值不一致。
BIFROST_INFO=""
BIFROST_INFO="$(python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    b = data.get('bifrost', {})
    if isinstance(b, dict):
        st = b.get('status', 'unknown')
        models = b.get('checks', {}).get('models_online', 0)
        print(f'{st}|{models}')
    else:
        print('unknown|0')
except:
    print('unknown|0')
" <<< "${HEALTH_JSON}" 2>/dev/null || echo "unknown|0")"
BIFROST_STATUS="${BIFROST_INFO%%|*}"
BIFROST_MODELS_N="${BIFROST_INFO##*|}"
if [ -n "${BIFROST_STATUS}" ] && [ "${BIFROST_STATUS}" = "ok" ]; then
    EXTRA_CHECKS="${EXTRA_CHECKS} bifrost_llm:ok(${BIFROST_MODELS_N}_models)"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} bifrost_llm:${BIFROST_STATUS:-unknown}"
    cron_warn "Bifrost LLM check not ok: status=${BIFROST_STATUS} models=${BIFROST_MODELS_N}"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

# D7: Hindsight recall endpoint check — 复用 health-check-all.py 权威结果（process + /health + PG）
HINDSIGHT_INFO=""
HINDSIGHT_INFO="$(python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    h = data.get('hindsight', {})
    if isinstance(h, dict):
        st = h.get('status', 'unknown')
        checks = h.get('checks', {})
        pg = checks.get('pg_connection', False)
        sysd = checks.get('systemd_active_state', 'unknown')
        print(f'{st}|pg={pg}|sysd={sysd}')
    else:
        print('unknown|pg=False|sysd=unknown')
except:
    print('unknown|pg=False|sysd=unknown')
" <<< "${HEALTH_JSON}" 2>/dev/null || echo "unknown|pg=False|sysd=unknown")"
HINDSIGHT_STATUS="${HINDSIGHT_INFO%%|*}"
if [ -n "${HINDSIGHT_STATUS}" ] && [ "${HINDSIGHT_STATUS}" = "ok" ]; then
    EXTRA_CHECKS="${EXTRA_CHECKS} hindsight_recall:ok(${HINDSIGHT_INFO#*|})"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} hindsight_recall:${HINDSIGHT_STATUS:-unknown}"
    cron_warn "Hindsight recall check not ok: ${HINDSIGHT_INFO}"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

cron_section "Step E: Check enabled cron jobs for last_status=error"

CRON_ERRORS=""
SELF_JOB_ID="9536bea31957"
JOBS_FILE="/root/.hermes/cron/jobs.json"

if [ -f "${JOBS_FILE}" ]; then
    CRON_ERRORS="$(python3 -c "
import json, sys
try:
    data = json.load(open('${JOBS_FILE}'))
except:
    print(''); sys.exit(0)
errors = []
for job in data.get('jobs', []):
    if not job.get('enabled', False):
        continue
    if job.get('id') == '${SELF_JOB_ID}':
        continue  # Skip self
    if job.get('last_status') == 'error':
        name = job.get('name', 'unknown')
        jid = job.get('id', '?')
        err_msg = job.get('last_error', '') or ''
        errors.append(f'{name}({jid}): {err_msg[:80]}')
print('\n'.join(errors))
" 2>/dev/null || echo "")"
fi

if [ -n "${CRON_ERRORS}" ]; then
    cron_warn "Cron jobs with errors:"
    echo "${CRON_ERRORS}" | while IFS= read -r line; do
        [ -n "$line" ] && cron_warn "  $line"
    done
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

cron_section "Step F: Write JSON report"

export REPORT_NOW_ISO="${NOW_ISO}"
export REPORT_INFRA_STATUS="${INFRA_STATUS}"
export REPORT_FAILED="${FAILED_SERVICES}"
export REPORT_WARN="${WARN_SERVICES}"
export REPORT_EXTRA="${EXTRA_CHECKS}"
export REPORT_ACTIONS="${ACTIONS_TAKEN}"
export REPORT_MANUAL="${NEEDS_MANUAL}"
export REPORT_FILE_PATH="${REPORT_FILE}"

echo "${HEALTH_JSON}" | python3 -c "
import json, os, sys

health_raw = {}
try:
    health_raw = json.loads(sys.stdin.read())
except:
    pass

report = {
    'timestamp': os.environ['REPORT_NOW_ISO'],
    'infra_status': os.environ['REPORT_INFRA_STATUS'],
    'health_check_raw': health_raw,
    'failed_services': os.environ.get('REPORT_FAILED', '').split(),
    'warn_services': os.environ.get('REPORT_WARN', '').split(),
    'extra_checks': [],
}

# Parse EXTRA_CHECKS name:status(detail) into structured objects
def _parse_extra(item: str):
    # format: name:status(detail)  e.g. bifrost_llm:ok(16_models)
    name, _, rest = item.partition(':')
    status = rest
    detail = ''
    if '(' in rest:
        status, _, detail = rest.partition('(')
        detail = detail.rstrip(')')
    return {'name': name.strip(), 'status': status.strip(), 'detail': detail.strip()}

for _it in os.environ.get('REPORT_EXTRA', '').strip().split():
    if _it:
        report['extra_checks'].append(_parse_extra(_it))

# Clean up empty lists
for key in ['failed_services', 'warn_services']:
     if not report[key]:
        report[key] = []

report['actions_taken'] = [x for x in os.environ.get('REPORT_ACTIONS', '').split() if x]
report['needs_manual'] = [x for x in os.environ.get('REPORT_MANUAL', '').split() if x]

os.makedirs(os.path.dirname(os.environ['REPORT_FILE_PATH']), exist_ok=True)
with open(os.environ['REPORT_FILE_PATH'], 'w') as f:
     json.dump(report, f, indent=2, ensure_ascii=False)
" 2>/dev/null || cron_warn "Failed to write JSON report"

cron_ok "Report written to ${REPORT_FILE}"

cron_section "Step G: Notify Feishu if failures or healing actions occurred"

# No news = good news. Only notify if there are failures or actions taken.
SHOULD_NOTIFY=false
NOTIFY_TITLE=""
NOTIFY_BODY=""

if [ "${INFRA_STATUS}" = "fail" ]; then
    SHOULD_NOTIFY=true
    NOTIFY_TITLE="[FAIL] System Health Self-Heal Report"
elif [ "${INFRA_STATUS}" = "warn" ]; then
    SHOULD_NOTIFY=true
    NOTIFY_TITLE="[WARN] System Health Self-Heal Report"
elif [ -n "${ACTIONS_TAKEN}" ]; then
    SHOULD_NOTIFY=true
    NOTIFY_TITLE="[HEALED] System Health Self-Heal Report"
fi

if [ "${SHOULD_NOTIFY}" = true ]; then
    # Build structured Markdown notification body
    NOTIFY_BODY="**状态**: ${INFRA_STATUS}"
    NOTIFY_BODY="${NOTIFY_BODY}
**时间**: ${NOW_ISO}"
    
    if [ -n "${FAILED_SERVICES}" ]; then
        NOTIFY_BODY="${NOTIFY_BODY}
**失败服务**: ${FAILED_SERVICES}"
    fi
    
    if [ -n "${WARN_SERVICES}" ]; then
        NOTIFY_BODY="${NOTIFY_BODY}
**告警服务**: ${WARN_SERVICES}"
    fi
    
    # Structured extra checks — one line per check, status icon + name + detail
    if [ -n "${EXTRA_CHECKS}" ]; then
        NOTIFY_BODY="${NOTIFY_BODY}
**补充检查**:"
        # Convert EXTRA_CHECKS into structured markdown lines via python
        EXTRA_MD="$(python3 -c "
import sys, os
items = os.environ.get('REPORT_EXTRA', '').strip().split()
lines = []
for it in items:
    name, _, rest = it.partition(':')
    status = rest
    detail = ''
    if '(' in rest:
        status, _, detail = rest.partition('(')
        detail = detail.rstrip(')')
    if status in ('ok', 'active'):
        icon = '✅'
    elif status in ('fail', 'not_mounted', 'not_present'):
        icon = '🔴'
    else:
        icon = '⚠️'
    line = f'- {icon} {name.strip()}'
    if detail:
        line += f' ({detail.strip()})'
    lines.append(line)
print('\n'.join(lines))
" 2>/dev/null || echo '')"
        NOTIFY_BODY="${NOTIFY_BODY}
${EXTRA_MD}"
    fi
    
    if [ -n "${ACTIONS_TAKEN}" ]; then
        NOTIFY_BODY="${NOTIFY_BODY}
**已执行修复**: ${ACTIONS_TAKEN}"
    fi
    
    if [ -n "${NEEDS_MANUAL}" ]; then
        NOTIFY_BODY="${NOTIFY_BODY}
**需人工处理**: ${NEEDS_MANUAL}"
    fi
    
    if [ -n "${CRON_ERRORS}" ]; then
        NOTIFY_BODY="${NOTIFY_BODY}
**Cron 错误**:
${CRON_ERRORS}"
    fi
    
    cron_notify "${NOTIFY_TITLE}" "${NOTIFY_BODY}"
else
    cron_ok "All systems healthy - no notification needed"
fi

cron_section "Step H: Finish"

cron_finish
