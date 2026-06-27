#!/bin/bash
# knowledge-tree-consolidate.sh — 知识树合并定时任务 wrapper
#
# 部署路径: /root/.hermes/scripts/knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   30 10 * * 1 (每周一 10:30)

set -euo pipefail

# ===== 加载公共库 =====
_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    # shellcheck disable=SC1090
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh (${_CRON_LIB})" >&2
    echo "请先部署 cron-common 项目: deploy.sh deploy cron-common" >&2
    exit 2
fi

# ===== 初始化 =====
cron_init "knowledge-tree-consolidate"

# ===== 环境准备 =====
cd /root/.hermes/scripts/knowledge-tree-builder
source venv/bin/activate

if [[ -f /root/.hermes/.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source /root/.hermes/.env
  set +a
fi

: "${KT_DB_URL:?KT_DB_URL is required. Set it in /root/.hermes/.env}"
: "${LITELLM_MASTER_KEY:?LITELLM_MASTER_KEY is required. Set it in /root/.hermes/.env}"

# ===== 执行合并 =====
cron_section "知识树 consolidate run"
if python3 -m knowledge_tree_builder.cli consolidate run; then
    cron_ok "知识树合并完成"
    _STEP_RESULTS+=("✅ 知识树 consolidate run")
else
    rc=$?
    cron_err "知识树合并失败 (exit=$rc)"
    _STEP_RESULTS+=("❌ 知识树 consolidate run (exit=$rc)")
fi

# ===== 完成 =====
cron_finish
