#!/bin/bash
# deploy-cleanup-health-check.sh — 部署闭环巡检（知识导航 / 知识树 / 飞轮报告）
#
# 用途：每次部署后一键验证三个模块在生产环境的闭环状态。
# 部署路径: /root/.hermes/scripts/deploy-cleanup-health-check.sh
# 调度: 手动或部署后触发（非定时）
#
# 关键点：统一注入 knowledge-navigation + knowledge-tree-plugin 两个 src 到
#         PYTHONPATH，避免单独注入 kt/src 时 turn_gate 因 knowledge_navigation
#         不在 path 而触发"门控降级为全放行"的误报警。

set -uo pipefail

# ===== 加载公共库 =====
_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    source "$_CRON_LIB"
else
    echo "警告：找不到 cron_common.sh，降级为纯 stdout 模式" >&2
    cron_init()    { echo "[init] $1"; }
    cron_section() { echo "=== $1 ==="; }
    cron_ok()      { echo "  ✅ $1"; }
    cron_warn()    { echo "  ⚠️  $1"; }
    cron_finish()  { echo "[finish]"; }
fi

cron_init "deploy-cleanup-health-check"

# ===== 环境路径（统一注入，消除 turn_gate 误报警）=====
KN_SRC="/root/.hermes/plugins/knowledge-navigation/src"
KT_SRC="/root/.hermes/plugins/knowledge-tree-plugin/src"
FHR_SRC="/root/.hermes/scripts/flywheel-health-report/src"
export PYTHONPATH="${KN_SRC}:${KT_SRC}:${FHR_SRC}:${PYTHONPATH:-}"

# ===== 1. 知识树插件：_USER_BUDGET_CHARS 常量 + 门控加载 =====
cron_section "知识树插件 (knowledge-tree-plugin)"
_KT_OK=$(python3 -c "
import sys, logging
logging.basicConfig(level=logging.CRITICAL)  # 抑制降级告警噪声
from knowledge_tree_plugin.extract_new import _USER_BUDGET_CHARS
from knowledge_tree_plugin import hooks as H
# 若 turn_gate 降级，skip 函数会是 _passthrough；正常应为 skip_non_user
name = getattr(H._skip_non_user, '__name__', '')
print('OK' if (_USER_BUDGET_CHARS == 800 and name == 'skip_non_user') else 'FAIL', _USER_BUDGET_CHARS, name)
" 2>/dev/null || echo "FAIL -")
if [[ "$_KT_OK" == OK* ]]; then
    cron_ok "知识树: _USER_BUDGET_CHARS=800 就位, turn_gate 门控正常加载 (无降级)"
else
    cron_warn "知识树: 检查失败 [$_KT_OK] — 常量缺失或 turn_gate 降级"
fi

# ===== 2. 知识导航插件：三路召回入口 + 真实 DB 三路表 =====
cron_section "知识导航插件 (knowledge-navigation)"
_KN_OK=$(python3 -c "
import sys
from knowledge_navigation.core.hooks import pre_llm_call
from knowledge_navigation.core.env_loader import get_env
import psycopg2
c = psycopg2.connect(get_env('KT_DB_URL',''), connect_timeout=8); cur = c.cursor()
cur.execute(\"SELECT (SELECT count(*) FROM memory_units),(SELECT count(*) FROM memory_links WHERE link_type IN ('causes','caused_by'))\")
mu, cl = cur.fetchone(); c.close()
print('OK' if (pre_llm_call and mu > 0 and cl > 0) else 'FAIL', mu, cl)
" 2>/dev/null || echo "FAIL -")
if [[ "$_KN_OK" == OK* ]]; then
    cron_ok "知识导航: 三路召回入口可加载, 真实DB memory_units/causal_links 存活 [$_KN_OK]"
else
    cron_warn "知识导航: 检查失败 [$_KN_OK]"
fi

# ===== 3. 飞轮报告：CLI 可加载 + 生产文件含 P3 修复 =====
cron_section "飞轮报告 (flywheel-health-report)"
_FHR_OK=$(python3 -c "
import flywheel_health_report.cli as C
# 验证 P3-A 加权平均已部署：report.py 中存在按 count 加权逻辑
import inspect, flywheel_health_report.report as R
src = inspect.getsource(R.generate_report)
has_weighted = 'avg_score' in src and 'count' in src and '*' in src
print('OK' if has_weighted else 'FAIL')
" 2>/dev/null || echo "FAIL -")
if [[ "$_FHR_OK" == OK* ]]; then
    cron_ok "飞轮报告: CLI 可加载, P3 加权平均修复已在生产就位"
else
    cron_warn "飞轮报告: 检查失败 [$_FHR_OK]"
fi

# ===== 4. 工作区 / git 一致性 =====
cron_section "版本一致性"
cd /mnt/d/HermesProject 2>/dev/null || cd /root/.hermes 2>/dev/null || true
_UNCOMMITTED=$(git status --short 2>/dev/null | wc -l)
if [[ "$_UNCOMMITTED" -eq 0 ]]; then
    cron_ok "工作区干净 (无未提交改动)"
else
    cron_warn "工作区有 $_UNCOMMITTED 项未提交改动"
fi

cron_finish
