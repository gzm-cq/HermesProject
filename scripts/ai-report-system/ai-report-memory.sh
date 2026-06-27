#!/usr/bin/env bash
# =============================================================================
# 🚀 AI报告增强记忆系统 — 一键启动器
# =============================================================================
# 用法:
#   ./ai-report-memory.sh                  交互模式（自动检测模板）
#   ./ai-report-memory.sh "你的查询"        单次查询（输出增强提示词）
#
# 一条命令搞定，无需任何配置。
# 自动加载 Honcho 记忆，4模板根据关键词自动匹配。
# =============================================================================
set -euo pipefail

HERMES_VENV_PYTHON="${HOME}/.hermes/hermes-agent/venv/bin/python3"
LAUNCHER="${HOME}/.hermes/ai_report_launcher.py"

# 检查环境
if [ ! -f "$LAUNCHER" ]; then
    echo "❌ 找不到启动器: $LAUNCHER"
    exit 1
fi

if [ ! -x "$HERMES_VENV_PYTHON" ]; then
    echo "❌ 找不到 Hermes Python: $HERMES_VENV_PYTHON"
    exit 1
fi

# 使用 Hermes venv Python 运行启动器
exec "$HERMES_VENV_PYTHON" "$LAUNCHER" "$@"
