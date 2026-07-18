#!/bin/bash
# run-skill-eval.sh — Skill Matcher 每日评估基线采集
#
# 部署路径: /mnt/d/HermesProject/scripts/cron-wrappers/run-skill-eval.sh → /root/.hermes/scripts/run-skill-eval.sh
# 调度: 每天 12:00（与 knowledge-navigation-baseline.sh 同批次）
#
# 功能:
#   1. 调用 run_skill_eval.py --json 采集评估指标
#   2. 与上周基线对比，退化 >10% 飞书告警
#   3. 无异常静默退出

set -euo pipefail

# ===== 配置区 =====
HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
REGRESSION_THRESHOLD_PCT=-10

# ===== 加载公共库 =====
_CRON_LIB="${CRON_LIB:-${HERMES_HOME}/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh" >&2
    exit 2
fi

cron_init "run-skill-eval"

PLUGIN_DIR="${HERMES_HOME}/plugins/knowledge-navigation"
EVAL_SCRIPT="$PLUGIN_DIR/scripts/run_skill_eval.py"
PREV_BASELINE="${HERMES_HOME}/data/flywheel/skill_eval_prev.json"
FLYWHEEL_DIR="$(dirname "$PREV_BASELINE")"
mkdir -p "$FLYWHEEL_DIR"

# 安全临时文件（脚本退出时自动清理）
EVAL_RESULT_FILE=$(mktemp /tmp/skill_eval_result-XXXXXX.json)
trap 'rm -f "$EVAL_RESULT_FILE"' EXIT

# ===== 1. 跑评估 =====
cron_section "Skill Matcher 评估"
if ! python3 "$EVAL_SCRIPT" --json > "$EVAL_RESULT_FILE" 2>/dev/null; then
    cron_warn "评估脚本执行失败"
    _STEP_RESULTS+=("⚠️ Skill Eval: 脚本执行失败")
    CRON_SKIP_FINISH_NOTIFY=false
    cron_finish
    exit 0
fi

CUR_RESULT=$(cat "$EVAL_RESULT_FILE")
CUR_F1=$(echo "$CUR_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['meta']['avg_f1'])" 2>/dev/null || echo "N/A")

if [[ "$CUR_F1" == "N/A" ]]; then
    cron_warn "无法解析评估结果"
    _STEP_RESULTS+=("⚠️ Skill Eval: 结果解析失败")
else
    cron_ok "当前 F1@3: $CUR_F1"
    _STEP_RESULTS+=("✅ Skill Eval: 当前 F1=$CUR_F1")
fi

# ===== 2. 与上周基线对比 =====
if [[ -f "$PREV_BASELINE" ]] && [[ "$CUR_F1" != "N/A" ]]; then
    PREV_F1=$(python3 -c "
import json
try:
    d=json.load(open('$PREV_BASELINE'))
    print(d['meta']['avg_f1'])
except (json.JSONDecodeError, KeyError, ValueError): print('N/A')
" 2>/dev/null)

    if [[ "$PREV_F1" != "N/A" ]]; then
        DELTA=$(python3 -c "
cur, prev = float('$CUR_F1'), float('$PREV_F1')
print(f'{(cur-prev)/prev*100:.1f}' if prev > 0 else '0.0')
" 2>/dev/null)

        # 用 Python 进行浮点比较（避免依赖 bc 命令）
        IS_REGRESSED=$(python3 -c "
try:
    d = float('$DELTA')
    print('1' if d < ${REGRESSION_THRESHOLD_PCT} else '0')
except: print('0')
" 2>/dev/null)
        if [[ "$IS_REGRESSED" == "1" ]]; then
            cron_warn "Skill Matcher 退化 ${DELTA}% (prev=$PREV_F1 → cur=$CUR_F1)"
            _STEP_RESULTS+=("⚠️ Skill Eval: 退化 ${DELTA}%")
        else
            cron_ok "Skill Matcher 稳定 (Δ${DELTA}%)"
            _STEP_RESULTS+=("✅ Skill Eval: 稳定 (Δ${DELTA}%)")
        fi
    fi
fi

# 保存当前为之后对比用
cp "$EVAL_RESULT_FILE" "$PREV_BASELINE"

cron_finish
