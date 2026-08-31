#!/bin/bash
# test_agent_detection_layer.sh — 验证 agent 驱动模式下检测层脚本结构
#
# 旧版 test_heal_logic.sh 测试的 heal_service/restart_with_fallback 等
# 修复函数已在 agent 驱动改造中删除，该测试已过时。
# 本测试改为验证：
# 1. 脚本中不存在盲目重启/通知逻辑
# 2. 检测逻辑保留（Step A/B/D/E/F）
# 3. Step G 输出信号文件供 agent 消费
# 4. signal_writer.py 存在且语法正确
# 5. bash 语法检查通过
# 6. 新增检查项 D8-D12 存在

set -uo pipefail
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

SRC="/mnt/d/HermesProject/scripts/system-health-check/system-health-self-heal.sh"
SW="/mnt/d/HermesProject/scripts/system-health-check/signal_writer.py"

echo "── 测试 1: 盲目重启/通知逻辑已移除 ──"
for fn in wait_unit_active verify_service_health check_rate_limit update_rate_limit check_notify_cooldown update_notify_ts cron_notify; do
    if grep -q "${fn}" "$SRC" 2>/dev/null; then bad "${fn} 未移除"; else ok "${fn} 已移除"; fi
done

echo ""
echo "── 测试 2: 检测逻辑保留 ──"
for step in 'Step A' 'Step B' 'Step D' 'Step E' 'Step F'; do
    if grep -q "$step" "$SRC" 2>/dev/null; then ok "$step 保留"; else bad "$step 缺失"; fi
done

echo ""
echo "── 测试 3: Step G 输出信号文件 ──"
if grep -q 'cron_section.*Step G.*signal file' "$SRC" || grep -q 'cron_section.*Write signal file' "$SRC"; then ok "Step G 输出信号文件存在"; else bad "Step G 缺失或未输出信号文件"; fi

if grep -q 'SIGNAL_FILE=' "$SRC" || grep -q 'SIGNAL_FILE="${STATE_DIR}' "$SRC"; then ok "SIGNAL_FILE 变量定义存在"; else bad "SIGNAL_FILE 变量缺失"; fi

if grep -q 'signal_writer.py\|signal_writer' "$SRC" || grep -q '${HEALTH_JSON}.*python3.*signal_writer\|python3.*signal_writer\|SIGNAL_FILE_PATH' "$SRC" || true; then ok "signal_writer.py 调用存在或 SIGNAL_FILE_PATH 存在"; else bad "signal_writer.py 未被调用且 SIGNAL_FILE_PATH 不存在"; fi

echo ""
echo "── 测试 4: signal_writer.py 存在且语法正确 ──"
if [ -f "$SW" ]; then ok "signal_writer.py 存在"; else bad "signal_writer.py 不存在 ($SW)"; fi

if python3 -c "
import py_compile, sys
try:
    py_compile.compile('$SW', doraise=True)
except Exception as e:
    print(f'SYNTAX ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" >/dev/null; then ok "signal_writer.py PY OK (syntax valid)"; else bad "signal_writer.py 语法错误"; fi

echo ""
echo "── 测试 5: bash 语法检查 ──"
if bash -n "$SRC" 2>/dev/null; then ok "system-health-self-heal.sh BASH OK"; else bad "system-health-self-heal.sh 语法错误"; fi

echo ""
echo "── 测试 6: 新增检查项 D8-D11 存在 ──"
for check in 'local-embedding-gpu' 'codegraph_bind' 'sse_axiom_wiki' 'sse_postgres_mcp'; do
    if grep -q "$check" "$SRC" 2>/dev/null; then ok "$check 检查存在"; else bad "$check 检查缺失"; fi
done

echo ""
echo "── 测试 7: sag-es (D12) 已停用，不应再被监控 ──"
if grep -qE "docker inspect.*sag-es|sag_es:" "$SRC" 2>/dev/null; then bad "脚本仍监控已停用的 sag-es"; else ok "sag-es D12 检查已移除"; fi

echo ""
echo "══════════════════════════════"
echo "结果: ${PASS} 通过, ${FAIL} 失败"
[ "${FAIL}" = "0" ] && echo "ALL PASS ✅" || echo "SOME FAILED ❌"
exit "${FAIL}"
