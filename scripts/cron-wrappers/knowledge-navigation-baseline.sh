#!/bin/bash
# knowledge-navigation-baseline.sh — 知识导航评估基线定时任务 wrapper
#
# 部署路径: /root/.hermes/scripts/knowledge-navigation-baseline.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   0 11 * * 1 (每周一 11:00)
#
# 功能:
#   - 调用 collect_baseline.py --delta --trigger
#   - 周期基线 delta 检测，发现回归时触发告警

set -euo pipefail

# ===== 加载公共库 =====
_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    # shellcheck disable=SC1090
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh (${_CRON_LIB})" >&2
    echo "请先部署 cron-common 项目: deploy.sh deploy cron-common" >&2
    exit 2
fi

# ===== 初始化 =====
cron_init "knowledge-navigation-baseline"

# ===== 环境准备 =====
PLUGIN_DIR="/root/.hermes/plugins/knowledge-navigation"
cd "$PLUGIN_DIR"

# 加载 Python 环境（如果 venv 存在）
if [[ -f venv/bin/activate ]]; then
    source venv/bin/activate
fi

# 加载共享环境变量
if [[ -f /root/.hermes/.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source /root/.hermes/.env
  set +a
fi

# ===== 执行基线采集 =====
cron_section "评估基线 delta 检测"
if python3 scripts/collect_baseline.py --delta --trigger; then
    cron_ok "评估基线检测完成"
    _STEP_RESULTS+=("✅ 评估基线 delta 检测")
else
    rc=$?
    cron_err "评估基线检测失败 (exit=$rc)"
    _STEP_RESULTS+=("❌ 评估基线 delta 检测 (exit=$rc)")
fi

# ===== 完成 =====
cron_finish
