#!/bin/bash
# self-evolving-nightly.sh — Self-Evolving 编排定时任务 wrapper（F-5）
#
# 部署路径: /root/.hermes/scripts/self-evolving/self-evolving-nightly.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   30 17 * * *（每日 17:30，在 skillopt 15:00 之后消费其失败轨迹，
#              且避开 dream-daily 16:00 的 LLM 网关高峰）
#
# 作用：把 SkillOpt 的失败轨迹喂给 Revision→Refinement 算子，完成能力飞轮闭环。
#
# 2026-08-29 A+B 改造（驱动脚本侧）：
#   - 队列消费：处理过的 task 从 state.json 的 failed_tasks 移除，
#     不再每晚重跑同一批（此前连续四天跑的是完全相同的 10 个任务）。
#   - 全局去重：同一 task_id 挂在多个 skill 下只处理一次。
#   - 并发：默认 3 路，单项超时 900s（此前纯串行，单夜最长 5704s）。
#   - 写回护栏：SKILL.md 超 30000 字符、或待复核 SE 块达 8 个，
#     则该 skill 的 task 在前置阶段跳过（不调 LLM），需人工整合后自动恢复。
#
# 可调参数（环境变量或命令行，均可在 .env 下发）：
#   SE_MAX_WORKERS=3            LLM 并发度
#   SE_ITEM_TIMEOUT=900         单项 revise→refine 超时秒数
#   SE_SIMILARITY_THRESHOLD=0.9 与上次产出相似度超过此值则跳过写回
#   SE_SKILL_SOFT_MAX=12000     SKILL.md 软上限（超过仅告警）
#   SE_SKILL_HARD_MAX=30000     SKILL.md 硬上限（超过拒绝写回）
#   SE_MAX_BLOCK_COUNT=8        待复核 SE 块数量上限

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
