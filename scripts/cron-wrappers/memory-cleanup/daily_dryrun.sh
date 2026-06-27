#!/bin/bash
# daily_dryrun.sh — memory-cleanup 定时任务 wrapper
#
# 部署路径: /root/.hermes/scripts/memory-cleanup/daily_dryrun.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   0 2 * * * (每日 02:00)
#
# 功能:
#   - flock 防重入
#   - 日志落盘 /root/.hermes/logs/cron/memory-cleanup-YYYYMMDD.log
#   - 飞书通知（成功/失败）
#   - 执行 memory-cleanup --vote 1 --apply

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
cron_init "memory-cleanup"

# ===== 执行清理 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

cron_section "记忆清理 (--vote 1 --apply)"
if bash run.sh --vote 1 --apply; then
    cron_ok "记忆清理完成"
    _STEP_RESULTS+=("✅ 记忆清理 --vote 1 --apply")
else
    rc=$?
    cron_err "记忆清理失败 (exit=$rc)"
    _STEP_RESULTS+=("❌ 记忆清理 --vote 1 --apply (exit=$rc)")
fi

# ===== 完成 =====
cron_finish
