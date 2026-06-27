#!/bin/bash
# deploy/projects/self-evolving.sh — 自进化飞轮项目部署脚本
# 可直接执行: ./deploy/projects/self-evolving.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> self-evolving [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="self-evolving"
PROJECT_SRC_REL="scripts/self-evolving"
PROJECT_TGT="/root/.hermes/scripts/self-evolving"
PROJECT_SVC=""

SKILLS_SRC="scripts/self-evolving/skills"
SKILLS_TGT="/root/.hermes/skills"

LEGACY_FILES=(
  "/root/.hermes/scripts/self-evolving.py"
)

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
