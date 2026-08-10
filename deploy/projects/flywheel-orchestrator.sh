#!/bin/bash
# deploy/projects/flywheel-orchestrator.sh — 飞轮编排器部署
#
# 注意：本部署项目为本次修复新增（原 flywheel-review 修复遗漏其部署通道）。
# 源: scripts/flywheel-orchestrator/ → 标: /root/.hermes/scripts/flywheel-orchestrator/

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="flywheel-orchestrator"
PROJECT_SRC_REL="scripts/flywheel-orchestrator"
PROJECT_TGT="/root/.hermes/scripts/flywheel-orchestrator"
PROJECT_SVC=""

SKILLS_SRC=""
SKILLS_TGT=""

LEGACY_FILES=()

# Target is a dedicated subdirectory under the shared /root/.hermes/scripts tree.
# Skip first-deploy full cleanup to avoid touching sibling projects.
FIRST_DEPLOY_CLEANUP="false"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
