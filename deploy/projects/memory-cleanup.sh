#!/bin/bash
# deploy/projects/memory-cleanup.sh — 记忆清理系统部署脚本
# 可直接执行: ./deploy/projects/memory-cleanup.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> memory-cleanup [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="memory-cleanup"
PROJECT_SRC_REL="scripts/memory-cleanup"
PROJECT_TGT="/root/.hermes/scripts/memory-cleanup"
PROJECT_SVC=""

LEGACY_FILES=(
  "/root/.hermes/scripts/memory-classify-v6.py"
)

SKILLS_SRC="scripts/memory-cleanup/skills"
SKILLS_TGT="/root/.hermes/skills"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
