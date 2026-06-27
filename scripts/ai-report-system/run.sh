#!/usr/bin/env bash
# =============================================================================
# AI Report System — 快速运行入口
# 直接在 Hermes venv 中执行 Python 代码
# Usage:
#   ./run.sh script.py          # 运行脚本
#   ./run.sh -c "print(1+1)"    # 执行单行代码
#   ./run.sh -m module_name     # 运行模块
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"
exec ./hermes-run.sh "$@"
