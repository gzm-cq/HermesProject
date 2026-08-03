#!/bin/bash
# auto-tuner.sh — 飞轮参数自优化调优器（薄wrapper）
#
# 部署路径: /root/.hermes/scripts/flywheel-health-report/scripts/auto-tuner.sh
# 调度:     在 flywheel-health-report.sh 末尾自动调用
# 用法:     auto-tuner.sh [--dry-run] [--help]

set -euo pipefail

# 加载 cron 环境变量
CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$CRON_LIB" ]]; then
    source "$CRON_LIB"
fi

# 设置 PYTHONPATH
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="$PROJECT_DIR/src:${PYTHONPATH:-}"

exec python3 -m flywheel_health_report.auto_tuner.tuner "$@"
