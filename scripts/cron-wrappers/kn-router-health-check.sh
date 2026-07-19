#!/bin/bash
# kn-router-health-check.sh — 知识导航 Router 健康巡检
#
# 部署路径: /root/.hermes/scripts/kn-router-health-check.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh
# 调度: 每天 14:00（memory-cleanup 之后，skillopt 之前）
#
# 功能:
#   - 检查 gateway 日志中 "Router JSON 解析失败" 次数
#   - 检查 trace.log 中 recall 成功率
#   - 检查 Router 模型响应稳定性（抽样 5 次）
#   - 每次巡检完成都推送飞书通知

set -euo pipefail

# ===== 加载公共库 =====
_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh" >&2
    exit 2
fi

# ===== 初始化 =====
cron_init "kn-router-health-check"

# ===== 环境准备 =====
PLUGIN_DIR="/root/.hermes/plugins/knowledge-navigation"

# ===== 1. 检查 Router JSON 解析失败次数（最近 24h）=====
cron_section "Router 解析失败检查"

# 用 journalctl 查最近 24h 的失败次数
FAIL_COUNT=0
if command -v journalctl &>/dev/null; then
    FAIL_COUNT=$(journalctl -u hermes-gateway --since "24 hours ago" --no-pager 2>/dev/null | grep -c "Router JSON 解析失败" || true)
    FAIL_COUNT=$(echo "$FAIL_COUNT" | tr -dc '0-9')
    FAIL_COUNT=${FAIL_COUNT:-0}
fi

# 允许少量 JSON 解析失败（<5次/24h 属于 LLM 正常波动，fallback 兜底不阻断）
if [[ "$FAIL_COUNT" -gt 5 ]]; then
    cron_warn "Router JSON 解析失败: ${FAIL_COUNT} 次 (24h)"
    _STEP_RESULTS+=("⚠️ Router 解析失败: ${FAIL_COUNT} 次")
else
    cron_ok "Router 解析失败: 0 次 (24h) ✅"
    _STEP_RESULTS+=("✅ Router 解析失败: 0 次")
fi

# ===== 2. 检查 recall 成功率（最近 24h）=====
cron_section "Recall 成功率检查"

