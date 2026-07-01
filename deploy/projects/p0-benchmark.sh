#!/bin/bash
# deploy/projects/p0-benchmark.sh — P0 Benchmark 性能验证部署脚本
# 可直接执行: ./deploy/projects/p0-benchmark.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> p0-benchmark [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="p0-benchmark"
PROJECT_SRC_REL="scripts/p0-benchmark"
PROJECT_TGT="/root/.hermes/scripts/p0-benchmark"
PROJECT_SVC=""

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
