#!/bin/bash
# deploy/projects/recall-eval.sh — recall-eval 召回评估部署脚本
# 可直接执行: ./deploy/projects/recall-eval.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> recall-eval [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="recall-eval"
PROJECT_SRC_REL="scripts/recall-eval"
PROJECT_TGT="/root/.hermes/scripts/recall-eval"
PROJECT_SVC=""

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
