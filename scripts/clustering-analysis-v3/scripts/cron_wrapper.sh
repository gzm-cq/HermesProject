#!/bin/bash
# cron_wrapper.sh — Hindsight 记忆维护 cron 包装脚本
#
# 管线顺序（对齐 hindsight-memory-maintenance-framework.md）：
#   ① 质量报告 (memory_quality_report.py --report-only)
#   ② 超长记忆治理（dry-run 默认；--apply 需 CONFIRM_APPLY）
#   ③ MinHash LSH 跨条目去重（dry-run 默认；--apply 需 CONFIRM_APPLY）
#   ④ 聚类分析（dry-run 默认；--apply 需 CONFIRM_APPLY）
#   ⑤ 飞书通知（含结构化指标总结）
#
# 超长记忆治理采用"归档原文 + 压缩替换 + 重算 embedding"，不直接删除唯一记忆。
#
# 设计要点：
#   - 纯 bash，no_agent=true 可用
#   - 每个步骤独立 if/else，失败不阻断后续
#   - 日志落盘 /tmp/hindsight-cron-<timestamp>.log
#   - 通知从日志解析关键指标，生成结构化总结
#
# 用法：
#   bash cron_wrapper.sh                          # dry-run 管线（默认，不写入）
#   CONFIRM_APPLY=I_UNDERSTAND_THIS_WRITES_HINDSIGHT bash cron_wrapper.sh --apply
#                                                  # 写入管线（②/③/④ 执行 apply）
#   bash cron_wrapper.sh --skip-steps "1,3"       # 跳过指定步骤（逗号分隔，1-based）
#
# 环境变量：
#   CLUSTERING_DB_URL  PostgreSQL 连接字符串（必填）
#   CONFIRM_APPLY      --apply 必须显式设置为 I_UNDERSTAND_THIS_WRITES_HINDSIGHT
#   FEISHU_WEBHOOK_URL  飞书 Webhook 地址（选填，用于通知）

set -euo pipefail

# ===== 配置 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
LOG_FILE="/tmp/hindsight-cron-${TIMESTAMP}.log"
SKIP_STEPS=""
MODE="dry-run"
CONFIRM_APPLY_VALUE="I_UNDERSTAND_THIS_WRITES_HINDSIGHT"

# ===== 颜色 & 日志 =====
C_CYA='\033[36m'; C_GRN='\033[32m'; C_RED='\033[31m'; C_YLW='\033[33m'; C_RST='\033[0m'
log()  { echo -e "${C_CYA}[cron]${C_RST} $*" | tee -a "$LOG_FILE"; }
ok()   { echo -e "${C_GRN}[ ok  ]${C_RST} $*" | tee -a "$LOG_FILE"; }
err()  { echo -e "${C_RED}[error]${C_RST} $*" | tee -a "$LOG_FILE"; }
warn() { echo -e "${C_YLW}[warn ]${C_RST} $*" | tee -a "$LOG_FILE"; }
section() {
    echo "" | tee -a "$LOG_FILE"
    echo "========================================" | tee -a "$LOG_FILE"
    echo "  $1" | tee -a "$LOG_FILE"
    echo "========================================" | tee -a "$LOG_FILE"
}

# ===== 参数解析 =====
while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply)
            MODE="apply"
            shift
            ;;
        --skip-steps)
            SKIP_STEPS="$2"
            shift 2
            ;;
        *)
            err "未知参数: $1"
            exit 2
            ;;
    esac
done

if [[ "$MODE" == "apply" && "${CONFIRM_APPLY:-}" != "$CONFIRM_APPLY_VALUE" ]]; then
    err "拒绝执行 --apply：请先设置 CONFIRM_APPLY=$CONFIRM_APPLY_VALUE"
    exit 2
fi

is_apply_mode() {
    [[ "$MODE" == "apply" ]]
}

