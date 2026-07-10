#!/bin/bash
# deploy/projects/dream-synth.sh — 梦境流水线部署脚本
set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="dream-synth"
PROJECT_SRC_REL="scripts/dream-synth"
PROJECT_TGT="/root/.hermes/scripts/dream-synth"
PROJECT_SVC=""

SKILLS_SRC=""
SKILLS_TGT=""

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"