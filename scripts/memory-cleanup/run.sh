#!/bin/bash
# memory-cleanup/run.sh — 统一入口脚本
#
# 用法:
#   ./run.sh              dry-run（只分类报告，不修改数据）
#   ./run.sh --apply      实际执行清理（修改 MEMORY.md + USER.md）
#   ./run.sh --help       查看全部参数
#
# 运行优先级:
#   1. 已安装的 memory-cleanup CLI 命令
#   2. python3 -m memory_cleanup（直接从 src/ 运行，无需安装）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v memory-cleanup >/dev/null 2>&1; then
  exec memory-cleanup "$@"
else
  export PYTHONPATH="${SCRIPT_DIR}/src${PYTHONPATH:+:$PYTHONPATH}"
  exec python3 -m memory_cleanup "$@"
fi
