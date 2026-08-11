#!/bin/bash
# dream-daily.sh — cron wrapper for dream-daily.py
# API key from .env, not hardcoded
set -euo pipefail

# LLM 模型继承链兜底：DREAM_SYNTH_LLM_MODEL → LLM_MODEL_LIGHT
export DREAM_SYNTH_LLM_MODEL="${DREAM_SYNTH_LLM_MODEL:-${LLM_MODEL_LIGHT:-}}"

# 从 ~/.hermes/.env 加载 LITELLM_MASTER_KEY
if [ -f /root/.hermes/.env ]; then
    set -a
    source /root/.hermes/.env
    set +a
fi

cd /root/.hermes/scripts/dream-synth
python3 scripts/dream-daily.py "$@" 2>&1 | logger -t dream-daily
