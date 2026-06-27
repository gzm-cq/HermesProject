#!/bin/bash
# deploy/projects/knowledge-navigation.sh — 知识导航插件部署脚本
# 可直接执行: ./deploy/projects/knowledge-navigation.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> knowledge-navigation [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="knowledge-navigation"
PROJECT_SRC_REL="plugins/knowledge-navigation"
PROJECT_TGT="/root/.hermes/plugins/knowledge-navigation"
PROJECT_SVC="hermes-gateway.service"

SKILLS_SRC="plugins/knowledge-navigation/skills"
SKILLS_TGT="/root/.hermes/skills"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
