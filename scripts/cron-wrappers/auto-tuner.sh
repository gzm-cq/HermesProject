#!/bin/bash
# auto-tuner.sh — 飞轮参数自优化调优器（薄包装）
#
# 部署路径: /root/.hermes/scripts/auto-tuner.sh
# 调度: 在 flywheel-health-report.sh 末尾自动调用
#
# 核心逻辑在 auto-tuner.py 中实现，本脚本仅负责：
#   1. 加载 cron_common.sh（提供飞书通知等公共函数）
#   2. 转发命令行参数给 Python 脚本

set -uo pipefail

# 定位脚本所在目录（兼容部署路径和开发路径）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 加载 cron_common.sh（如果存在）
_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    source "$_CRON_LIB"
fi

# 转发给 Python 实现
exec python3 "${SCRIPT_DIR}/auto-tuner.py" "$@"
