#!/bin/bash
# deploy/projects/flywheel-health-report.sh — 飞轮健康报告系统部署脚本
# 可直接执行: ./deploy/projects/flywheel-health-report.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> flywheel-health-report [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="flywheel-health-report"
PROJECT_SRC_REL="scripts/flywheel-health-report"
PROJECT_TGT="/root/.hermes/scripts/flywheel-health-report"
PROJECT_SVC=""

LEGACY_FILES=(
  "/root/.hermes/scripts/auto-tuner.py"
  "/root/.hermes/scripts/auto-tuner.sh"
  "/root/.hermes/scripts/flywheel-health-report.py"
  "/root/.hermes/scripts/flywheel-health-report.sh"
)

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"