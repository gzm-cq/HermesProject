#!/bin/bash
# test_agent_detection_layer.sh — 验证 agent 驱动模式下检测层脚本结构
#
# 旧版 test_heal_logic.sh 测试的 heal_service/restart_with_fallback 等
# 修复函数已在 agent 驱动改造中删除，该测试已过时。
# 本测试改为验证：
#   1. 脚本中不存在盲目重启/通知逻辑
#   2. 检测逻辑保留（Step A/B/D/E/F）
#   3. Step G 输出信号文件供 agent 消费
set -uo pipefail
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

SRC="/mnt/d/HermesProject/scripts/system-health-check/system-health-self-heal.sh"
SIGNAL_WRITER="/mnt/d/HermesProject/scripts/system-health-check/signal_writer.py"

echo "── 测试 1: 修复逻辑已移除（agent 接管决策）──"
for token in "heal_service" "restart_with_fallback" "wait_unit_active" \
             "verify_service_health" "check_rate_limit" "update_rate_limit" \
             "check_notify_cooldown" "update_notify_ts" "cron_notify"; do
    if grep -q "${token}" "${SRC}"; then
        bad "${token} 仍存在于脚本（应已移除）"
    else
        ok "${token} 已移除"
    fi
done

echo ""
echo "── 测试 2: 检测逻辑保留 ──"
for step in 'Step A' 'Step B' 'Step D' 'Step E' 'Step F'; do
    if grep -q "${step}" "${SRC}"; then
        ok "${step} 保留"
    else
        bad "${step} 缺失"
    fi
done

echo ""
echo "── 测试 3: 信号文件输出（Step G）──"
if grep -q 'Step G: Write signal file for agent consumption' "${SRC}"; then
    ok "Step G 信号文件输出存在"
else
    bad "Step G 缺失"
fi

if grep -q 'SIGNAL_FILE="${STATE_DIR}/health-signal.json"' "${SRC}"; then
    ok "SIGNAL_FILE 变量定义存在"
else
    bad "SIGNAL_FILE 变量定义缺失"
fi

if grep -q 'python3 "${SCRIPT_DIR}/signal_writer.py"' "${SRC}"; then
    ok "Step G 调用 signal_writer.py"
else
    bad "Step G 未调用 signal_writer.py"
fi

echo ""
echo "── 测试 4: signal_writer.py 存在且语法正确 ──"
if [ -f "${SIGNAL_WRITER}" ]; then
    ok "signal_writer.py 存在"
else
    bad "signal_writer.py 缺失"
fi

if python3 -c "import py_compile; py_compile.compile('${SIGNAL_WRITER}', doraise=True)" 2>/dev/null; then
    ok "signal_writer.py 语法通过"
else
    bad "signal_writer.py 语法错误"
fi

echo ""
echo "── 测试 5: bash 语法检查 ──"
if bash -n "${SRC}" 2>/dev/null; then
    ok "system-health-self-heal.sh 语法通过"
else
    bad "system-health-self-heal.sh 语法错误"
fi

echo ""
echo "══════════════════════════════"
echo "结果: ${PASS} 通过, ${FAIL} 失败"
[ "${FAIL}" = "0" ] && echo "ALL PASS ✅" || echo "SOME FAILED ❌"
exit "${FAIL}"