should_skip() {
    local step="$1"
    if [[ -z "$SKIP_STEPS" ]]; then
        return 1
    fi
    IFS=',' read -ra skip_arr <<< "$SKIP_STEPS"
    for s in "${skip_arr[@]}"; do
        s_trimmed="$(echo "$s" | xargs)"
        if [[ "$s_trimmed" == "$step" ]]; then
            return 0
        fi
    done
    return 1
}

# ===== 飞书通知（lark-cli 文字消息）=====
FEISHU_CHAT_ID="${FEISHU_CHAT_ID:-oc_f04a9f65d4b780511cc3f402c7d54ac3}"

send_feishu_notification() {
    local subject="$1"
    local message="$2"
    local full_msg
    full_msg=$(printf "%b\n%b" "$subject" "$message")

    if command -v lark-cli &>/dev/null; then
        if lark-cli im +messages-send \
            --chat-id "$FEISHU_CHAT_ID" \
            --text "$full_msg" \
            --as bot &>/dev/null; then
            ok "飞书通知已发送（lark-cli）"
            return 0
        else
            warn "飞书通知发送失败（lark-cli），尝试 webhook 降级"
        fi
    fi

    if [[ -n "${FEISHU_WEBHOOK_URL:-}" ]]; then
        if python3 - "$subject" "$message" <<'PY'
import json
import os
import sys
import urllib.request

subject, message = sys.argv[1], sys.argv[2]
url = os.environ.get("FEISHU_WEBHOOK_URL")
payload = {
    "msg_type": "post",
    "content": {
        "post": {
            "zh_cn": {
                "title": subject,
                "content": [[{"tag": "text", "text": message}]],
            }
        }
    },
}
req = urllib.request.Request(
    url,
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=10) as resp:
    body = resp.read().decode("utf-8", errors="replace")
result = json.loads(body) if body else {}
if result.get("code", 0) != 0:
    raise SystemExit(f"Feishu webhook business error: {body}")
PY
        then
            ok "飞书通知已发送（webhook）"
            return 0
        else
            warn "飞书通知发送失败（webhook）"
        fi
    else
        warn "未配置 lark-cli 或 FEISHU_WEBHOOK_URL，跳过飞书通知"
    fi
}

# ===== 指标提取（从日志解析关键数字，构建结构化总结）=====
build_metrics_summary() {
    local log="$1"
    python3 - "$log" <<'PY'
import re, sys

log = open(sys.argv[1], encoding="utf-8").read()
lines = log.split("\n")

metrics = {}

# 步骤 1：质量报告
m = re.search(r'超长记忆: (\d+) / (\d+) 条', log)
if m:
    metrics["总记忆"] = m.group(2)
    metrics["超长记忆"] = m.group(1)
alert_count = len(re.findall(r'⚠️.*告警', log))
if alert_count:
    metrics["告警"] = str(alert_count)

# 步骤 2：超长记忆治理
m = re.search(r'阈值: >(\d+) 字符，候选: (\d+) 条', log)
if m:
    metrics["超长治理_阈值"] = m.group(1)
    metrics["超长治理_候选"] = m.group(2)
m = re.search(r'已归档: (\d+) 条', log)
if m:
    metrics["已归档"] = m.group(1)
m = re.search(r'已压缩替换: (\d+) 条', log)
if m:
    metrics["已压缩"] = m.group(1)

# 步骤 3：LSH 去重
m = re.search(r'级联删除: (\d+) 条', log)
if m:
    metrics["去重_删除"] = m.group(1)

# 步骤 4：聚类分析
m = re.search(r'写入 entities: (\d+)', log)
if m:
    metrics["新实体"] = m.group(1)
m = re.search(r'写入 unit_entities: (\d+)', log)
if m:
    metrics["实体关联"] = m.group(1)
m = re.search(r'写入 memory_links: (\d+)', log)
if m:
    metrics["因果链"] = m.group(1)
m = re.search(r'标记 (\d+) 条记忆', log)
if m:
    metrics["自动标记"] = m.group(1)
m = re.search(r'embedding 更新完成: (\d+)', log)
if m:
    metrics["embedding更新"] = m.group(1)

# 构建紧凑总结
parts = []
if "总记忆" in metrics:
    parts.append(f"📊 总记忆 {metrics['总记忆']} 条")
if "告警" in metrics:
    parts.append(f"⚠️ {metrics['告警']} 项告警")
if "超长记忆" in metrics:
    parts.append(f"📏 超长 {metrics['超长记忆']} 条")

if "已归档" in metrics:
    parts.append(f"🏛️ 归档 {metrics['已归档']} 条")
if "已压缩" in metrics:
    parts.append(f"✂️ 压缩 {metrics['已压缩']} 条")
if "去重_删除" in metrics:
    parts.append(f"🗑️ 去重 {metrics['去重_删除']} 条")

if "新实体" in metrics:
    parts.append(f"🏷️ 新实体 {metrics['新实体']}")
if "实体关联" in metrics:
    parts.append(f"🔗 关联 {metrics['实体关联']} 条")
if "因果链" in metrics:
    parts.append(f"⛓️ 因果链 {metrics['因果链']} 条")
if "自动标记" in metrics:
    parts.append(f"📌 标记 {metrics['自动标记']} 条")
if "embedding更新" in metrics:
    parts.append(f"🔄 向量 {metrics['embedding更新']} 条")

if not parts:
    parts.append("（无有效指标）")

print("  ".join(parts))
PY
}

