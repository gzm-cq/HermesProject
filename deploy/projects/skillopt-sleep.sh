#!/bin/bash
# deploy/projects/skillopt-sleep.sh — SkillOpt-Sleep 技能优化引擎部署脚本
# 可直接执行: ./deploy/projects/skillopt-sleep.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> skillopt-sleep [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="skillopt-sleep"
PROJECT_SRC_REL="scripts/skillopt-sleep"
PROJECT_TGT="/root/.hermes/skillopt-sleep"
PROJECT_SVC=""

# 无技能需要部署
SKILLS_SRC=""
SKILLS_TGT=""

LEGACY_FILES=()

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"