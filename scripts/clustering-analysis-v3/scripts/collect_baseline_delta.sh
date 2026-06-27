#!/usr/bin/env bash
# collect_baseline_delta.sh — no_agent cron wrapper for knowledge-navigation baseline.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
source /root/.hermes/.env 2>/dev/null || true
# collect_baseline.py 属于 knowledge-navigation 项目
KN_SCRIPT="/root/.hermes/plugins/knowledge-navigation/scripts/collect_baseline.py"
if [ ! -f "$KN_SCRIPT" ]; then
    echo "错误: $KN_SCRIPT 不存在" >&2
    exit 1
fi
python3 "$KN_SCRIPT" --delta --trigger
