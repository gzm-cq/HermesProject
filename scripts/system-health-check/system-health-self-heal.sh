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
FAIL_STATE_FILE="${STATE_DIR}/self-heal-failcount.json"
NOTIFY_STATE_FILE="${STATE_DIR}/self-heal-notify.json"
REPORT_FILE="${LOG_DIR}/system-health-self-heal-latest.json"

# P1-8: 连续失败升级阈值 — 同一服务连续失败达到该次数（每小时 1 次 cron）即停止自动修复转人工
MAX_CONSECUTIVE_FAILS=3

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
get_fail_count() {
    local svc="$1"
    python3 -c "
import json, os
path = '${FAIL_STATE_FILE}'
try:
    data = json.load(open(path))
except:
    data = {}
rec = data.get('${svc}', {})
count = rec.get('count', 0)
ts = rec.get('ts', 0)
if ${NOW_EPOCH} - ts > 86400:
    count = 0
print(count)
" 2>/dev/null || echo 0
}

set_fail_count() {
    local svc="$1"; local n="$2"
    python3 -c "
import json, os
path = '${FAIL_STATE_FILE}'
try:
    data = json.load(open(path))
except:
    data = {}
data['${svc}'] = {'count': ${n}, 'ts': ${NOW_EPOCH}}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null || true
}

# P0-2: 轮询等待 shared-postgres 容器内 PG 就绪（替代等 docker.service — docker 服务常驻但容器可能未起）
# 返回 0=PG ready, 1=超时仍不可用
wait_postgres_ready() {
    local max_wait="${1:-90}"
    local waited=0
    while [ "${waited}" -lt "${max_wait}" ]; do
        if docker exec shared-postgres pg_isready -U postgres -h 127.0.0.1 -q 2>/dev/null; then
            return 0
        fi
        sleep 3
        waited=$(( waited + 3 ))
    done
    return 1
}

cron_section "Step C: Attempt self-healing (systemctl restart with rate limit)"

# Map health-check service names to systemd unit names
# postgres 跑在 docker 容器 shared-postgres；unit 用 "docker" 标记，修复走 docker start + pg_isready 轮询（P0-2）
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

# P0-2: 轮询等待 systemd 单元 active（替代固定 sleep 3，适配启动慢的服务）
# 返回 0=active, 1=超时仍非 active
wait_unit_active() {
    local unit="$1"
    local max_wait="${2:-60}"
    local waited=0
    while [ "${waited}" -lt "${max_wait}" ]; do
        if systemctl is-active --quiet "${unit}" 2>/dev/null; then
            return 0
        fi
        # Restart=on-failure 会在退出后自动拉起；等 systemd 调度
        sleep 3
        waited=$(( waited + 3 ))
    done
    return 1
}

# P0-1: 修复后重新验证服务健康 — 重跑 health-check-all.py 读取该服务最新状态
# 返回 0=验证通过(ok), 1=验证失败(fail/warn/unreachable), 2=无法校验(服务不在健康检查清单)
verify_service_health() {
    local svc="$1"
    local vjson
    # P1-7: 单服务重查（--service 只跑该 check），避免每次重跑全量 8 项巡检
    vjson="$(python3 "${SCRIPT_DIR}/health-check-all.py" --service "${svc}" 2>/dev/null || echo '{}')"
    if [ -z "${vjson}" ] || [ "${vjson}" = "{}" ]; then
        return 2
    fi
    local vstatus
    vstatus="$(python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    info = data.get('${svc}', {})
    if isinstance(info, dict):
        print(info.get('status', 'unknown'))
    else:
        print('unknown')
except:
    print('unknown')
" <<< "${vjson}" 2>/dev/null || echo "unknown")"
    if [ "${vstatus}" = "ok" ]; then
        return 0
    elif [ "${vstatus}" = "unknown" ]; then
        return 2
    else
        return 1
    fi
}

