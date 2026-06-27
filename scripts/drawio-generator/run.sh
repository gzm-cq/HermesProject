#!/usr/bin/env bash
# run.sh — drawio-generator CLI 入口
# 用法: ./run.sh <layout.json> <output.drawio|.svg>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/src${PYTHONPATH:+:}${PYTHONPATH:-}"

if [[ $# -lt 2 ]]; then
  echo "用法: $0 <layout.json> <output_path>" >&2
  exit 1
fi

exec python3 -m drawio_generator.render "$@"
