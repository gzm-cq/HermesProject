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

# ===== 环境变量（必须在 Runner 阶段 0 之前设置）=====
export HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
CHAT_ID="${FEISHU_CHAT_ID:-}"
REPORT_DIR="${HERMES_HOME}/logs/reports"

# 设置 PYTHONPATH 指向包的 src 目录（Runner 和 CLI 都依赖此变量）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="$PROJECT_DIR/src:${PYTHONPATH:-}"

# ===== 阶段 0：Runner 前置登记 =====
# 声明式标记 report 阶段 1 将内部执行 KN LLM Judge，替代原 knowledge-navigation-baseline cron job
# （防止 judge 被跑 2 次浪费 LLM token）。runner.run_all 本身不耗时（0.01s 级）。
cron_section "Runner：阶段 0 登记"
if python3 -m flywheel_health_report.runner --home "$HERMES_HOME"; then
    cron_ok "Runner 登记 OK"
    _STEP_RESULTS+=("✅ Runner 阶段 0")
else
    _RUNNER_RC=$?
    cron_warn "Runner 登记异常 (exit=$_RUNNER_RC，不影响后续报告)"
    _STEP_RESULTS+=("⚠️ Runner 阶段 0 异常")
fi

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

# ===== Auto-Tuner: 参数自优化（必须在飞书通知前执行，以便结果包含在通知中）=====
_AUTO_TUNER="${SCRIPT_DIR}/auto-tuner.sh"
_AUTO_TUNER_LOG="${HERMES_HOME}/data/flywheel/auto-tuner-log.jsonl"
if [[ -f "$_AUTO_TUNER" ]]; then
    cron_section "Auto-Tuner 参数自优化"
    _TUNER_OUTPUT=""
    if _TUNER_OUTPUT=$(bash "$_AUTO_TUNER" 2>&1); then
        cron_ok "Auto-Tuner 完成"
        _STEP_RESULTS+=("✅ Auto-Tuner")
        # 从日志提取最近一次调优详情
        _TUNER_DETAIL=$(python3 - "$_AUTO_TUNER_LOG" <<'PY' 2>/dev/null || echo ""
import json, sys
from pathlib import Path

log_file = Path(sys.argv[1])
if not log_file.is_file():
    print(""); sys.exit(0)

lines = log_file.read_text(encoding="utf-8").strip().splitlines()
today = __import__("datetime").date.today().isoformat()

# 找最近一条非 dry_run 的记录
latest = None
for line in reversed(lines):
    try:
        rec = json.loads(line)
        if rec.get("dry_run"):
            continue
        latest = rec
        break
    except json.JSONDecodeError:
        continue

if not latest:
    print(""); sys.exit(0)

name = latest.get("parameter", "?")
old = latest.get("old_value", "?")
new = latest.get("new_value", "?")
reason = latest.get("reason", "")
status = latest.get("status", "")
needs_restart = status == "pending_restart"

restart_note = "⚠️ 需要重启网关生效" if needs_restart else "✅ 已自动生效"

print(f"{name}|{old}|{new}|{reason}|{restart_note}")
PY
)
    else
        cron_warn "Auto-Tuner 执行异常（不影响飞轮报告）"
        _STEP_RESULTS+=("⚠️ Auto-Tuner 异常")
        _TUNER_DETAIL=""
    fi
else
    cron_log "Auto-Tuner 脚本不存在，跳过: ${_AUTO_TUNER}"
    _TUNER_DETAIL=""
fi

if command -v lark-cli &>/dev/null; then
    cron_section "发送飞书通知"

    P0=$(awk '/^## 🔴 P0/{f=1;next} /^## /{f=0} f && /^\|/' "$REPORT_FILE" | tail -n +2 | head -${MAX_P0_LINES} || echo "")
    P1=$(awk '/^## 🟡 P1/{f=1;next} /^## /{f=0} f && /^\|/' "$REPORT_FILE" | tail -n +2 | head -${MAX_P1_LINES} || echo "")
    FAILED_LINE=$(awk -F'|' '$3 ~ /❌/ {print}' "$REPORT_FILE" | head -1 || echo "")

    # Markdown 格式消息
    MD_BODY="## 📊 飞轮健康报告 ${TODAY_CN}"

    # P0
    MD_BODY="${MD_BODY}

