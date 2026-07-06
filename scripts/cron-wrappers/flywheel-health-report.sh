#!/bin/bash
# flywheel-health-report.sh — 飞轮健康报告统一生成
#
# 部署路径: /root/.hermes/scripts/flywheel-health-report.sh
# 调度建议: 0 17 * * *（每日 17:00，所有 cron 跑完后）
#
# 功能:
#   - 调用 flywheel-health-report.py 生成报告
#   - 发送飞书通知：只报 P0/P1/失败任务，无多余信息

set -euo pipefail

_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh" >&2
    exit 2
fi

cron_init "flywheel-health-report"
CRON_SKIP_FINISH_NOTIFY=true

CHAT_ID="${FEISHU_CHAT_ID:-oc_f04a9f65d4b780511cc3f402c7d54ac3}"
REPORT_DIR="/root/.hermes/logs/reports"
TODAY_CN=$(TZ='Asia/Shanghai' date +%Y-%m-%d)
REPORT_FILE="${REPORT_DIR}/flywheel-report-${TODAY_CN}.md"

cron_section "飞轮健康报告生成"
_RC=0
if python3 /root/.hermes/scripts/flywheel-health-report.py; then
    cron_ok "报告已生成，无 P0 问题"
    _STEP_RESULTS+=("✅ 报告生成（无 P0）")
else
    _RC=$?
    if [[ $_RC -eq 1 ]]; then
        cron_warn "报告已生成，检测到 P0 问题"
        _STEP_RESULTS+=("⚠️ 报告生成（有 P0）")
    else
        cron_err "报告生成失败 (exit=$_RC)"
        _STEP_RESULTS+=("❌ 报告生成失败 (exit=$_RC)")
    fi
fi

if command -v lark-cli &>/dev/null; then
    cron_section "发送飞书通知"

    # 只提取 P0/P1 数据行和失败任务
    P0=$(grep -A 20 '^## 🔴 P0' "$REPORT_FILE" | grep -E '^\|' | tail -n +3 | head -3 || echo "")
    P1=$(grep -A 20 '^## 🟡 P1' "$REPORT_FILE" | grep -E '^\|' | tail -n +3 | head -5 || echo "")
    FAILED_LINE=$(awk -F'|' '$3 ~ /❌/ {print}' "$REPORT_FILE" | head -1 || echo "")

    BODY=""
    if [[ -n "$P0" ]]; then
        while IFS='|' read -r _ fw problem detail _; do
            fw=$(echo "$fw" | xargs)
            problem=$(echo "$problem" | xargs | cut -c1-50)
            [[ -z "$fw" ]] && continue
            BODY=$(printf '%b' "${BODY}\n· ${fw}：${problem}")
        done <<< "$P0"
    fi

    if [[ -n "$P1" ]]; then
        BODY=$(printf '%b' "${BODY}\n")
        while IFS='|' read -r _ fw problem detail _; do
            fw=$(echo "$fw" | xargs)
            problem=$(echo "$problem" | xargs | cut -c1-50)
            [[ -z "$fw" ]] && continue
            BODY=$(printf '%b' "${BODY}\n· ${fw}：${problem}")
        done <<< "$P1"
    fi

    if [[ -n "$FAILED_LINE" ]]; then
        FAILED_NAME=$(echo "$FAILED_LINE" | awk -F'|' '{print $2}' | xargs)
        BODY=$(printf '%b' "${BODY}\n\n· ${FAILED_NAME}")
    fi

    cron_notify "飞轮健康报告 ${TODAY_CN}" "$BODY"

    if [[ -f "$REPORT_FILE" ]]; then
        cd "$REPORT_DIR" && lark-cli im +messages-send \
            --chat-id "$CHAT_ID" \
            --file "flywheel-report-${TODAY_CN}.md" \
            --as bot &>/dev/null || true
    fi
fi

cron_finish || true