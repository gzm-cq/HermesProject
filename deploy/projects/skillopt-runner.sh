#!/bin/bash
# deploy/projects/skillopt-runner.sh — SkillOpt 技能优化运行器部署脚本
# 可直接执行: ./deploy/projects/skillopt-runner.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> skillopt-runner [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="skillopt-runner"
PROJECT_SRC_REL="scripts/skillopt-runner"
PROJECT_TGT="/root/.hermes/skillopt-runner"
PROJECT_SVC=""

# 无技能需要部署
SKILLS_SRC=""
SKILLS_TGT=""

LEGACY_FILES=()

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