# P1-5: restart 失败时 SIGKILL fallback（仅对已知 SIGTERM 卡顿的服务）
# hermes-gateway 对 SIGTERM 有时不响应（graceful drain 卡住），用 SIGKILL + systemd 自动拉起
restart_with_fallback() {
    local unit="$1"
    if systemctl restart "${unit}" 2>/dev/null; then
        return 0
    fi
    case "${unit}" in
        hermes-gateway)
            cron_warn "systemctl restart ${unit} failed, trying SIGKILL fallback"
            systemctl kill --signal=SIGKILL "${unit}" 2>/dev/null
            sleep 5
            # systemd Restart=on-failure 自动拉起，等待 active
            if wait_unit_active "${unit}" 60; then
                return 0
            fi
            return 1
            ;;
        *)
            return 1
            ;;
    esac
}

# P0-1/P0-2: 完整修复流程 — restart + 轮询等待 + 重验健康
# 返回 0=修复成功, 1=修复失败(仍 inactive), 2=进程 active 但健康重验未过/无法校验
heal_service() {
    local svc="$1"
    local unit="$2"
    local detail="${3:-}"

    # P1-8: 连败升级 — 连续失败达阈值则停止自动修复，转人工
    local fails
    fails="$(get_fail_count "${svc}")"
    if [ "${fails}" -ge "${MAX_CONSECUTIVE_FAILS}" ]; then
        cron_warn "${svc}: ${fails} consecutive failures, escalating to manual (no auto-restart)"
        push_manual "escalated:${svc}"
        return 4
    fi

    if ! check_rate_limit "${unit}"; then
        cron_warn "Rate-limited: skipping restart of ${unit} (within 10min window)"
        push_manual "rate_limited:${unit}"
        return 3
    fi

    cron_ok "Attempting to heal ${svc} (${unit})..."

    local restart_ok=0
    if [ "${unit}" = "docker" ]; then
        # P0-2: postgres 在 docker 容器；先确保 docker 服务本身 active，再重启容器
        if ! systemctl is-active --quiet docker 2>/dev/null; then
            cron_warn "docker.service not active, starting it..."
            if ! systemctl start docker 2>/dev/null; then
                cron_err "Failed to start docker.service (needed for postgres)"
                push_manual "restart_failed:docker"
                update_rate_limit "${unit}"
                set_fail_count "${svc}" $(( fails + 1 ))
                return 1
            fi
        fi
        docker restart shared-postgres 2>/dev/null && restart_ok=0 || restart_ok=1
        if [ "${restart_ok}" = "0" ]; then
            cron_ok "Restarted docker container: shared-postgres"
        else
            cron_err "Failed to restart shared-postgres container"
            push_manual "restart_failed:shared-postgres"
            update_rate_limit "${unit}"
            set_fail_count "${svc}" $(( fails + 1 ))
            return 1
        fi
    else
        if restart_with_fallback "${unit}"; then
            cron_ok "Restarted ${unit}"
        else
            cron_err "Restart failed for ${unit}"
            push_manual "restart_failed:${unit}"
            update_rate_limit "${unit}"
            set_fail_count "${svc}" $(( fails + 1 ))
            return 1
        fi
    fi

    # P0-2: 轮询等待就绪 — postgres 等容器内 pg_isready，其余等 systemd 单元 active
    if [ "${unit}" = "docker" ]; then
        if ! wait_postgres_ready 90; then
            cron_err "PostgreSQL not ready after container restart"
            push_manual "still_inactive:postgres"
            update_rate_limit "${unit}"
            set_fail_count "${svc}" $(( fails + 1 ))
            return 1
        fi
    elif ! wait_unit_active "${unit}" 60; then
        cron_err "${unit} still inactive after restart"
        push_manual "still_inactive:${unit}"
        update_rate_limit "${unit}"
        set_fail_count "${svc}" $(( fails + 1 ))
        return 1
    fi

    update_rate_limit "${unit}"

    # P0-1: 重验健康 — 单服务重查 health-check-all.py 确认服务真正恢复
    case "${svc}" in
        hermes|bifrost|hindsight|sag|postgres|dashboard|mcp|memory_files|orphan_scan)
            if verify_service_health "${svc}"; then
                cron_ok "Verified ${svc} healthy after restart"
                set_fail_count "${svc}" 0
                push_action "healed:${svc}${detail:+(${detail})}"
                return 0
            else
                cron_err "${svc} still unhealthy after restart (recheck failed)"
                push_manual "recheck_failed:${svc}"
                set_fail_count "${svc}" $(( fails + 1 ))
                return 2
            fi
            ;;
        *)
            # 不在健康检查清单的服务（如 sag-mcp-bridge）：以进程 active 为准
            set_fail_count "${svc}" 0
            push_action "healed:${svc}${detail:+(${detail})}"
            return 0
            ;;
    esac
}