# ===== 主流程 =====
log "Hindsight 记忆维护管线启动"
log "模式: $MODE"
log "日志: $LOG_FILE"
log "跳过步骤: ${SKIP_STEPS:-无}"
echo "CLUSTERING_DB_URL=${CLUSTERING_DB_URL:0:20}..." >> "$LOG_FILE"

OVERALL_STATUS="success"
FAILED_STEPS=""

# ===== 步骤 1: 质量报告 =====
if should_skip "1"; then
    warn "跳过步骤 1: 质量报告"
else
    section "① 质量报告"
    if python3 "$SCRIPT_DIR/memory_quality_report.py" --report-only 2>>"$LOG_FILE" | tee -a "$LOG_FILE"; then
        ok "质量报告完成"
    else
        err "质量报告失败"
        OVERALL_STATUS="partial"
        FAILED_STEPS="${FAILED_STEPS}1,"
    fi
fi

# ===== 步骤 2: 超长记忆治理 =====
if should_skip "2"; then
    warn "跳过步骤 2: 超长记忆治理"
else
    if is_apply_mode; then
        section "② 超长记忆治理 (long_memory_governance.py --apply)"
        if CLUSTERING_DB_URL="$CLUSTERING_DB_URL" python3 "$SCRIPT_DIR/long_memory_governance.py" --apply 2>>"$LOG_FILE" | tee -a "$LOG_FILE"; then
            ok "超长记忆治理完成"
        else
            err "超长记忆治理失败"
            OVERALL_STATUS="partial"
            FAILED_STEPS="${FAILED_STEPS}2,"
        fi
    else
        section "② 超长记忆治理 (dry-run)"
        if CLUSTERING_DB_URL="$CLUSTERING_DB_URL" python3 "$SCRIPT_DIR/long_memory_governance.py" 2>>"$LOG_FILE" | tee -a "$LOG_FILE"; then
            ok "超长记忆治理 dry-run 完成"
        else
            err "超长记忆治理 dry-run 失败"
            OVERALL_STATUS="partial"
            FAILED_STEPS="${FAILED_STEPS}2,"
        fi
    fi
fi

# ===== 步骤 3: MinHash LSH 跨条目去重 =====
if should_skip "3"; then
    warn "跳过步骤 3: MinHash LSH 去重"
