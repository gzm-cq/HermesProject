#!/bin/bash
# deploy/projects/drawio-generator.sh — draw.io 矢量图生成器部署脚本
# 可直接执行: ./deploy/projects/drawio-generator.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> drawio-generator [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="drawio-generator"
PROJECT_SRC_REL="scripts/drawio-generator"
PROJECT_TGT="/root/.hermes/scripts/drawio-generator"
PROJECT_SVC=""

SKILLS_SRC="scripts/drawio-generator/skills"
SKILLS_TGT="/root/.hermes/skills"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