for svc in ${FAILED_SERVICES}; do
    unit="${SVC_MAP[$svc]:-$svc}"
    heal_service "${svc}" "${unit}"
done

# P1-4: WARN_SERVICES 策略 — 不自动重启（可能是瞬时抖动），记录到 needs_manual 提示
for svc in ${WARN_SERVICES}; do
    unit="${SVC_MAP[$svc]:-$svc}"
    cron_warn "Warn service ${svc} (${unit}) not auto-restarted (conservative policy)"
    push_manual "warn_observed:${svc}"
done

# P2: 通知冷却 — 同类告警（fail/warn/healed）在冷却期内不重复轰炸飞书
NOTIFY_COOLDOWN_SECS=21600  # 6 小时

check_notify_cooldown() {
    local level="$1"
    python3 -c "
import json, os
path = '${NOTIFY_STATE_FILE}'
try:
    data = json.load(open(path))
except:
    data = {}
last = data.get('${level}', 0)
print(0 if ${NOW_EPOCH} - last >= ${NOTIFY_COOLDOWN_SECS} else 1)
" 2>/dev/null || echo 0
}

update_notify_ts() {
    local level="$1"
    python3 -c "
import json, os
path = '${NOTIFY_STATE_FILE}'
try:
    data = json.load(open(path))
except:
    data = {}
data['${level}'] = ${NOW_EPOCH}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null || true
}

cron_section "Step D: Extra checks (smb-mounts, wsl-keepalive, mountpoints, sag-mcp-bridge, Bifrost LLM, Hindsight recall)"

EXTRA_CHECKS=""

