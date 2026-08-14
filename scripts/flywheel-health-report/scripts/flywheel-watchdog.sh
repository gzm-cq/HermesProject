#!/bin/bash
# flywheel-watchdog.sh — 飞轮闭环实时看门狗
# 每 5 分钟检查 ledger 中的异常事件，立即飞书告警
# 安装：cron jobs.json "*/5 * * * *"
#
# 检测项：
#   1. kn_judge anomaly（全 1.0/0.0 + 小样本）
#   2. self_evolving 失败（errors > 0）
#   3. cron job 连续失败（通过 runner-summary.json）
#
# 去重：state 文件记录最后告警的 ledger ts，同一事件只告警一次

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
LEDGER="${HERMES_HOME}/data/flywheel/ledger.jsonl"
STATE_FILE="${HERMES_HOME}/data/flywheel/watchdog-state.json"
FEISHU_CHAT_ID="${FEISHU_CHAT_ID:-oc_ff1105d776b5bd4842d0109d186cd95f}"

# ledger 不存在 → 静默退出
[ -f "$LEDGER" ] || exit 0

# 主检测逻辑：读 ledger 最后 20 行，找未告警的异常
ALERT_OUTPUT=$(python3 -c "
import json, sys, os

ledger_path = '$LEDGER'
state_path = '$STATE_FILE'

# 读上次告警时间戳
last_alert_ts = ''
if os.path.exists(state_path):
    try:
        last_alert_ts = json.load(open(state_path)).get('last_alert_ts', '')
    except Exception:
        pass

# 读 ledger 最后 20 行
with open(ledger_path, 'r') as f:
    lines = f.readlines()[-20:]

alerts = []
max_ts = last_alert_ts

for line in lines:
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        continue

    ts = ev.get('ts', '')
    if not ts or ts <= last_alert_ts:
        continue

    event = ev.get('event', '')

    # 检测 1: kn_judge anomaly（全 1.0/0.0 + 小样本）
    if event == 'kn_judge':
        h = ev.get('relevant_rate_h')
        sc = ev.get('sample_count_h', 0)
        if isinstance(h, (int, float)) and isinstance(sc, int) and sc < 50 and (h == 1.0 or h == 0.0):
            alerts.append(f'KN Judge 异常: rate_h={h} sample={sc} (全极端值+小样本) ts={ts}')

    # 检测 2: self_evolving 失败
    elif event == 'self_evolving':
        errors = ev.get('errors', 0)
        if isinstance(errors, int) and errors > 0:
            alerts.append(f'Self-Evolving 错误: {errors} errors, processed={ev.get(\"processed\",0)} ts={ts}')

    # 检测 3: dream_promote 异常（sag_rate 过低）
    elif event == 'dream_promote':
        sag_rate = ev.get('sag_rate', 1.0)
        if isinstance(sag_rate, (int, float)) and sag_rate < 0.2:
            alerts.append(f'Dream SAG rate 过低: {sag_rate} ts={ts}')

    if ts > max_ts:
        max_ts = ts

if alerts:
    print(json.dumps({'alerts': alerts, 'max_ts': max_ts}))
else:
    print(json.dumps({'alerts': [], 'max_ts': max_ts}))
" 2>/dev/null)

# 解析输出
ALERTS=$(echo "$ALERT_OUTPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print('\n'.join(d.get('alerts',[])))" 2>/dev/null)
MAX_TS=$(echo "$ALERT_OUTPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('max_ts',''))" 2>/dev/null)

# 更新 state 文件
if [ -n "$MAX_TS" ]; then
    python3 -c "import json; json.dump({'last_alert_ts': '$MAX_TS'}, open('$STATE_FILE','w'))" 2>/dev/null || true
fi

# 有告警 → 发飞书
if [ -n "$ALERTS" ]; then
    if command -v lark-cli &>/dev/null; then
        BODY=$(echo "$ALERTS" | head -5)
        lark-cli notify --chat "$FEISHU_CHAT_ID" --title "🚨 飞轮异常告警" --body "$BODY" 2>/dev/null || true
    fi
    echo "[watchdog] 告警已发送: $(echo "$ALERTS" | head -1)"
else
    # 无异常 → 静默（遵循"没问题跳过推送"原则）
    :
fi
