#!/bin/bash
# self-evolving-nightly.sh — Self-Evolving 编排定时任务 wrapper（F-5）
#
# 部署路径: /root/.hermes/scripts/self-evolving/self-evolving-nightly.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   30 17 * * *（每日 17:30，在 skillopt 15:00 之后消费其失败轨迹，
#              且避开 dream-daily 16:00 的 LLM 网关高峰）
#
# 作用：把 SkillOpt 的失败轨迹喂给 Revision→Refinement 算子，完成能力飞轮闭环。

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
cron_init "self-evolving-nightly"

# ===== 执行 Self-Evolving 编排 =====
SELF_EVOLVING_DIR="${HERMES_HOME:-/root/.hermes}/scripts/self-evolving"
PYTHON_BIN="${HERMES_HOME:-/root/.hermes}/hermes-agent/venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(command -v python3 || echo python3)"
fi

cron_section "Self-Evolving Revision→Refinement（自动写回 SKILL.md）"
set +e
( cd "$SELF_EVOLVING_DIR" && "$PYTHON_BIN" scripts/self_evolving_driver.py --auto-apply )
rc=$?
set -e
if [[ $rc -eq 0 ]]; then
    cron_ok "Self-Evolving 运行完成"
    _STEP_RESULTS+=("✅ Self-Evolving 编排")
else
    cron_err "Self-Evolving 运行失败 (exit=$rc)"
    _STEP_RESULTS+=("❌ Self-Evolving 编排 (exit=$rc)")
fi

# ===== 完成 =====
cron_finish
