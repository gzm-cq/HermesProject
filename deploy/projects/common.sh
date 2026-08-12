#!/bin/bash
# deploy/projects/common.sh — 公共库部署（F-1 统一账本等）
# 可直接执行: ./deploy/projects/common.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> common [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="common"
PROJECT_SRC_REL="scripts/common"
PROJECT_TGT="/root/.hermes/scripts/common"
PROJECT_SVC=""

SKILLS_SRC=""
SKILLS_TGT=""

LEGACY_FILES=()

# Target is /root/.hermes/scripts/common — a dedicated subdir under the shared
# /root/.hermes/scripts directory (which holds many sibling projects). Opt out of
# the first-deploy full cleanup so we never touch those siblings as "stale".
FIRST_DEPLOY_CLEANUP="false"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
