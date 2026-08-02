#!/bin/bash
# auto-tuner.sh — 飞轮参数自优化调优器（薄wrapper，实际逻辑在同目录 auto-tuner.py）
#
# 部署路径: /root/.hermes/scripts/auto-tuner.sh
# 调度:     在 flywheel-health-report.sh 末尾自动调用
# 用法:     auto-tuner.sh [--dry-run] [--help]
#           — 所有参数原样透传给 Python 实现

set -euo pipefail

# 加载 cron 环境变量（cron_notify、FEISHU_CHAT_ID、HERMES_HOME 等）
CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$CRON_LIB" ]]; then
    # shellcheck disable=SC1090
    source "$CRON_LIB"
fi

# 定位同目录下的 auto-tuner.py（通过脚本自身路径解析，cwd 不影响）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_IMPL="$SCRIPT_DIR/auto-tuner.py"

if [[ ! -f "$PY_IMPL" ]]; then
    echo "错误: 找不到 Python 实现: $PY_IMPL" >&2
    exit 2
fi

# exec 替换进程，避免 bash 额外保留一层 shell
exec python3 "$PY_IMPL" "$@"
