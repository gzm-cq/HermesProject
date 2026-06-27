#!/bin/bash
# deploy/projects/system-health-check.sh — 系统健康巡检脚本部署

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="system-health-check"
PROJECT_SRC_REL="scripts/system-health-check"
PROJECT_TGT="/root/.hermes/scripts"
PROJECT_SVC=""

SKILLS_SRC=""
SKILLS_TGT=""

LEGACY_FILES=()

# Target is the shared /root/.hermes/scripts directory. Do not run first-deploy
# full-target cleanup here, or unrelated script projects under the same target
# would be treated as stale files.
FIRST_DEPLOY_CLEANUP="false"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
