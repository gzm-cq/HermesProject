#!/bin/bash
# deploy/projects/knowledge-tree-builder.sh — 知识树建树管线部署脚本
# 可直接执行: ./deploy/projects/knowledge-tree-builder.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> knowledge-tree-builder [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="knowledge-tree-builder"
PROJECT_SRC_REL="scripts/knowledge-tree-builder"
PROJECT_TGT="/root/.hermes/scripts/knowledge-tree-builder"
PROJECT_SVC=""

SKILLS_SRC="scripts/knowledge-tree-builder/skills"
SKILLS_TGT="/root/.hermes/skills"

LEGACY_FILES=(
  "/root/.hermes/scripts/knowledge-tree-builder.py"
  "/root/.hermes/scripts/knowledge-tree-consolidate.sh"
)

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