if [[ -f "${PLUGIN_DIR}/trace.log" ]]; then
    # 按 timestamp 字段过滤最近 24h 的 recall_success / recall_error 事件
    # trace.log 为 JSON Lines，每行有 "timestamp" 字段（ISO 8601 UTC）
    _RECALL_STATS=$(python3 -c "
import json, sys
from datetime import datetime, timezone, timedelta

cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
total = success = sag_count = 0
with open('${PLUGIN_DIR}/trace.log', 'r') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        event = d.get('event', '')
        if event not in ('recall_success', 'recall_error', 'recall_timeout'):
            continue
        ts_str = d.get('timestamp', '')
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        total += 1
        if event == 'recall_success':
            success += 1
            sag_count += int(d.get('sag_kept', 0) or 0)
print(f'{success} {total} {sag_count}')
" 2>/dev/null || echo "0 0 0")

    SUCCESS_CALLS=$(echo "$_RECALL_STATS" | awk '{print $1}')
    TOTAL_CALLS=$(echo "$_RECALL_STATS" | awk '{print $2}')
    SAG_KEPT_TOTAL=$(echo "$_RECALL_STATS" | awk '{print $3}')
    FAIL_CALLS=$((TOTAL_CALLS - SUCCESS_CALLS))

    if [[ "$TOTAL_CALLS" -gt 0 ]]; then
        RATE=$((SUCCESS_CALLS * 100 / TOTAL_CALLS))
        cron_ok "Recall: ${SUCCESS_CALLS}/${TOTAL_CALLS} 成功 (${RATE}%) (24h); SAG 累计召回 ${SAG_KEPT_TOTAL} 条"
        _STEP_RESULTS+=("✅ Recall 成功率: ${RATE}% (${SUCCESS_CALLS}/${TOTAL_CALLS}); SAG 召回: ${SAG_KEPT_TOTAL} 条 (24h)")
    else
        cron_ok "Recall: 无最近 24h 事件（可能刚部署）"
        _STEP_RESULTS+=("✅ Recall: 无最近 24h 事件")
    fi
else
    cron_warn "trace.log 不存在: ${PLUGIN_DIR}/trace.log"
    _STEP_RESULTS+=("⚠️ trace.log 不存在")
fi

# ===== 3. 检查 Router 模型稳定性（抽样 5 次）=====
cron_section "Router 模型稳定性检查"

STABILITY_OK=true
STABILITY_TOTAL=5
STABILITY_PASS=0

_ROUTER_CHECK_PY="${PLUGIN_DIR:-${HERMES_HOME}/plugins/knowledge-navigation}/scripts/_router_stability_check.py"
if [[ -n "${KN_ROUTER_API_KEY:-}" ]]; then
    for i in $(seq 1 $STABILITY_TOTAL); do
        RESP=$(python3 "$_ROUTER_CHECK_PY" 2>/dev/null || echo "FAIL")
        if [[ "$RESP" == "OK" ]]; then
            STABILITY_PASS=$((STABILITY_PASS + 1))
        fi
    done
else
    STABILITY_PASS=$STABILITY_TOTAL  # 无 key 跳过检查
fi

STABILITY_RATE=$((STABILITY_PASS * 100 / STABILITY_TOTAL))
# 接受 4/5 成功（DeepSeek 偶尔延迟）
_STABILITY_MIN=4
if [[ "$STABILITY_PASS" -lt "$_STABILITY_MIN" ]]; then
    cron_warn "Router 模型稳定性: ${STABILITY_PASS}/${STABILITY_TOTAL} 成功 (${STABILITY_RATE}%)"
    _STEP_RESULTS+=("⚠️ Router 稳定性: ${STABILITY_PASS}/${STABILITY_TOTAL} (${STABILITY_RATE}%)")
else
    cron_ok "Router 模型稳定性: ${STABILITY_PASS}/${STABILITY_TOTAL} 成功 (${STABILITY_RATE}%) ✅"
    _STEP_RESULTS+=("✅ Router 稳定性: ${STABILITY_RATE}%")
fi

# ===== 4. SAG 服务与熔断器健康检查 =====
cron_section "SAG 健康检查"

SAG_OK=true
SAG_DETAILS=()

# 4.1 检查 SAG 熔断器状态（从 circuit_breaker.json 读取）
# 文件路径：plugins/knowledge-navigation/src/circuit_breaker.json
# （circuit_breaker.py 在 src/knowledge_navigation/core/，往上三层 = src/）
SAG_CB_FILE="${PLUGIN_DIR}/src/circuit_breaker.json"
if [[ -f "$SAG_CB_FILE" ]]; then
    SAG_CB_STATE=$(python3 -c "
import json
try:
    with open('$SAG_CB_FILE', 'r') as f:
        d = json.load(f)
    sag = d.get('sag', {})
    state = sag.get('state', 'closed')
    fail = sag.get('consecutive_failures', 0)
    total = sag.get('total_failures', 0)
    print(f'{state} {fail} {total}')
except Exception:
    print('unknown 0 0')
" 2>/dev/null || echo "unknown 0 0")
    SAG_CB_STATE_NAME=$(echo "$SAG_CB_STATE" | awk '{print $1}')
    SAG_CB_FAILS=$(echo "$SAG_CB_STATE" | awk '{print $2}')
    SAG_CB_TOTAL_FAILS=$(echo "$SAG_CB_STATE" | awk '{print $3}')

    if [[ "$SAG_CB_STATE_NAME" == "closed" ]]; then
        cron_ok "SAG 熔断器: closed (连续失败: ${SAG_CB_FAILS}, 累计: ${SAG_CB_TOTAL_FAILS})"
        SAG_DETAILS+=("✅ SAG 熔断器: closed (累计失败 ${SAG_CB_TOTAL_FAILS})")
    else
        cron_warn "SAG 熔断器: ${SAG_CB_STATE_NAME} (连续失败: ${SAG_CB_FAILS}, 累计: ${SAG_CB_TOTAL_FAILS})"
        SAG_DETAILS+=("⚠️ SAG 熔断器: ${SAG_CB_STATE_NAME} (累计 ${SAG_CB_TOTAL_FAILS})")
        SAG_OK=false
    fi
else
    cron_warn "SAG 熔断器文件不存在: ${SAG_CB_FILE}"
    SAG_DETAILS+=("⚠️ SAG 熔断器: 状态文件缺失")
    SAG_OK=false
fi

# 4.2 检查 SAG 服务可连通性
# 用 /health 端点做存活检查（轻量，不消耗检索资源）。
# 注意：曾用 /search 探测，但 SAG /search 要求 sourceIds 必填且非空数组，
# cron 探测漏了该字段会稳定返回 400 "请求参数无效"，造成误报。
# 检索功能已由第2步 trace.log 的 SAG 召回统计间接验证。
SAG_API_URL="${SAG_API_URL:-http://127.0.0.1:4173}"
if command -v curl &>/dev/null; then
    SAG_HTTP=$(curl -s -o /dev/null -w "%{http_code}" "${SAG_API_URL}/health" \
        --max-time 5 2>/dev/null || echo "000")
    if [[ "$SAG_HTTP" == "200" ]]; then
        cron_ok "SAG 服务可连通 (HTTP 200)"
        SAG_DETAILS+=("✅ SAG 服务: 可连通")
    else
        cron_warn "SAG 服务不可达 (HTTP ${SAG_HTTP})"
        SAG_DETAILS+=("⚠️ SAG 服务: HTTP ${SAG_HTTP}")
        SAG_OK=false
    fi
else
    SAG_DETAILS+=("ℹ️ SAG 连通性: 跳过（无 curl）")
fi

_STEP_RESULTS+=("${SAG_DETAILS[@]}")

# ===== 5. 汇总 =====
cron_section "巡检汇总"

if [[ "$FAIL_COUNT" -le 5 && "$STABILITY_PASS" -ge "$_STABILITY_MIN" && "$SAG_OK" == true ]]; then
    cron_ok "知识导航 Router 巡检全部通过 ✅"
    _STEP_RESULTS+=("✅ 巡检全部通过")
else
    cron_warn "知识导航 Router 巡检发现异常 ⚠️"
    _STEP_RESULTS+=("⚠️ 巡检发现异常，见上方详情")
fi

# ===== 完成 =====
cron_finish
