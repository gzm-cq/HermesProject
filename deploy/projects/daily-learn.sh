#!/bin/bash
# deploy/projects/daily-learn.sh — 每日在线学习脚本部署

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="daily-learn"
PROJECT_SRC_REL="scripts/cron-wrappers/daily-learn"
PROJECT_TGT="/root/.hermes/scripts/daily-learn"
PROJECT_SVC=""

SKILLS_SRC=""
SKILLS_TGT=""

LEGACY_FILES=()

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
