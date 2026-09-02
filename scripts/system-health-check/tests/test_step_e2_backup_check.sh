#!/bin/bash
# test_step_e2_backup_check.sh — 验证 system-health-self-heal.sh Step E2
# （pg-daily-backup 任务存在性 + 备份新鲜度盲区兜底检查）
#
# 覆盖：
# 1. Step E2 section 存在于源码
# 2. 任务存在性检查（BACKUP_TASK_OK / jobs.json 中 pg-daily-backup 存在且 enabled）
# 3. 新鲜度判定逻辑（fresh / stale / missing 三分支，用隔离临时目录行为验证）
# 4. bash 语法检查
#
# 运行：bash tests/test_step_e2_backup_check.sh
# 或    python3 -m pytest tests/ -q --noconftest（经 test_agent_detection_layer.sh 联动）

set -uo pipefail
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

SRC="/mnt/d/HermesProject/scripts/system-health-check/system-health-self-heal.sh"
JOBS_FILE="/root/.hermes/cron/jobs.json"
WORKDIR="$(mktemp -d /tmp/e2-test.XXXXXX)"
trap 'rm -rf "${WORKDIR}"' EXIT

echo "── 测试 1: Step E2 section 存在 ──"
if grep -q 'cron_section "Step E2' "$SRC"; then ok "Step E2 section 存在于源码"; else bad "Step E2 section 缺失"; fi

echo ""
echo "── 测试 2: 任务存在性检查逻辑存在 ──"
for pat in 'BACKUP_TASK_OK=' 'name.*pg-daily-backup' 'pg-daily-backup cron job missing or disabled'; do
    if grep -qE "$pat" "$SRC"; then ok "$pat"; else bad "$pat 缺失"; fi
done

echo ""
echo "── 测试 3: 新鲜度判定逻辑存在（26h 阈值 + 三分支） ──"
for pat in 'DUMP_FRESH=' '26' 'BACKUP_DIR_DEFAULT=' 'stale:\*' 'missing)' 'no hindsight backup dump'; do
    if grep -qE "$pat" "$SRC"; then ok "$pat"; else bad "$pat 缺失"; fi
done

echo ""
echo "── 测试 4: 行为验证 — fresh / stale / missing 三分支 ──"
# 提取 Step E2 的判定核心（与源码一致的最小可执行片段），用工作目录伪造输入
run_freshness() {
    local dump_path="$1"   # 空 = missing
    local dir="$2"
    local out
    out=$(bash -c "
set +e
LATEST_DUMP=''
if [ -n '${dump_path}' ]; then
    LATEST_DUMP=\"\$(ls -t '${dir}'/hindsight_*.dump 2>/dev/null | head -1)\"
fi
DUMP_FRESH=''
if [ -n \"\${LATEST_DUMP}\" ]; then
    DUMP_FRESH=\"\$(python3 -c \"
import os, time, sys
try:
    age_h = (time.time() - os.path.getmtime('\${LATEST_DUMP}')) / 3600.0
    print('ok' if age_h <= 26 else f'stale:{age_h:.1f}h')
except:
    print('err')
\")\"
else
    DUMP_FRESH='missing'
fi
echo \"\${DUMP_FRESH}\"
" 2>/dev/null)
    echo "$out"
}

# fresh: 1 小时前
touch -d "1 hour ago" "${WORKDIR}/hindsight_fresh.dump"
r="$(run_freshness "${WORKDIR}/hindsight_fresh.dump" "${WORKDIR}")"
case "$r" in ok)      ok "fresh dump → ok";;
                   *) bad "fresh dump 期望 ok，实际 $r";; esac

# stale: 30 小时前
rm -f "${WORKDIR}"/hindsight_*.dump
touch -d "30 hours ago" "${WORKDIR}/hindsight_stale.dump"
r="$(run_freshness "${WORKDIR}/hindsight_stale.dump" "${WORKDIR}")"
case "$r" in stale:*)
    ok "stale dump → ${r} (触发告警)";;
    *) bad "stale dump 期望 stale:xx，实际 $r";; esac

# missing: 无 dump
rm -f "${WORKDIR}"/hindsight_*.dump
r="$(run_freshness "" "${WORKDIR}")"
case "$r" in missing) ok "无 dump → missing (触发告警)";;
                   *) bad "无 dump 期望 missing，实际 $r";; esac

echo ""
echo "── 测试 5: 真实 jobs.json 中 pg-daily-backup 存在且 enabled ──"
if [ -f "${JOBS_FILE}" ]; then
    python3 -c "
import json, sys
data = json.load(open('${JOBS_FILE}'))
for job in data.get('jobs', []):
    if job.get('name') == 'pg-daily-backup':
        sys.exit(0 if job.get('enabled', False) else 1)
sys.exit(1)
" 2>/dev/null && ok "pg-daily-backup 在 jobs.json 中存在且 enabled" || bad "pg-daily-backup 缺失/被禁用（巡检将告警）"
else
    bad "jobs.json 不存在: ${JOBS_FILE}"
fi

echo ""
echo "── 测试 6: bash 语法检查 ──"
if bash -n "$SRC" 2>/dev/null; then ok "system-health-self-heal.sh BASH OK"; else bad "bash 语法错误"; fi

echo ""
echo "══════════════════════════════"
echo "结果: ${PASS} 通过, ${FAIL} 失败"
[ "${FAIL}" = "0" ] && echo "ALL PASS ✅" || echo "SOME FAILED ❌"
exit "${FAIL}"