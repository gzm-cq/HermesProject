#!/bin/bash
# deploy/projects/ai-report-system.sh — AI 报告生成系统部署脚本
# 可直接执行: ./deploy/projects/ai-report-system.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> ai-report-system [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="ai-report-system"
PROJECT_SRC_REL="scripts/ai-report-system"
PROJECT_TGT="/root/.hermes/scripts/ai-report-system"
PROJECT_SVC=""

LEGACY_FILES=(
  "/root/.hermes/scripts/md2docx.py"
  "/root/.hermes/scripts/error_search.py"
)

SKILLS_SRC="scripts/ai-report-system/skills"
SKILLS_TGT="/root/.hermes/skills"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
