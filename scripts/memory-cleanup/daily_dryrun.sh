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
if bash run.sh --vote 1 --apply --quiet; then
    cron_ok "记忆清理完成"
    _STEP_RESULTS+=("✅ 记忆清理完成")

    # 发送飞书通知
    if command -v lark-cli &>/dev/null; then
        # 找最新的清理报告
        LATEST_REPORT=$(ls -t "$MEMORY_DIR"/cleanup-report-*.json 2>/dev/null | head -1 || echo "")
        if [[ -n "$LATEST_REPORT" ]]; then
            # 提取关键指标（直接从报告 JSON 读取 phase1_* 计数，与 --quiet 输出一致）
            NOTIFY_JSON=$(python3 -c "
import json
d=json.load(open('$LATEST_REPORT'))
sources=d.get('sources',{})
mem=sources.get('MEMORY.md',{})
user=sources.get('USER.md',{})
mem_after=mem.get('after_cleanup',{})
user_after=user.get('after_cleanup',{})
mem_chars=mem_after.get('keep_chars',0)
user_chars=user_after.get('keep_chars',0)
merge=mem.get('phase1_merge',0)+user.get('phase1_merge',0)
compress=mem.get('phase1_compress',0)+user.get('phase1_compress',0)
hindsight=mem.get('phase1_hindsight',0)+user.get('phase1_hindsight',0)
remove=mem.get('phase1_remove',0)+user.get('phase1_remove',0)
print(json.dumps({'mc':mem_chars,'uc':user_chars,'merge':merge,'compress':compress,'hindsight':hindsight,'remove':remove}))
" 2>/dev/null || echo '{"mc":0,"uc":0,"merge":0,"compress":0,"hindsight":0,"remove":0}')

            MC=$(echo "$NOTIFY_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin).get('mc',0))")
            UC=$(echo "$NOTIFY_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin).get('uc',0))")
            MERGE=$(echo "$NOTIFY_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin).get('merge',0))")
            COMPRESS=$(echo "$NOTIFY_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin).get('compress',0))")
            HINDSIGHT=$(echo "$NOTIFY_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin).get('hindsight',0))")
            REMOVE=$(echo "$NOTIFY_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin).get('remove',0))")

            BODY="🧹 记忆清理完成\nMEMORY.md: ${MC} chars | USER.md: ${UC} chars\n合并: ${MERGE} 组 | 压缩: ${COMPRESS} 条 | 迁出: ${HINDSIGHT} 条 | 删除: ${REMOVE} 条"
            cron_notify "记忆清理完成" "$BODY" || true
        fi
    fi
else
    RC=$?
    cron_err "记忆清理失败 (exit=$RC)"
    _STEP_RESULTS+=("❌ 记忆清理失败 (exit=$RC)")

    # 发送失败通知
    if command -v lark-cli &>/dev/null; then
        cron_notify "⚠️ 记忆清理失败" "memory-cleanup 执行失败 (exit=$RC)" || true
    fi
fi

cron_finish

