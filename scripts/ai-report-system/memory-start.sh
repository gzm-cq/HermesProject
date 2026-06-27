#!/usr/bin/env bash
# =============================================================================
# 🧠 AI报告系统 — 增强记忆启动脚本
# =============================================================================
# 用法: 在项目目录下直接运行 ./memory-start.sh
# 启动后进入交互式对话，exit 退出回到常规5层记忆模式
# =============================================================================
set -euo pipefail

HERMES_PYTHON="${HOME}/.hermes/hermes-agent/venv/bin/python3"
MANAGER="${HOME}/.hermes/hermes_memory_manager.py"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

exec "$HERMES_PYTHON" "$MANAGER" start \
    --project "AI报告系统" \
    --path "$PROJECT_DIR"
