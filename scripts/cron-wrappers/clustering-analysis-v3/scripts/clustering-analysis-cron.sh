#!/bin/bash
# clustering-analysis-cron.sh — 聚类分析定时任务外层 wrapper
#
# 部署路径: /root/.hermes/scripts/clustering-analysis-v3/scripts/clustering-analysis-cron.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   0 4 * * 0 (每周日 04:00)
#
# 功能:
#   - 外层提供 cron_common 统一能力（flock、日志、飞书、状态跟踪）
#   - 内层调用原 cron_wrapper.sh（保留 5 步管线逻辑）
#   - cron_wrapper.sh 内部也有 flock，形成双重保护

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
cron_init "clustering-analysis"

# ===== 执行聚类管线 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cron_section "聚类分析管线 (--apply)"
if CONFIRM_APPLY=I_UNDERSTAND_THIS_WRITES_HINDSIGHT bash "${SCRIPT_DIR}/cron_wrapper.sh" --apply; then
    cron_ok "聚类分析管线完成"
    _STEP_RESULTS+=("✅ 聚类分析管线 --apply")
else
    rc=$?
    cron_err "聚类分析管线失败 (exit=$rc)"
    _STEP_RESULTS+=("❌ 聚类分析管线 (exit=$rc)")
fi

# ===== 完成 =====
cron_finish
