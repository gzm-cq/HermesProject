#!/bin/bash
# flywheel-health-report.sh — 飞轮健康报告统一生成
#
# 部署路径: /root/.hermes/scripts/flywheel-health-report/scripts/flywheel-health-report.sh
# 调度: CN 08:00（UTC 00:00），此时前一天 UTC 数据已完整
#
# 功能:
#   - 调用 flywheel_health_report.cli 生成报告
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
HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
REPORT_DIR="${HERMES_HOME}/logs/reports"

# 设置 PYTHONPATH 指向包的 src 目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="$PROJECT_DIR/src:${PYTHONPATH:-}"

# P0/P1 通知最大行数（避免飞书消息过长）
MAX_P0_LINES=5
MAX_P1_LINES=8
TODAY_CN=$(TZ='Asia/Shanghai' date +%Y-%m-%d)
REPORT_FILE="${REPORT_DIR}/flywheel-report-${TODAY_CN}.md"

cron_section "飞轮健康报告生成"
_RC=0
if python3 -m flywheel_health_report.cli --home "$HERMES_HOME"; then
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

    P0=$(awk '/^## 🔴 P0/{f=1;next} /^## /{f=0} f && /^\|/' "$REPORT_FILE" | tail -n +2 | head -${MAX_P0_LINES} || echo "")
    P1=$(awk '/^## 🟡 P1/{f=1;next} /^## /{f=0} f && /^\|/' "$REPORT_FILE" | tail -n +2 | head -${MAX_P1_LINES} || echo "")
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

    cron_notify "飞轮健康报告 ${TODAY_CN}" "$BODY" || true

    if [[ -f "$REPORT_FILE" ]]; then
        cd "$REPORT_DIR" && lark-cli im +messages-send \
            --chat-id "$CHAT_ID" \
            --file "flywheel-report-${TODAY_CN}.md" \
            --as bot &>/dev/null || true
    fi
fi

# ===== Auto-Tuner: 参数自优化 =====
_AUTO_TUNER="${SCRIPT_DIR}/auto-tuner.sh"
if [[ -f "$_AUTO_TUNER" ]]; then
    cron_section "Auto-Tuner 参数自优化"
    if bash "$_AUTO_TUNER"; then
        cron_ok "Auto-Tuner 完成"
        _STEP_RESULTS+=("✅ Auto-Tuner")
    else
        cron_warn "Auto-Tuner 执行异常（不影响飞轮报告）"
        _STEP_RESULTS+=("⚠️ Auto-Tuner 异常")
    fi
else
    cron_log "Auto-Tuner 脚本不存在，跳过: ${_AUTO_TUNER}"
fi

cron_finish
