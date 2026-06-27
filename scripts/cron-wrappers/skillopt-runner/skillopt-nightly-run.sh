#!/bin/bash
# skillopt-nightly-run.sh — SkillOpt 技能优化定时任务 wrapper
#
# 部署路径: /root/.hermes/scripts/skillopt-runner/skillopt-nightly-run.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   0 15 * * * (每日 15:00)

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
cron_init "skillopt-nightly-run"

# ===== 执行技能优化 =====
SKILLOPT_DIR="/root/.hermes/skillopt-runner"
PYTHON_BIN="/root/.hermes/hermes-agent/venv/bin/python"

cron_section "SkillOpt 增量优化"
if cd "$SKILLOPT_DIR" && "$PYTHON_BIN" skillopt_runner.py; then
    cron_ok "SkillOpt 运行完成"
    _STEP_RESULTS+=("✅ SkillOpt 增量优化")
else
    rc=$?
    cron_err "SkillOpt 运行失败 (exit=$rc)"
    _STEP_RESULTS+=("❌ SkillOpt 增量优化 (exit=$rc)")
fi

# ===== 完成 =====
cron_finish
