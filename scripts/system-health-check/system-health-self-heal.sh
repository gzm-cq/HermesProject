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
REPORT_FILE="${LOG_DIR}/system-health-self-heal-latest.json"
SIGNAL_FILE="${STATE_DIR}/health-signal.json"


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

# P1-8: 连败计数 — 记录每个服务的连续失败次数（用于升级到人工处理）
# 时间衰减：距上次失败超过 24h 视为归零，避免历史失败长期累积导致误 escalate
cron_section "Step C: Record failed/warn services for agent consumption"

# Map health-check service names to systemd unit names (for agent reference)
declare -A SVC_MAP=(
    ["hermes"]="hermes-gateway"
    ["bifrost"]="bifrost"
    ["hindsight"]="hindsight-daemon"
    ["sag"]="sag"
    ["postgres"]="docker"
    ["dashboard"]="hermes-dashboard"
)

# P1-4: WARN_SERVICES 策略 — 不自动重启（可能是瞬时抖动），记录供agent参考
for svc in ${WARN_SERVICES}; do
    cron_warn "Warn service ${svc} recorded for agent diagnosis"
done

cron_section "Step D: Extra checks (smb-mounts, wsl-keepalive, mountpoints, sag-mcp-bridge, Bifrost LLM, Hindsight recall)"

EXTRA_CHECKS=""

# D1: smb-mounts.service
if systemctl is-active --quiet smb-mounts.service 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} smb-mounts:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} smb-mounts:fail"
    cron_warn "smb-mounts.service not active"
fi

# D2: wsl-keepalive.service
if systemctl is-active --quiet wsl-keepalive.service 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} wsl-keepalive:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} wsl-keepalive:fail"
    cron_warn "wsl-keepalive.service not active"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

# D3: /mnt/c mountpoint check — 检测到未挂载时重启 smb-mounts.service 修复
if mountpoint -q /mnt/c 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} mnt_c:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} mnt_c:fail"
    cron_err "/mnt/c is not a valid mountpoint"
    INFRA_STATUS="fail"
fi

# D4: /mnt/d mountpoint check — 检测到未挂载时重启 smb-mounts.service 修复
if [ -d /mnt/d ]; then
    if mountpoint -q /mnt/d 2>/dev/null; then
        EXTRA_CHECKS="${EXTRA_CHECKS} mnt_d:ok"
    else
        EXTRA_CHECKS="${EXTRA_CHECKS} mnt_d:not_mounted"
        cron_warn "/mnt/d not a valid mountpoint"
    fi
else
    EXTRA_CHECKS="${EXTRA_CHECKS} mnt_d:not_present"
fi

# D5: sag-mcp-bridge.service
if systemctl is-active --quiet sag-mcp-bridge.service 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} sag-mcp-bridge:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} sag-mcp-bridge:fail"
    cron_warn "sag-mcp-bridge.service not active"
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
    EXTRA_CHECKS="${EXTRA_CHECKS} bifrost_llm:${BIFROST_STATUS:-unknown}(${BIFROST_MODELS_N}_models)"
    cron_warn "Bifrost LLM check not ok: status=${BIFROST_STATUS} models=${BIFROST_MODELS_N}"
    INFRA_STATUS="fail"
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
    EXTRA_CHECKS="${EXTRA_CHECKS} hindsight_recall:${HINDSIGHT_STATUS:-unknown}(${HINDSIGHT_INFO#*|})"
    cron_warn "Hindsight recall check not ok: ${HINDSIGHT_INFO}"
    INFRA_STATUS="fail"
fi

# D8: local-embedding-gpu.service (GPU embedding service, port 8082)
if systemctl is-active --quiet local-embedding-gpu.service 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} local-embedding-gpu:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} local-embedding-gpu:fail"
    cron_warn "local-embedding-gpu.service not active (SAG vector search depends on it)"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

# D9: codegraph bind mount (/mnt/d/HermesProject/.codegraph)
if mountpoint -q /mnt/d/HermesProject/.codegraph 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} codegraph_bind:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} codegraph_bind:not_mounted"
    cron_warn "/mnt/d/HermesProject/.codegraph not a valid mountpoint (codegraph MCP depends on it)"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

# D10: axiom-wiki SSE port 4143 connectivity check
# SSE endpoints stream indefinitely; use connect-timeout (not max-time) to avoid false negatives.
# curl exit code 0 or 28(timeout reading body) both mean the port is listening and responding.
if curl -s -o /dev/null --connect-timeout 3 --max-time 1 http://127.0.0.1:4143/sse 2>/dev/null || \
   [ $? -eq 28 ]; then
    EXTRA_CHECKS="${EXTRA_CHECKS} sse_axiom_wiki:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} sse_axiom_wiki:fail"
    cron_warn "axiom-wiki SSE port 4143 unreachable (connection refused or timeout)"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

# D11: postgres-mcp SSE port 4145 connectivity check
# Same SSE logic as D10: connect-timeout + accept exit code 28 (body read timeout).
if curl -s -o /dev/null --connect-timeout 3 --max-time 1 http://127.0.0.1:4145/sse 2>/dev/null || \
   [ $? -eq 28 ]; then
    EXTRA_CHECKS="${EXTRA_CHECKS} sse_postgres_mcp:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} sse_postgres_mcp:fail"
    cron_warn "postgres-mcp SSE port 4145 unreachable (connection refused or timeout)"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

# D12: sag-es container status (Elasticsearch for SAG)
SAG_ES_STATE="$(docker inspect --format='{{.State.Status}}' sag-es 2>/dev/null || echo 'not_found')"
if [ "${SAG_ES_STATE}" = "running" ]; then
    EXTRA_CHECKS="${EXTRA_CHECKS} sag_es:ok(running)"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} sag_es:${SAG_ES_STATE}"
    cron_warn "sag-es container not running (state: ${SAG_ES_STATE}) — SAG full-text search degraded"
    [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
fi

cron_section "Step E: Check enabled cron jobs for last_status=error"

CRON_ERRORS=""
# P0-1: 动态解析本 job 的 id（按 name 匹配），避免硬编码过期导致"跳过自己"失效；取不到时兜底当前 id
SELF_JOB_ID="$(python3 -c "
import json, sys
try:
    data = json.load(open('/root/.hermes/cron/jobs.json'))
except:
    print('746e0cb6039b'); sys.exit(0)
for job in data.get('jobs', []):
    if job.get('name') == 'system-health-self-heal':
        print(job.get('id', '746e0cb6039b')); sys.exit(0)
print('746e0cb6039b')
" 2>/dev/null || echo "746e0cb6039b")"
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

cron_section "Step G: Write signal file for agent consumption"

export SIGNAL_NOW_ISO="${NOW_ISO}"
export SIGNAL_INFRA_STATUS="${INFRA_STATUS}"
export SIGNAL_FAILED="${FAILED_SERVICES}"
export SIGNAL_WARN="${WARN_SERVICES}"
export SIGNAL_EXTRA="${EXTRA_CHECKS}"
export SIGNAL_FILE_PATH="${SIGNAL_FILE}"
export SIGNAL_CRON_ERRORS="${CRON_ERRORS}"

echo "${HEALTH_JSON}" | python3 "${SCRIPT_DIR}/signal_writer.py"

cron_ok "Signal file written to ${SIGNAL_FILE}"

cron_section "Step H: Finish"

cron_finish