#!/bin/bash
# cron_job_template.sh — Hermes cron 任务模板（示例 / 新任务复制此文件）
#
# 用法：
#   1. 复制本文件为 <your-job>.sh
#   2. 修改 CRON_JOB_NAME 和 cron_init 参数
#   3. 在 "业务逻辑" 区域替换为你的命令
#   4. 每个步骤用 cron_run_step 或手动 cron_section + cron_ok/cron_err
#
# 调度建议（错峰执行）：
#   memory-cleanup       02:00 每日
#   system-health-check  02:30 每日
#   daily-learn          03:00 每日
#   clustering-analysis  04:00 每周日
#
# crontab 示例：
#   0 3 * * * /root/.hermes/scripts/daily-learn/daily_learn.sh

set -euo pipefail

# ===== 加载公共库 =====
# 部署后用绝对路径；开发时可改为相对路径
_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    # shellcheck disable=SC1090
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh (${_CRON_LIB})" >&2
    exit 2
fi

# ===== 配置 =====
CRON_JOB_NAME="my-job-name"         # ← 改这里：唯一标识（用于日志文件名和锁文件）
cron_init "$CRON_JOB_NAME"           # 初始化日志 + flock 防重入

# ===== 业务逻辑 =====

# 方式 A：用 cron_run_step 自动跟踪状态
# cron_run_step "数据收集" python3 -m my_module.collect
# cron_run_step "数据处理" python3 -m my_module.process

# 方式 B：手动控制（适合需要复杂条件判断的步骤）
cron_section "示例步骤"
if echo "hello world"; then
    cron_ok "示例步骤完成"
    _STEP_RESULTS+=("✅ 示例步骤")
else
    cron_err "示例步骤失败"
    _STEP_RESULTS+=("❌ 示例步骤")
fi

# ===== 完成 =====
cron_finish                          # 打印汇总 + 发飞书通知 + 设置退出码
