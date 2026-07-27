#!/bin/bash
# kit-verify.sh — Hermes-Kit 安装验证
set -euo pipefail

KIT_HOME="${HERMES_KIT_HOME:-$HOME/.hermes-kit}"
KIT_CONFIG="$KIT_HOME/config.yaml"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PASS=0 FAIL=0

pass() { PASS=$((PASS+1)); echo "  ✅ $1"; }
fail() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

echo "=== 1. 配置文件 ==="
[[ -f "$KIT_CONFIG" ]] && pass "config.yaml 存在" || fail "config.yaml 缺失"

echo "=== 2. 插件部署 ==="
for p in knowledge-navigation knowledge-tree-plugin; do
  [[ -d "$HERMES_HOME/plugins/$p" ]] && pass "插件 $p 已部署" || fail "插件 $p 未部署"
done

echo "=== 3. 组件部署 ==="
for d in knowledge-tree-builder clustering-analysis-v3 memory-cleanup \
         skillopt-runner system-health-check dream-synth; do
  [[ -d "$HERMES_HOME/scripts/$d" ]] && pass "组件 $d 已部署" || fail "组件 $d 未部署"
done
[[ -d "$HERMES_HOME/skillopt-runner" ]] && pass "skillopt-runner 已部署" || fail "skillopt-runner 未部署"
[[ -f "$HERMES_HOME/lib/cron_common.sh" ]] && pass "cron-common 已部署" || fail "cron-common 未部署"

echo "=== 4. cron 任务 ==="
if command -v hermes >/dev/null 2>&1; then
  CNT=$(hermes cron list 2>/dev/null | grep -c 'Name:')
  [[ "$CNT" -ge 10 ]] && pass "cron 任务 $CNT 个 (≥10)" || fail "cron 任务仅 $CNT 个"
  hermes cron status 2>&1 | grep -qi "running" && pass "cron 调度器运行中" || fail "cron 调度器未运行"
else
  fail "hermes 未安装"
fi

echo "=== 5. 环境变量 ==="
[[ -f "$HERMES_HOME/.env" ]] && grep -q "HERMES_KIT" "$HERMES_HOME/.env" && pass "环境变量已注入" || fail "环境变量未注入"

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "✅ 全部通过 ($PASS/$PASS)"
else
  echo "⚠️  $PASS 通过, $FAIL 失败"
  exit 1
fi