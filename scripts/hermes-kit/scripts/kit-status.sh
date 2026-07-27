#!/bin/bash
# kit-status.sh — Hermes-Kit 组件状态检查
set -euo pipefail

KIT_HOME="${HERMES_KIT_HOME:-$HOME/.hermes-kit}"
KIT_CONFIG="$KIT_HOME/config.yaml"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PASS=0 FAIL=0
pass() { PASS=$((PASS+1)); echo "  ✅ $1"; }
fail() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

echo "=== 配置 ==="
[[ -f "$KIT_CONFIG" ]] && pass "config.yaml 存在" || fail "config.yaml 缺失"

echo "=== 插件 ==="
for p in knowledge-navigation knowledge-tree-plugin; do
  [[ -d "$HERMES_HOME/plugins/$p" ]] && pass "$p 已部署" || fail "$p 未部署"
done

echo "=== 组件 ==="
for d in cron-common knowledge-tree-builder clustering-analysis-v3 memory-cleanup \
         skillopt-runner system-health-check daily-learn dream-synth self-evolving; do
  [[ -d "$HERMES_HOME/scripts/$d" ]] && pass "$d 已部署" || fail "$d 未部署"
done

echo "=== cron 任务 ==="
if command -v hermes >/dev/null 2>&1; then
  hermes cron list 2>/dev/null | grep -c 'Name:' | awk '{if($1>=10) print "  ✅ "$1" 个 cron 任务"; else print "  ❌ 仅 "$1" 个 cron 任务"}'
else
  fail "hermes 未安装"
fi

echo ""
echo "=== 结果: $PASS 通过, $FAIL 失败 ==="
exit $FAIL