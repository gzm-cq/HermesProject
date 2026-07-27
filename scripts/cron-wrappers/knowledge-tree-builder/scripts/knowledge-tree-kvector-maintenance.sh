#!/bin/bash
# knowledge-tree-kvector-maintenance.sh — k_vector 兜底回填定时任务 wrapper
#
# 部署路径: /root/.hermes/scripts/knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   0 9 * * 6 (每周六 09:00)
#
# 行为:
#   - 统计 knowledge_point + subject 的 k_vector 缺失数量
#   - 总缺失低于阈值则静默退出（cron no_agent 语义）
#   - 达到阈值则执行分批回填（含 subject 节点的 name embedding）

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
cron_init "knowledge-tree-kvector"

# ===== 环境准备 =====
cd /root/.hermes/scripts/knowledge-tree-builder
source venv/bin/activate

# .env 加载已由 cron_common.sh 在 source 时统一处理，无需重复加载

: "${KT_DB_URL:?KT_DB_URL is required. Set it in /root/.hermes/.env}"
: "${HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY:?HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY is required. Set it in /root/.hermes/.env}"

THRESHOLD="${K_VECTOR_BACKFILL_THRESHOLD:-100}"
BATCH_SIZE="${K_VECTOR_BACKFILL_BATCH_SIZE:-20}"
DRY_RUN="${K_VECTOR_BACKFILL_DRY_RUN:-0}"

# ===== 统计缺失数 =====
cron_section "k_vector 缺失统计"
counts_json=$(python3 - <<'PY'
import json
import os
import psycopg2

db_url = os.environ["KT_DB_URL"]
with psycopg2.connect(db_url) as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE node_type = 'knowledge_point') AS knowledge_points,
              COUNT(*) FILTER (WHERE node_type = 'knowledge_point' AND k_vector IS NULL) AS missing_knowledge_points,
              COUNT(*) FILTER (WHERE node_type = 'subject') AS subjects,
              COUNT(*) FILTER (WHERE node_type = 'subject' AND k_vector IS NULL) AS missing_subjects
            FROM knowledge_tree
            WHERE node_type IN ('knowledge_point', 'subject')
            """
        )
        row = cur.fetchone()
print(json.dumps({
    "knowledge_points": int(row[0] or 0),
    "missing_knowledge_points": int(row[1] or 0),
    "subjects": int(row[2] or 0),
    "missing_subjects": int(row[3] or 0),
}, ensure_ascii=False))
PY
)

missing=$(python3 - <<'PY' "$counts_json"
import json, sys
data = json.loads(sys.argv[1])
kp_missing = data["missing_knowledge_points"]
subj_missing = data["missing_subjects"]
# 总缺失 = knowledge_point + subject，任一类达到阈值即触发回填
total = kp_missing + subj_missing
print(f"{total}\t{kp_missing}\t{subj_missing}")
PY
)
IFS=$'\t' read -r missing_total missing_kp missing_subj <<< "$missing"
cron_ok "统计完成: 总缺失=$missing_total (KP=$missing_kp Subject=$missing_subj) threshold=$THRESHOLD"

# ===== dry-run 模式 =====
if [[ "$DRY_RUN" == "1" || "$DRY_RUN" == "true" ]]; then
    cron_section "k_vector dry-run 回填"
    echo "[kvector] dry-run counts: $counts_json threshold=$THRESHOLD batch_size=$BATCH_SIZE"
    if python3 -m knowledge_tree_builder.cli backfill-k-vectors --dry-run --batch-size "$BATCH_SIZE"; then
        cron_ok "dry-run 回填完成"
        _STEP_RESULTS+=("✅ k_vector dry-run 回填")
    else
        rc=$?
        cron_err "dry-run 回填失败 (exit=$rc)"
        _STEP_RESULTS+=("❌ k_vector dry-run 回填 (exit=$rc)")
    fi
    cron_finish
    exit $?
fi

# ===== 阈值判断 =====
if (( missing_total < THRESHOLD )); then
    cron_ok "总缺失 $missing_total < 阈值 $THRESHOLD，无需回填"
    _STEP_RESULTS+=("✅ k_vector 无需回填 (total=$missing_total < $THRESHOLD)")
    cron_finish
    exit $?
fi

# ===== 执行回填 =====
cron_section "k_vector 回填 (total=$missing_total >= $THRESHOLD, KP=$missing_kp Subject=$missing_subj)"
echo "[kvector] counts: $counts_json"
if python3 -m knowledge_tree_builder.cli backfill-k-vectors --batch-size "$BATCH_SIZE"; then
    cron_ok "k_vector 回填完成"
    _STEP_RESULTS+=("✅ k_vector 回填 (total=$missing_total)")
else
    rc=$?
    cron_err "k_vector 回填失败 (exit=$rc)"
    _STEP_RESULTS+=("❌ k_vector 回填 (exit=$rc)")
fi

# ===== 完成 =====
cron_finish
