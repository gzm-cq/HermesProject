#!/bin/bash
# deploy/projects/clustering-analysis-v3.sh — 聚类分析系统部署脚本
# 可直接执行: ./deploy/projects/clustering-analysis-v3.sh <plan|deploy|rollback|history|cleanup> [选项]
# 也可通过分发器: ./deploy/deploy.sh <cmd> clustering-analysis-v3 [选项]

set -euo pipefail
shopt -s globstar nullglob extglob

PROJECT_NAME="clustering-analysis-v3"
PROJECT_SRC_REL="scripts/clustering-analysis-v3"
PROJECT_TGT="/root/.hermes/scripts/clustering-analysis-v3"
PROJECT_SVC=""

LEGACY_FILES=(
  "/root/.hermes/scripts/clustering-analysis-v3.py"
  "/root/.hermes/scripts/clustering-analysis-v3.py.bak"
  "/root/.hermes/scripts/clustering-analysis-v3.py.bak.*"
  "/root/.hermes/scripts/clustering-analysis.py"
  "/root/.hermes/scripts/clustering-results-v3.json"
  "/root/.hermes/scripts/clustering-results.json"
  "/root/.hermes/scripts/param-sweep-clustering.py"
  "/root/.hermes/scripts/causal-poc.py"
  "/root/.hermes/scripts/causal-poc-results.json"
  "/root/.hermes/scripts/causal-v3-results.json"
  "/root/.hermes/scripts/build-causal-links.py"
  "/root/.hermes/scripts/build-causal-links-v2.py"
  "/root/.hermes/scripts/re_extract_entities.py"
  "/root/.hermes/scripts/entities_v3_*.json"
  "/root/.hermes/scripts/test_clustering_analysis_v3.py"
  "/root/.hermes/scripts/memory_classification_sample.json"
  "/root/.hermes/scripts/memory_cleanup_backup.json"
)

SKILLS_SRC="scripts/clustering-analysis-v3/skills"
SKILLS_TGT="/root/.hermes/skills"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