**🔴 P0 问题**"
    if [[ -n "$P0" ]]; then
        while IFS='|' read -r _ fw problem detail _; do
            fw=$(echo "$fw" | xargs)
            problem=$(echo "$problem" | xargs | cut -c1-50)
            [[ -z "$fw" ]] && continue
            MD_BODY=$(printf '%b' "${MD_BODY}\n- **${fw}**：${problem}")
        done <<< "$P0"
    else
        MD_BODY="${MD_BODY}\n- ✅ 无 P0 问题"
    fi

    # P1
    MD_BODY="${MD_BODY}

**🟡 P1 问题**"
    if [[ -n "$P1" ]]; then
        while IFS='|' read -r _ fw problem detail _; do
            fw=$(echo "$fw" | xargs)
            problem=$(echo "$problem" | xargs | cut -c1-50)
            [[ -z "$fw" ]] && continue
            MD_BODY=$(printf '%b' "${MD_BODY}\n- **${fw}**：${problem}")
        done <<< "$P1"
    else
        MD_BODY="${MD_BODY}\n- ✅ 无 P1 问题"
    fi

    # Auto-Tuner
    if [[ -n "$_TUNER_DETAIL" ]]; then
        IFS='|' read -r T_NAME T_OLD T_NEW T_REASON T_RESTART <<< "$_TUNER_DETAIL"
        MD_BODY="${MD_BODY}

## 🔧 Auto-Tuner 优化"
        MD_BODY="${MD_BODY}\n- **参数**: ${T_NAME}"
        MD_BODY="${MD_BODY}\n- **变更**: ${T_OLD} → ${T_NEW}"
        if [[ -n "$T_REASON" ]]; then
            MD_BODY="${MD_BODY}\n- **原因**: ${T_REASON}"
        fi
        MD_BODY="${MD_BODY}\n- ${T_RESTART}"
    fi

    # 优化建议（从报告提取，限前 5 条）
    SUGGESTIONS=$(python3 - "$REPORT_FILE" <<'PY' 2>/dev/null || echo ""
import re, sys
from pathlib import Path
path = Path(sys.argv[1])
if not path.is_file():
    sys.exit(0)
text = path.read_text(encoding="utf-8")
m = re.search(r'^## 💡 优化方向\s*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
if not m:
    sys.exit(0)
section = m.group(1)
count = 0
for line in section.splitlines():
    line = line.strip()
    if not line.startswith("- **"):
        continue
    cleaned = re.sub(r'^- \*\*', '', line)
    cleaned = re.sub(r'\*\*:', '**：', cleaned)
    cleaned = re.sub(r'\*\*', '', cleaned)
    print(cleaned)
    count += 1
    if count >= 5:
        break
PY
)
    if [[ -n "$SUGGESTIONS" ]]; then
        MD_BODY="${MD_BODY}

## 💡 优化建议"
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            MD_BODY=$(printf '%b' "${MD_BODY}\n- ${line}")
        done <<< "$SUGGESTIONS"
    fi

    MD_BODY="${MD_BODY}

---
📎 详细报告见附件"

    # 发送 Markdown 消息
    cd "$HERMES_HOME" && source .env 2>/dev/null || true
    CHAT_ID="${CHAT_ID:-${FEISHU_CHAT_ID:-}}"

    if [[ -z "$CHAT_ID" ]]; then
        cron_warn "未配置 FEISHU_CHAT_ID，跳过飞书通知"
    else
        lark-cli im +messages-send \
            --chat-id "$CHAT_ID" \
            --markdown "$MD_BODY" \
            --as bot &>/dev/null || true

        # 发送报告附件
        if [[ -f "$REPORT_FILE" ]]; then
            cd "$REPORT_DIR" && lark-cli im +messages-send \
                --chat-id "$CHAT_ID" \
                --file "flywheel-report-${TODAY_CN}.md" \
                --as bot &>/dev/null || true
        fi
    fi
fi

cron_finish