# D1: smb-mounts.service — 检测到 inactive 时用 heal_service 自动修复
if systemctl is-active --quiet smb-mounts.service 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} smb-mounts:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} smb-mounts:fail"
    cron_warn "smb-mounts.service not active, attempting heal..."
    if ! heal_service "smb-mounts" "smb-mounts.service"; then
        [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
    fi
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
    cron_err "/mnt/c is not a valid mountpoint, attempting heal..."
    if ! heal_service "smb-mounts" "smb-mounts.service"; then
        INFRA_STATUS="fail"
    fi
fi

# D4: /mnt/d mountpoint check — 检测到未挂载时重启 smb-mounts.service 修复
if [ -d /mnt/d ]; then
    if mountpoint -q /mnt/d 2>/dev/null; then
        EXTRA_CHECKS="${EXTRA_CHECKS} mnt_d:ok"
    else
        EXTRA_CHECKS="${EXTRA_CHECKS} mnt_d:not_mounted"
        cron_warn "/mnt/d not a valid mountpoint, attempting heal..."
        if ! heal_service "smb-mounts" "smb-mounts.service"; then
            [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
        fi
    fi
else
    EXTRA_CHECKS="${EXTRA_CHECKS} mnt_d:not_present"
fi

# D5: sag-mcp-bridge.service — 检测到 inactive 时用 heal_service 统一闭环（轮询等待 + 重验）
if systemctl is-active --quiet sag-mcp-bridge.service 2>/dev/null; then
    EXTRA_CHECKS="${EXTRA_CHECKS} sag-mcp-bridge:ok"
else
    EXTRA_CHECKS="${EXTRA_CHECKS} sag-mcp-bridge:fail"
    cron_warn "sag-mcp-bridge.service not active, attempting heal..."
    # P1-6: 修复成功则不再降级 INFRA_STATUS；失败（含限流/升级）才降级为 warn
    if ! heal_service "sag-mcp-bridge" "sag-mcp-bridge.service"; then
        [ "${INFRA_STATUS}" = "ok" ] && INFRA_STATUS="warn"
    fi
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
    # P0-3: bifrost_llm 非 ok → 尝试修复（rate limit + restart + 轮询等待 + 重验）
    EXTRA_CHECKS="${EXTRA_CHECKS} bifrost_llm:${BIFROST_STATUS:-unknown}"
    cron_warn "Bifrost LLM check not ok: status=${BIFROST_STATUS} models=${BIFROST_MODELS_N}, attempting heal..."
    heal_service "bifrost" "bifrost"
    # P1-6: 修复后刷新状态
    NEW_BIFROST_INFO="$(python3 -c "
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
" <<< "$(python3 "${SCRIPT_DIR}/health-check-all.py" 2>/dev/null || echo '{}')" 2>/dev/null || echo "unknown|0")"
    NEW_BIFROST_STATUS="${NEW_BIFROST_INFO%%|*}"
    NEW_BIFROST_MODELS_N="${NEW_BIFROST_INFO##*|}"
    if [ "${NEW_BIFROST_STATUS}" = "ok" ]; then
        EXTRA_CHECKS="${EXTRA_CHECKS} bifrost_llm_after_heal:ok(${NEW_BIFROST_MODELS_N}_models)"
    else
        EXTRA_CHECKS="${EXTRA_CHECKS} bifrost_llm_after_heal:${NEW_BIFROST_STATUS:-unknown}"
        INFRA_STATUS="fail"
    fi
    # P1-6: 修复成功（NEW=ok）保持原状态，不再无条件降级 warn
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
    # P0-3: hindsight_recall 非 ok → 尝试修复（rate limit + restart + 轮询等待 + 重验）
    EXTRA_CHECKS="${EXTRA_CHECKS} hindsight_recall:${HINDSIGHT_STATUS:-unknown}"
    cron_warn "Hindsight recall check not ok: ${HINDSIGHT_INFO}, attempting heal..."
    heal_service "hindsight" "hindsight-daemon"
    # P1-6: 修复后刷新状态
    NEW_HINDSIGHT_INFO="$(python3 -c "
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
" <<< "$(python3 "${SCRIPT_DIR}/health-check-all.py" 2>/dev/null || echo '{}')" 2>/dev/null || echo "unknown|pg=False|sysd=unknown")"
    NEW_HINDSIGHT_STATUS="${NEW_HINDSIGHT_INFO%%|*}"
    if [ "${NEW_HINDSIGHT_STATUS}" = "ok" ]; then
        EXTRA_CHECKS="${EXTRA_CHECKS} hindsight_recall_after_heal:ok(${NEW_HINDSIGHT_INFO#*|})"
    else
        EXTRA_CHECKS="${EXTRA_CHECKS} hindsight_recall_after_heal:${NEW_HINDSIGHT_STATUS:-unknown}"
        INFRA_STATUS="fail"
    fi
    # P1-6: 修复成功（NEW=ok）保持原状态，不再无条件降级 warn
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

cron_section "Step G: Notify Feishu if failures or healing actions occurred"

# No news = good news. Only notify if there are failures or actions taken.
SHOULD_NOTIFY=false
NOTIFY_TITLE=""
NOTIFY_BODY=""

if [ "${INFRA_STATUS}" = "fail" ]; then
    if [ "$(check_notify_cooldown fail)" = "0" ]; then
        SHOULD_NOTIFY=true
        NOTIFY_TITLE="[FAIL] System Health Self-Heal Report"
        update_notify_ts fail
    fi
elif [ "${INFRA_STATUS}" = "warn" ]; then
    if [ "$(check_notify_cooldown warn)" = "0" ]; then
        SHOULD_NOTIFY=true
        NOTIFY_TITLE="[WARN] System Health Self-Heal Report"
        update_notify_ts warn
    fi
elif [ -n "${ACTIONS_TAKEN}" ]; then
    # 成功修复值得通知，但同样走冷却避免每轮轰炸
    if [ "$(check_notify_cooldown healed)" = "0" ]; then
        SHOULD_NOTIFY=true
        NOTIFY_TITLE="[HEALED] System Health Self-Heal Report"
        update_notify_ts healed
    fi
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
