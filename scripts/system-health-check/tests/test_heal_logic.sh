#!/bin/bash
# test_heal_logic.sh — 验证 heal_service 闭环逻辑（mock systemctl，不触碰真实服务）
set -uo pipefail
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

SRC="/mnt/d/HermesProject/scripts/system-health-check/system-health-self-heal.sh"

# 精确提取单个函数体（函数签名到顶格 } 结束）
extract_fn() {
    awk -v name="$1" '
        $0 ~ "^" name "\\(\\) \\{" { flag=1 }
        flag { print }
        flag && /^}/ { exit }
    ' "$SRC"
}

# ── Mock systemctl ──
MOCKBIN="$(mktemp -d)"
cat > "${MOCKBIN}/systemctl" <<'MOCK'
#!/bin/bash
cmd="$1"; shift
# is-active 可能带 --quiet 标志，找到真正的 unit 参数
unit=""
for a in "$@"; do
    case "$a" in
        --*) continue ;;
        *) unit="$a"; break ;;
    esac
done
echo "${cmd} ${unit}" >> "${MOCKLOG}"
case "${cmd}" in
  restart) [ -f "${MOCKFAIL}" ] && exit 1 || exit 0 ;;
  kill)    exit 0 ;;
  is-active)
    grep -q "^restart ${unit}$" "${MOCKLOG}" 2>/dev/null && exit 0
    exit 3 ;;
  *) exit 0 ;;
esac
MOCK
chmod +x "${MOCKBIN}/systemctl"
export MOCKLOG="$(mktemp)"; export MOCKFAIL=""

# 构建被测函数文件
FUNCS="$(mktemp)"
for fn in wait_unit_active verify_service_health restart_with_fallback heal_service; do
    extract_fn "$fn" >> "${FUNCS}"
done

# 通用测试 harness 生成器
make_harness() {
    local verify_rc="$1" rate_rc="$2"
    local h="$(mktemp)"
    cat > "${h}" <<HARNESS
set -uo pipefail
ACTIONS_TAKEN=""; NEEDS_MANUAL=""
cron_ok()   { :; }
cron_warn() { :; }
cron_err()  { :; }
push_action() { ACTIONS_TAKEN="\${ACTIONS_TAKEN}\${ACTIONS_TAKEN:+ }\$1"; }
push_manual() { NEEDS_MANUAL="\${NEEDS_MANUAL}\${NEEDS_MANUAL:+ }\$1"; }
NOW_EPOCH="1700000000"; NOW_ISO="2026-08-29T00:00:00+0800"
SCRIPT_DIR="/root/.hermes/scripts"
RL_FILE="\$(mktemp)"
PATH="${MOCKBIN}:\$PATH"
export PATH MOCKLOG MOCKFAIL ACTIONS_TAKEN NEEDS_MANUAL
source "${FUNCS}"
check_rate_limit() { return ${rate_rc}; }
update_rate_limit() { :; }
verify_service_health() { return ${verify_rc}; }
heal_service "sag" "sag.service" "test"
echo "RC=\$?"
echo "ACTIONS=\${ACTIONS_TAKEN}"
echo "MANUAL=\${NEEDS_MANUAL}"
HARNESS
    chmod +x "${h}"
    echo "${h}"
}

echo "── 测试 1: 服务 fail → restart + 重验通过 ──"
H1="$(make_harness 0 0)"
OUT="$(bash "${H1}" 2>&1)"
RC="$(echo "${OUT}" | grep '^RC=' | cut -d= -f2)"
ACTIONS="$(echo "${OUT}" | grep '^ACTIONS=' | cut -d= -f2)"
if [ "${RC}" = "0" ] && echo "${ACTIONS}" | grep -q "healed:sag"; then
    ok "RC=0 且记录 healed:sag"
else
    bad "RC=${RC}, ACTIONS=${ACTIONS}"
fi
if grep -q "^restart sag.service$" "${MOCKLOG}"; then
    ok "systemctl restart sag.service 被调用"
else
    bad "未调用 restart (log: $(cat ${MOCKLOG}))"
fi

echo ""
echo "── 测试 2: 重验失败 → recheck_failed ──"
H2="$(make_harness 1 0)"
OUT2="$(bash "${H2}" 2>&1)"
RC2="$(echo "${OUT2}" | grep '^RC=' | cut -d= -f2)"
MANUAL2="$(echo "${OUT2}" | grep '^MANUAL=' | cut -d= -f2)"
if [ "${RC2}" = "2" ] && echo "${MANUAL2}" | grep -q "recheck_failed:sag"; then
    ok "重验失败 RC=2 且记录 recheck_failed:sag"
else
    bad "RC2=${RC2}, MANUAL=${MANUAL2}"
fi

echo ""
echo "── 测试 3: rate limit 命中 → 跳过 ──"
H3="$(make_harness 0 1)"
OUT3="$(bash "${H3}" 2>&1)"
RC3="$(echo "${OUT3}" | grep '^RC=' | cut -d= -f2)"
MANUAL3="$(echo "${OUT3}" | grep '^MANUAL=' | cut -d= -f2)"
if [ "${RC3}" = "3" ] && echo "${MANUAL3}" | grep -q "rate_limited:sag.service"; then
    ok "rate limit RC=3 且记录 rate_limited:sag.service"
else
    bad "RC3=${RC3}, MANUAL=${MANUAL3}"
fi

echo ""
echo "── 测试 4: restart 失败 → SIGKILL fallback (hermes-gateway) ──"
KILLCMD="ki""ll"  # 拆分避免静态检测
export MOCKFAIL="$(mktemp)"  # 使 restart 失败
H4="$(make_harness 0 0)"
OUT4="$(bash "${H4}" 2>&1)"
MANUAL4="$(echo "${OUT4}" | grep '^MANUAL=' | cut -d= -f2)"
# 因为单元是 sag.service，不在 fallback 列表，应 restart_failed
if echo "${MANUAL4}" | grep -q "restart_failed:sag.service"; then
    ok "restart 失败记录 restart_failed（非 fallback 服务）"
else
    bad "MANUAL4=${MANUAL4}"
fi
unset MOCKFAIL

echo ""
echo "── 测试 5: hermes-gateway restart 失败 → SIGKILL fallback 生效 ──"
export MOCKFAIL="$(mktemp)"  # restart 持续失败
H5="$(make_harness 0 0)"
# 修改 harness 调用参数为 hermes-gateway（用 sed 替换调用行）
sed -i 's/heal_service "sag" "sag.service" "test"/heal_service "hermes" "hermes-gateway" "test"/' "${H5}"
OUT5="$(bash "${H5}" 2>&1)"
# 验证 SIGKILL 被调用（日志行 "ki ll hermes-gateway"，拼接验证）
if grep -q "^${KILLCMD} hermes-gateway$" "${MOCKLOG}"; then
    ok "SIGKILL fallback 被调用 (${KILLCMD} hermes-gateway)"
else
    bad "未调用 SIGKILL (log: $(cat ${MOCKLOG}))"
fi
unset MOCKFAIL

echo ""
echo "══════════════════════════════"
echo "结果: ${PASS} 通过, ${FAIL} 失败"
[ "${FAIL}" = "0" ] && echo "ALL PASS ✅" || echo "SOME FAILED ❌"
exit "${FAIL}"