else
    if is_apply_mode; then
        section "③ MinHash LSH 跨条目去重 (dedup_minhash.py --apply)"
        if CLUSTERING_DB_URL="$CLUSTERING_DB_URL" python3 "$SCRIPT_DIR/dedup_minhash.py" --apply 2>>"$LOG_FILE" | tee -a "$LOG_FILE"; then
            ok "MinHash LSH 去重完成"
        else
            err "MinHash LSH 去重失败"
            OVERALL_STATUS="partial"
            FAILED_STEPS="${FAILED_STEPS}3,"
        fi
    else
        section "③ MinHash LSH 跨条目去重 (dry-run)"
        if CLUSTERING_DB_URL="$CLUSTERING_DB_URL" python3 "$SCRIPT_DIR/dedup_minhash.py" 2>>"$LOG_FILE" | tee -a "$LOG_FILE"; then
            ok "MinHash LSH 去重 dry-run 完成"
        else
            err "MinHash LSH 去重 dry-run 失败"
            OVERALL_STATUS="partial"
            FAILED_STEPS="${FAILED_STEPS}3,"
        fi
    fi
fi

# ===== 步骤 4: 聚类分析 =====
if should_skip "4"; then
    warn "跳过步骤 4: 聚类分析"
else
    if is_apply_mode; then
        section "④ 聚类分析 --apply"
        if PYTHONPATH="$PROJECT_DIR/src:$PROJECT_DIR/scripts:${PYTHONPATH:-}" CLUSTERING_DB_URL="$CLUSTERING_DB_URL" python3 -c "
from clustering_analysis.cli import run
run(apply=True, dry_run=False, cleanup=False, force=True, skip_entity=False, config_path='config/default.yaml')
" 2>>"$LOG_FILE" | tee -a "$LOG_FILE"; then
            ok "聚类分析完成"
        else
            err "聚类分析失败"
            OVERALL_STATUS="partial"
            FAILED_STEPS="${FAILED_STEPS}4,"
        fi
    else
        section "④ 聚类分析 dry-run"
        if PYTHONPATH="$PROJECT_DIR/src:$PROJECT_DIR/scripts:${PYTHONPATH:-}" CLUSTERING_DB_URL="$CLUSTERING_DB_URL" python3 -c "
from clustering_analysis.cli import run
run(apply=False, dry_run=True, cleanup=False, force=True, skip_entity=False, config_path='config/default.yaml')
" 2>>"$LOG_FILE" | tee -a "$LOG_FILE"; then
            ok "聚类分析 dry-run 完成"
        else
            err "聚类分析 dry-run 失败"
            OVERALL_STATUS="partial"
            FAILED_STEPS="${FAILED_STEPS}4,"
        fi
    fi
fi

# ===== 步骤 6: 基线反馈闭环（Phase 6 — 基于 clustering_audit.log silhouette）=====
if should_skip "6"; then
    warn "跳过步骤 6: 基线反馈闭环"
else
    section "⑥ 基线反馈闭环"

    AUDIT_LOG="/root/.hermes/plugins/knowledge-navigation/clustering_audit.log"
    PREV_AUDIT="/root/.hermes/data/flywheel/clustering_baseline_prev.json"
    FLYWHEEL_DIR="$(dirname "$PREV_AUDIT")"
    mkdir -p "$FLYWHEEL_DIR"

    if [[ ! -f "$AUDIT_LOG" ]]; then
        warn "聚类审计日志不存在: $AUDIT_LOG，跳过对比"
    else
        LAST_SILHOUETTE=$(tail -1 "$AUDIT_LOG" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('silhouette', 'N/A'))
