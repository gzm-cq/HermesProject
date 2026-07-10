#!/bin/bash
# dream-daily.sh — cron wrapper for dream-daily.py
# API key from .env, not hardcoded
set -euo pipefail

# 从 ~/.hermes/.env 加载 LITELLM_MASTER_KEY
if [ -f /root/.hermes/.env ]; then
    set -a
    source /root/.hermes/.env
    set +a
fi

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$SCRIPT_DIR"

python3 scripts/dream-daily.py "$@" 2>&1 | logger -t dream-daily