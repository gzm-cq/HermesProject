#!/bin/bash
# health-check-cron.sh — system-health-check 定时任务 wrapper
#
# 部署路径: /root/.hermes/scripts/health-check-cron.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   30 2 * * * (每日 02:30)
#
# 功能:
#   - flock 防重入
#   - 日志落盘 /root/.hermes/logs/cron/system-health-check-YYYYMMDD.log
#   - 调用 health-check-run.py（自带飞书推送 + JSON 格式巡检报告）
#   - cron_finish 发送额外汇总通知

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
cron_init "system-health-check"

# ===== 执行巡检 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cron_section "系统健康巡检"
if python3 "${SCRIPT_DIR}/health-check-run.py"; then
    cron_ok "系统健康巡检完成"
    _STEP_RESULTS+=("✅ 系统健康巡检")
else
    rc=$?
    cron_err "系统健康巡检失败 (exit=$rc)"
    _STEP_RESULTS+=("❌ 系统健康巡检 (exit=$rc)")
fi

# ===== 完成 =====
cron_finish
