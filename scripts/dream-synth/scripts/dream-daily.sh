#!/bin/bash
# dream-daily.sh — cron wrapper for dream-daily.py
#
# 部署路径: /root/.hermes/scripts/dream-synth/scripts/dream-daily.sh
# 调度建议:   0 16 * * * (每日 16:00)
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

# LLM 模型继承链兜底：DREAM_SYNTH_LLM_MODEL → LLM_MODEL_LIGHT
export DREAM_SYNTH_LLM_MODEL="${DREAM_SYNTH_LLM_MODEL:-${LLM_MODEL_LIGHT:-}}"

python3 dream-daily.py "$@" 2>&1 | logger -t dream-daily