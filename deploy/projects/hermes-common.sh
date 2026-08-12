#!/bin/bash
# deploy/projects/hermes-common.sh — 统一共享库部署（hermes_common）
# 可直接执行: ./deploy/projects/hermes-common.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> hermes-common [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="hermes-common"
PROJECT_SRC_REL="libs/hermes_common"
PROJECT_TGT="/root/.hermes/lib"
PROJECT_SVC=""

SKILLS_SRC=""
SKILLS_TGT=""

LEGACY_FILES=()

# Target is /root/.hermes/lib — 独立的共享库目录。关闭首次部署全量清理，
# 避免把同目录下的兄弟库当成残留误删（未来若有其它 lib 也放这里）。
FIRST_DEPLOY_CLEANUP="false"

# 引用一致性断言：部署后扫描消费方源码，确认无旧库引用残留
# （scripts/common、hermes-plugin-common 已废弃，若仍被引用则部署失败）。
REFERENCE_CHECK_DIRS=("/root/.hermes/scripts" "/root/.hermes/plugins")
REFERENCE_CHECK_PATTERNS=(
  "scripts\.common"
  "hermes_plugin_common"
  "hermes-plugin-common"
  "(from|import) common\.(ledger|llm_guard)"
)

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
