#!/bin/bash
# Daily memory cleanup wrapper - runs memory-cleanup with --apply flag
# 发送飞书通知（成功/失败）

set -euo pipefail

_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh" >&2
    exit 2
fi

cron_init "memory-cleanup"
CRON_SKIP_FINISH_NOTIFY=true

HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
CHAT_ID="${FEISHU_CHAT_ID:-oc_ff1105d776b5bd4842d0109d186cd95f}"
MEMORY_DIR="${HERMES_HOME}/memories"

# LLM 模型继承链兜底：MEMORY_CLEANUP_LLM_MODEL → LLM_MODEL_LIGHT
export MEMORY_CLEANUP_LLM_MODEL="${MEMORY_CLEANUP_LLM_MODEL:-${LLM_MODEL_LIGHT:-}}"

cron_section "执行记忆清理"
if bash run.sh --vote 1 --apply; then
    cron_ok "记忆清理完成"
    _STEP_RESULTS+=("✅ 记忆清理完成")

    # 发送飞书通知
    if command -v lark-cli &>/dev/null; then
        # 找最新的清理报告
        LATEST_REPORT=$(ls -t "$MEMORY_DIR"/cleanup-report-*.json 2>/dev/null | head -1 || echo "")
        if [[ -n "$LATEST_REPORT" ]]; then
            # 提取关键指标
            MEMORY_CHARS=$(python3 -c "import json; d=json.load(open('$LATEST_REPORT')); print(d.get('sources',{}).get('MEMORY.md',{}).get('after_cleanup',{}).get('keep_chars',0))" 2>/dev/null || echo "0")
            USER_CHARS=$(python3 -c "import json; d=json.load(open('$LATEST_REPORT')); print(d.get('sources',{}).get('USER.md',{}).get('after_cleanup',{}).get('keep_chars',0))" 2>/dev/null || echo "0")
            REMOVE=$(python3 -c "import json; d=json.load(open('$LATEST_REPORT')); print(d.get('total_remove',0))" 2>/dev/null || echo "0")
            COMPRESS=$(python3 -c "import json; d=json.load(open('$LATEST_REPORT')); print(d.get('total_compress',0))" 2>/dev/null || echo "0")

            BODY="🧹 记忆清理完成\nMEMORY.md: ${MEMORY_CHARS} chars | USER.md: ${USER_CHARS} chars\n删除: ${REMOVE} 条 | 压缩: ${COMPRESS} 条"
            cron_notify "记忆清理完成" "$BODY" || true
        fi
    fi
else
    RC=$?
    cron_err "记忆清理失败 (exit=$RC)"
    _STEP_RESULTS+=("❌ 记忆清理失败 (exit=$RC)")

    # 发送失败通知
    if command -v lark-cli &>/dev/null; then
        cron_notify "⚠️ 记忆清理失败" "memory-cleanup-daily 执行失败 (exit=$RC)" || true
    fi
fi

cron_finish

