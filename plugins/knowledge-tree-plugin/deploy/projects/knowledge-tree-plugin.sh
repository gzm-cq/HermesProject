#!/bin/bash
# deploy/projects/knowledge-tree-plugin.sh — 知识树插件部署脚本

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="knowledge-tree-plugin"
PROJECT_SRC_REL="plugins/knowledge-tree-plugin"
PROJECT_TGT="/root/.hermes/plugins/knowledge-tree-plugin"
PROJECT_SVC="hermes-gateway.service"

SKILLS_SRC="plugins/knowledge-tree-plugin/skills"
SKILLS_TGT="/root/.hermes/skills"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