except: print('N/A')
" 2>/dev/null)

        if [[ "$LAST_SILHOUETTE" == "N/A" ]]; then
            warn "无法提取 silhouette，跳过对比"
        elif [[ ! -f "$PREV_AUDIT" ]]; then
            cp "$AUDIT_LOG" "$PREV_AUDIT"
            ok "首次运行，基线已保存 (silhouette=${LAST_SILHOUETTE})"
            _STEP_RESULTS+=("✅ Phase 6: 首次基线保存 (sil=${LAST_SILHOUETTE})")
        else
            PREV_SILHOUETTE=$(python3 -c "
import json
try:
    lines = open('$PREV_AUDIT').readlines()
    last = json.loads(lines[-1].strip())
    print(last.get('silhouette', 'N/A'))
except: print('N/A')
" 2>/dev/null)

            if [[ "$PREV_SILHOUETTE" != "N/A" ]]; then
                DIFF=$(python3 -c "
cur, prev = float('$LAST_SILHOUETTE'), float('$PREV_SILHOUETTE')
print(f'{cur - prev:.4f}')
" 2>/dev/null)

                # silhouette 绝对值下降 > 0.05 → 告警
                if (( $(echo "$DIFF < -0.05" | bc -l 2>/dev/null || echo 0) )); then
                    ALERT_MSG="🔴 聚类质量下降 ${DIFF} (prev=${PREV_SILHOUETTE} → cur=${LAST_SILHOUETTE})"
                    warn "$ALERT_MSG"
                    _STEP_RESULTS+=("⚠️ Phase 6: $ALERT_MSG")

                    DECLINE_FILE="$FLYWHEEL_DIR/clustering_decline_count"
                    CUR_DECLINE=$(cat "$DECLINE_FILE" 2>/dev/null || echo "0")
                    CUR_DECLINE=$((CUR_DECLINE + 1))
                    echo "$CUR_DECLINE" > "$DECLINE_FILE"

                    if [[ "$CUR_DECLINE" -ge 3 ]]; then
                        ESCALATED_MSG="🔥 聚类参数可能需要调整（连续 ${CUR_DECLINE} 周下降）"
                        err "$ESCALATED_MSG"
                        _STEP_RESULTS+=("🔥 Phase 6: $ESCALATED_MSG")
                    fi
                else
                    ok "基线对比: silhouette ${LAST_SILHOUETTE} vs prev ${PREV_SILHOUETTE} (Δ${DIFF})"
                    _STEP_RESULTS+=("✅ Phase 6: 基线稳定 (Δ${DIFF})")
                    echo "0" > "$FLYWHEEL_DIR/clustering_decline_count" 2>/dev/null
                fi
            fi
            cp "$AUDIT_LOG" "$PREV_AUDIT"
        fi
    fi
fi

if should_skip "5"; then
    warn "跳过步骤 5: 飞书通知"
else
    section "⑤ 飞书通知"

    if [[ "$OVERALL_STATUS" == "success" ]]; then
        STATUS_EMOJI="✅"
        STATUS_TEXT="全部成功"
    elif [[ "$OVERALL_STATUS" == "partial" ]]; then
        STATUS_EMOJI="⚠️"
        STATUS_TEXT="部分失败 (步骤: ${FAILED_STEPS%,})"
    else
        STATUS_EMOJI="❌"
        STATUS_TEXT="执行失败"
    fi

    # 从日志提取关键指标
    METRICS_LINE=$(build_metrics_summary "$LOG_FILE")

    SUBJECT="Hindsight 记忆维护周报"
    MESSAGE="${STATUS_EMOJI} 状态: ${STATUS_TEXT}
时间: $(date '+%Y-%m-%d %H:%M')
${METRICS_LINE}
日志: ${LOG_FILE}"

    send_feishu_notification "$SUBJECT" "$MESSAGE"
fi

# ===== 完成 =====
echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
if [[ "$OVERALL_STATUS" == "success" ]]; then
    ok "全部步骤完成 ✅"
elif [[ "$OVERALL_STATUS" == "partial" ]]; then
    warn "部分步骤完成 ⚠️ (失败: ${FAILED_STEPS%,})"
else
    err "执行失败 ❌"
fi
echo "日志: $LOG_FILE" | tee -a "$LOG_FILE"

# 返回状态（供 cron 判断）
if [[ "$OVERALL_STATUS" == "success" ]]; then
    exit 0
else
    exit 1
fi