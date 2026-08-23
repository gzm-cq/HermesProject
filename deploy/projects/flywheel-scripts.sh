#!/bin/bash
# deploy/projects/flywheel-scripts.sh — 数据飞轮增强辅助脚本统一部署
# 可直接执行: ./deploy/projects/flywheel-scripts.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> flywheel-scripts [选项]
#
# 聚合部署 5 个飞轮增强新增脚本到 /root/.hermes/scripts/（保留子目录结构）：
#   skill-router/   P0-1 SkillRouter 本地 embedding 后端（KN_SKILL_EMBEDDING_BACKEND=skillrouter 激活时调用）
#   auto-harness/   P1-1 WeaknessMiner 失败模式聚类
#   graphiti-bridge/ P1-2 轻量时间版本管理桥接
#   memory-weeder/  P0-3 Vestige 配套的主动记忆清理
#   p2-eval/        P2 理念验证脚本
#
# 目标目录 /root/.hermes/scripts 与 cron-wrappers/system-health-check 共享，
# common.sh 的 SHARED_TARGET_DIRS 会自动关闭首次全量防残留扫描，避免误删兄弟项目。

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="flywheel-scripts"
PROJECT_SRC_REL="scripts"
PROJECT_TGT="/root/.hermes/scripts"
PROJECT_SVC=""

# 技能部署：memory-weeder 运维 skill（scripts/memory-weeder/skills/ → ~/.hermes/skills/）
SKILLS_SRC="scripts/memory-weeder/skills"
SKILLS_TGT="/root/.hermes/skills"

LEGACY_FILES=()

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
