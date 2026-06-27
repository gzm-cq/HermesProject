#!/bin/bash
# deploy/projects/cron-wrappers.sh — cron 定时任务 wrapper 统一部署
# 可直接执行: ./deploy/projects/cron-wrappers.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> cron-wrappers [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="cron-wrappers"
PROJECT_SRC_REL="scripts/cron-wrappers"
PROJECT_TGT="/root/.hermes/scripts"
PROJECT_SVC=""

# 无技能需要部署
SKILLS_SRC=""
SKILLS_TGT=""

LEGACY_FILES=()

# Target is /root/.hermes/scripts — SHARED directory with many projects.
# Skip first-deploy cleanup to avoid deleting other projects' files.
FIRST_DEPLOY_CLEANUP="false"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
