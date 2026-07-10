#!/bin/bash
# dream-daily.sh — cron wrapper for dream-daily.py
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$SCRIPT_DIR"

python3 scripts/dream-daily.py "$@" 2>&1 | logger -t dream-daily