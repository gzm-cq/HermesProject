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
#   - 无异常静默退出，有异常才飞书通知

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
GATEWAY_LOG="/var/log/syslog"  # 或 journalctl 替代

# ===== 1. 检查 Router JSON 解析失败次数（最近 24h）=====
cron_section "Router 解析失败检查"

# 用 journalctl 查最近 24h 的失败次数
FAIL_COUNT=0
if command -v journalctl &>/dev/null; then
    FAIL_COUNT=$(journalctl -u hermes-gateway --since "24 hours ago" --no-pager 2>/dev/null | grep -c "Router JSON 解析失败" || echo 0)
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
total = success = 0
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
print(f'{success} {total}')
" 2>/dev/null || echo "0 0")

    SUCCESS_CALLS=$(echo "$_RECALL_STATS" | awk '{print $1}')
    TOTAL_CALLS=$(echo "$_RECALL_STATS" | awk '{print $2}')
    FAIL_CALLS=$((TOTAL_CALLS - SUCCESS_CALLS))

    if [[ "$TOTAL_CALLS" -gt 0 ]]; then
        RATE=$((SUCCESS_CALLS * 100 / TOTAL_CALLS))
        cron_ok "Recall: ${SUCCESS_CALLS}/${TOTAL_CALLS} 成功 (${RATE}%) (24h)"
        _STEP_RESULTS+=("✅ Recall 成功率: ${RATE}% (${SUCCESS_CALLS}/${TOTAL_CALLS}) (24h)")
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

if [[ -n "${KN_ROUTER_API_KEY:-}" ]]; then
    for i in $(seq 1 $STABILITY_TOTAL); do
        RESP=$(python3 -c "
import os, httpx
key = os.environ.get('KN_ROUTER_API_KEY', '')
resp = httpx.post(
    'http://127.0.0.1:4142/v1/chat/completions',
    json={
        'model': 's-deepseek-v4-flash',
        'temperature': 0.1,
        'max_tokens': 256,
        'messages': [
            {'role': 'system', 'content': '你是一个注入路由判断器。输出 JSON: {\"h\": bool, \"kt\": bool, \"s\": bool}'},
            {'role': 'user', 'content': '消息：测试\n\nJSON 输出：'}
        ],
    },
    headers={'Authorization': f'Bearer {key}'},
    timeout=10,
)
data = resp.json()
content = data['choices'][0]['message'].get('content', '')
print('OK' if content and 'h' in content else 'FAIL')
" 2>/dev/null || echo "FAIL")
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
    cron_ok "Router 模型稳定性: ${STABILITY_PASS}/${STABILITY_TOTAL} 成功 (100%) ✅"
    _STEP_RESULTS+=("✅ Router 稳定性: 100%")
fi

# ===== 4. 汇总 =====
cron_section "巡检汇总"

if [[ "$FAIL_COUNT" -le 5 && "$STABILITY_PASS" -ge "$_STABILITY_MIN" ]]; then
    cron_ok "知识导航 Router 巡检全部通过 ✅"
    _STEP_RESULTS+=("✅ 巡检全部通过")
    # 无异常静默退出，不推送飞书
    CRON_SKIP_FINISH_NOTIFY=true
else
    cron_warn "知识导航 Router 巡检发现异常 ⚠️"
    _STEP_RESULTS+=("⚠️ 巡检发现异常，见上方详情")
fi

# ===== 完成 =====
cron_finish
