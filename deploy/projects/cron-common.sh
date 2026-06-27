#!/bin/bash
# deploy/projects/cron-common.sh — cron 公共库部署

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="cron-common"
PROJECT_SRC_REL="scripts"
PROJECT_TGT="/root/.hermes/lib"
PROJECT_SVC=""

SKILLS_SRC=""
SKILLS_TGT=""

LEGACY_FILES=()

# Target is /root/.hermes/lib — dedicated directory, no other projects here.
FIRST_DEPLOY_CLEANUP="false"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
