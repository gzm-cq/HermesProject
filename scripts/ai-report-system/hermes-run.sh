#!/usr/bin/env bash
# =============================================================================
# AI Report System — 统一入口脚本
# 自动使用 Hermes venv 的 Python 3.11 运行，保证测试和运行环境一致
# Usage:
#   ./hermes-run.sh -m pytest tests/ -v          # 跑测试
#   ./hermes-run.sh -c "from ai_report...; print(...)"  # 执行代码
#   ./hermes-run.sh path/to/script.py             # 运行脚本
# =============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="${HOME}/.hermes/hermes-agent/venv/bin/python3"
VENV_PIP="${HOME}/.hermes/hermes-agent/venv/bin/pip"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "❌ 错误: 找不到 Hermes venv Python: $VENV_PYTHON"
    exit 1
fi

# 设置环境
# PYTHONPATH 包含 compat/、src/ 和项目根，兼容新旧两种导入方式
# 新方式: from ai_report.xxx import ... (通过项目根 + editable install)
# 旧方式: from src.xxx import ...   (通过 compat/src/ 兼容 shim 转发)
# 旧方式: from src.xxx import ...   (也可通过 src/ 前缀直接访问 ai_report 子包)
export PYTHONPATH="${PROJECT_DIR}/compat:${PROJECT_DIR}/src${PYTHONPATH:+:$PYTHONPATH}"
export TAVILY_API_KEY="${TAVILY_API_KEY:-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}"
export HERMES_AGENT_PATH="${HOME}/.hermes/hermes-agent"

exec "$VENV_PYTHON" "$@"
