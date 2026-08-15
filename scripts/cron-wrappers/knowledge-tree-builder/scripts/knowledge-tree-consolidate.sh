#!/bin/bash
# knowledge-tree-consolidate.sh — 知识树合并定时任务 wrapper
#
# 部署路径: /root/.hermes/scripts/knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   0 11 * * 1 (每周一 11:00)

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

# .env 加载已由 cron_common.sh 在 source 时统一处理，无需重复加载

: "${KT_DB_URL:?KT_DB_URL is required. Set it in /root/.hermes/.env}"
: "${LITELLM_MASTER_KEY:?LITELLM_MASTER_KEY is required. Set it in /root/.hermes/.env}"

# LLM 模型继承链兜底：KT_LLM_MODEL → LLM_MODEL_MAIN
export KT_LLM_MODEL="${KT_LLM_MODEL:-${LLM_MODEL_MAIN:-}}"

# ===== 执行合并 =====
# --build-edges: 同步重建 KP 级关联边（阈值 0.65/0.65），防止孤立知识点率随新知识沉淀而回升
cron_section "知识树 consolidate run (--merge-domains --build-edges)"
if python3 -m knowledge_tree_builder.cli consolidate run --merge-domains --build-edges; then
    cron_ok "知识树合并完成"
    _STEP_RESULTS+=("✅ 知识树 consolidate run --merge-domains --build-edges")
else
    rc=$?
    cron_err "知识树合并失败 (exit=$rc)"
    _STEP_RESULTS+=("❌ 知识树 consolidate run (exit=$rc)")
fi

# ===== 处理超时审查项 =====
cron_section "处理超时审查项"
if python3 -m knowledge_tree_builder.cli consolidate process-timeouts; then
    cron_ok "超时审查项处理完成"
    _STEP_RESULTS+=("✅ process-timeouts")
else
    rc=$?
    cron_warn "超时审查项处理失败 (exit=$rc)"
    _STEP_RESULTS+=("⚠️ process-timeouts (exit=$rc)")
fi

# ===== 重新分类 general/root 下的知识点 =====
cron_section "redistribute 重新分类"
if python3 -m knowledge_tree_builder.cli redistribute; then
    cron_ok "redistribute 完成"
    _STEP_RESULTS+=("✅ redistribute")
else
    rc=$?
    cron_warn "redistribute 失败 (exit=$rc)"
    _STEP_RESULTS+=("⚠️ redistribute (exit=$rc)")
fi

# ===== 基线对比 + 退化检测（Phase 6）=====
HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
BASELINE_LATEST="${HERMES_HOME}/data/flywheel/kt-baseline-latest.json"
BASELINE_PREV="${HERMES_HOME}/data/flywheel/kt-baseline-prev.json"
FLYWHEEL_DIR="$(dirname "$BASELINE_LATEST")"
mkdir -p "$FLYWHEEL_DIR"

if [[ -f "$BASELINE_LATEST" ]]; then
    cron_section "基线对比 + 退化检测"

    # 读取当前基线
    CURRENT=$(python3 -c "
import json, sys
try:
    d = json.load(open('$BASELINE_LATEST'))
    m = d['metrics']
    print(f\"{m['avg_confidence']}|{m['total_kps']}|{m['fragment_domains']}|{m['orphan_kps']}\")
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null || echo "")

    if [[ -n "$CURRENT" ]]; then
        IFS='|' read -r CUR_CONF CUR_KPS CUR_FRAG CUR_ORPH <<< "$CURRENT"

        if [[ ! -f "$BASELINE_PREV" ]]; then
            # 首次运行，保存基线
            cp "$BASELINE_LATEST" "$BASELINE_PREV"
            cron_ok "首次基线已保存 (avg_conf=$CUR_CONF, kps=$CUR_KPS)"
            _STEP_RESULTS+=("✅ 基线: 首次保存 (avg_conf=$CUR_CONF)")
        else
            # 读取前次基线
            PREV=$(python3 -c "
import json, sys
try:
    d = json.load(open('$BASELINE_PREV'))
    m = d['metrics']
    print(f\"{m['avg_confidence']}|{m['total_kps']}|{m['fragment_domains']}|{m['orphan_kps']}\")
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null || echo "")

            if [[ -n "$PREV" ]]; then
                IFS='|' read -r PREV_CONF PREV_KPS PREV_FRAG PREV_ORPH <<< "$PREV"

                # Python 内联计算 delta
                DELTA_REPORT=$(python3 -c "
import sys
cur_conf = float('$CUR_CONF')
prev_conf = float('$PREV_CONF')
cur_kps = float('$CUR_KPS')
prev_kps = float('$PREV_KPS')
cur_orph = float('$CUR_ORPH')
prev_orph = float('$PREV_ORPH')

conf_diff = cur_conf - prev_conf
kps_pct = ((cur_kps - prev_kps) / max(prev_kps, 1)) * 100
orph_pct = ((cur_orph - prev_orph) / max(prev_orph, 1)) * 100

alerts = []
if conf_diff < -0.05:
    alerts.append(f'confidence 下降 {conf_diff:.4f}')
if kps_pct < -5:
    alerts.append(f'知识点减少 {kps_pct:.2f}%')
if orph_pct > 10:
    alerts.append(f'孤儿节点增加 {orph_pct:.2f}%')

# 输出用于 shell 解析的结构化结果
result = {
    'conf_diff': round(conf_diff, 4),
    'kps_pct': round(kps_pct, 2),
    'orph_pct': round(orph_pct, 2),
    'alerts': alerts,
    'stable': len(alerts) == 0,
}
import json
print(json.dumps(result))
" 2>/dev/null || echo "")

                if [[ -n "$DELTA_REPORT" ]]; then
                    HAS_ALERT=$(echo "$DELTA_REPORT" | python3 -c "import json,sys; d=json.load(sys.stdin); print('true' if not d['stable'] else 'false')" 2>/dev/null || echo "false")

                    if [[ "$HAS_ALERT" == "true" ]]; then
                        ALERT_MSG=$(echo "$DELTA_REPORT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('; '.join(d['alerts']))
" 2>/dev/null)
                        cron_warn "知识树退化: ${ALERT_MSG}"
                        _STEP_RESULTS+=("🔥 退化告警: ${ALERT_MSG}")

                        # 连续退化计数
                        DECLINE_FILE="${FLYWHEEL_DIR}/kt-decline-count"
                        CUR_DECLINE=$(cat "$DECLINE_FILE" 2>/dev/null || echo "0")
                        CUR_DECLINE=$((CUR_DECLINE + 1))
                        echo "$CUR_DECLINE" > "$DECLINE_FILE"

                        if [[ "$CUR_DECLINE" -ge 3 ]]; then
                            cron_err "🔥 知识树参数可能需要调整（连续 ${CUR_DECLINE} 周下降）"
                            _STEP_RESULTS+=("🔥 连续 ${CUR_DECLINE} 周下降，需关注")
                        fi
                    else
                        CONF_DIFF=$(echo "$DELTA_REPORT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['conf_diff'])" 2>/dev/null)
                        cron_ok "知识树质量稳定 (avg_conf=$CUR_CONF, Δconf=$CONF_DIFF)"
                        _STEP_RESULTS+=("✅ 质量稳定 (avg_conf=$CUR_CONF)")
                        echo "0" > "${FLYWHEEL_DIR}/kt-decline-count" 2>/dev/null
                    fi
                fi
            fi
            # 更新前次基线
            cp "$BASELINE_LATEST" "$BASELINE_PREV"
        fi
    fi
fi

# ===== 完成 =====
cron_finish