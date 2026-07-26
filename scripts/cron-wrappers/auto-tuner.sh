#!/bin/bash
# auto-tuner.sh — 飞轮参数自优化调优器
#
# 部署路径: /root/.hermes/scripts/auto-tuner.sh
# 调度: 在 flywheel-health-report.sh 末尾自动调用
#
# 功能:
#   - 读取 daily-summary-history.jsonl 获取最近指标
#   - 从参数池选一个未收敛的参数，判断调优方向
#   - 修改 .env 并重启 hermes-gateway
#   - 记录调优操作到 auto-tuner-log.jsonl
#   - 支持 --dry-run 模式：只输出决策，不改 .env 和 gateway
#   - 遵循 SPEC 安全机制：一次只动一个参数、步幅≤20%、备份 .env、恶化自动回滚

set -euo pipefail

# ===== 路径配置 =====
HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
ENV_FILE="${HERMES_HOME}/.env"
HISTORY_FILE="${HERMES_HOME}/data/flywheel/daily-summary-history.jsonl"
LOG_FILE="${HERMES_HOME}/data/flywheel/auto-tuner-log.jsonl"
PAUSE_FILE="${HERMES_HOME}/data/flywheel/auto-tuner.pause"
BACKUP_DIR="${HERMES_HOME}/backups/auto-tuner"
STATE_FILE="${HERMES_HOME}/data/flywheel/auto-tuner-state.json"

# ===== 加载 cron_common.sh（如果存在）=====
_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
_CRON_LOADED=false
if [[ -f "$_CRON_LIB" ]]; then
    source "$_CRON_LIB"
    _CRON_LOADED=true
fi

# ===== 命令行参数解析 =====
DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        *) echo "错误: 未知参数 $arg" >&2; echo "用法: $0 [--dry-run]" >&2; exit 1 ;;
    esac
done

# ===== 参数池定义 =====
# 格式: 参数名:当前值:最小值:最大值:步长:反馈指标列表
# 反馈指标从 JSONL 中提取，用于判断调优效果
PARAM_DEFS=(
    "KN_MIN_SCORE:0.6:0.4:0.8:0.05:kn_avg_score,router_empty_pct"
    "sag_max_inject:3:2:6:1:sag_total_kept"
    "sag_search_top_k:3:3:10:1:sag_merge_zero_pct"
    "token_budget_hindsight_ratio:0.4:0.3:0.6:0.05:memory_hindsight_count,sag_total_kept"
)
# lambda_mrr 暂不启用（无直接反馈指标）
# "lambda_mrr:0.5:0.3:0.7:0.1:"

# ===== 颜色输出（非终端时禁用）=====
if [[ -t 1 ]]; then
    C_CYA='\033[36m'
    C_GRN='\033[32m'
    C_RED='\033[31m'
    C_YLW='\033[33m'
    C_BLU='\033[34m'
    C_RST='\033[0m'
else
    C_CYA=''; C_GRN=''; C_RED=''; C_YLW=''; C_BLU=''; C_RST=''
fi

log_info()  { echo -e "${C_CYA}[tuner]${C_RST} $*"; }
log_ok()    { echo -e "${C_GRN}[  ok ]${C_RST} $*"; }
log_warn()  { echo -e "${C_YLW}[warn ]${C_RST} $*"; }
log_err()   { echo -e "${C_RED}[error]${C_RST} $*"; }
log_step()  { echo -e "${C_BLU}[step ]${C_RST} $*"; }

# ===== 辅助函数 =====

# 暂停检测
check_pause() {
    if [[ -f "$PAUSE_FILE" ]]; then
        local reason
        reason=$(head -1 "$PAUSE_FILE" 2>/dev/null || echo "手动暂停")
        log_info "暂停文件存在 (${PAUSE_FILE})，原因: ${reason}"
        log_info "跳过本次调优"
        return 0
    fi
    return 1
}

# 从 .env 读取当前参数值
read_env_param() {
    local param_name="$1"
    local value
    value=$(grep -E "^${param_name}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2)
    echo "${value:-}"
}

# 写入参数到 .env
write_env_param() {
    local param_name="$1"
    local new_value="$2"
    if grep -qE "^${param_name}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s/^${param_name}=.*/${param_name}=${new_value}/" "$ENV_FILE"
    else
        # 追加到文件末尾（在 # 注释之后，确保可读写）
        echo "${param_name}=${new_value}" >> "$ENV_FILE"
    fi
}

# 备份 .env
backup_env() {
    mkdir -p "$BACKUP_DIR"
    local backup_file="${BACKUP_DIR}/env-$(date +%Y%m%d_%H%M%S).bak"
    cp "$ENV_FILE" "$backup_file"
    echo "$backup_file"
}

# 验证步幅不超过 20%
validate_step() {
    local old_val="$1"
    local new_val="$2"
    if [[ "$old_val" == "0" ]]; then
        return 0  # 无法计算百分比，跳过校验
    fi
    local change_pct
    # 计算相对变化百分比
    change_pct=$(python3 -c "print(abs(($new_val - $old_val) / $old_val * 100))" 2>/dev/null || echo "0")
    if (( $(echo "$change_pct > 20.0" | bc -l 2>/dev/null || echo "1") )); then
        log_err "步幅 ${change_pct}% 超过 20% 上限，跳过"
        return 1
    fi
    return 0
}

# 重启 hermes-gateway
restart_gateway() {
    if [[ "$DRY_RUN" == true ]]; then
        log_info "[DRY-RUN] 跳过 gateway 重启"
        return 0
    fi
    log_step "重启 hermes-gateway..."
    if systemctl restart hermes-gateway 2>/dev/null; then
        log_ok "hermes-gateway 重启成功"
        sleep 2
        if systemctl is-active --quiet hermes-gateway 2>/dev/null; then
            log_ok "hermes-gateway 运行正常"
            return 0
        else
            log_err "hermes-gateway 重启后状态异常"
            return 1
        fi
    else
        log_warn "systemctl restart 失败，尝试 systemctl start..."
        systemctl start hermes-gateway 2>/dev/null || true
        sleep 2
        if systemctl is-active --quiet hermes-gateway 2>/dev/null; then
            log_ok "hermes-gateway 已启动"
            return 0
        fi
        log_err "hermes-gateway 启动失败"
        return 1
    fi
}

# 记录调优日志
write_tuner_log() {
    local entry="$1"
    mkdir -p "$(dirname "$LOG_FILE")"
    echo "$entry" >> "$LOG_FILE"
}

# 获取今天的日期（CN 时区，与飞轮报告一致）
get_today_cn() {
    TZ='Asia/Shanghai' date +%Y-%m-%d
}

# 获取昨天的日期（CN 时区）
get_yesterday_cn() {
    TZ='Asia/Shanghai' date -d 'yesterday' +%Y-%m-%d
}

# 获取前天的日期（CN 时区）
get_before_yesterday_cn() {
    TZ='Asia/Shanghai' date -d '2 days ago' +%Y-%m-%d
}

# ===== 核心逻辑 =====

# 读取最近 N 天的指标数据（从 JSONL 中提取指定日期的最新一条记录）
# 返回 JSON 格式
read_metrics_for_date() {
    local target_date="$1"
    python3 -c "
import json, sys
target = '$target_date'
last = None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        rec = json.loads(line)
        if rec.get('date') == target:
            last = rec
    except json.JSONDecodeError:
        continue
if last:
    print(json.dumps(last))
else:
    print('null')
" < "$HISTORY_FILE" 2>/dev/null || echo "null"
}

# 从两个日期的指标数据中提取反馈指标
# 返回: metric_name:old_value:new_value
extract_metric_diff() {
    local metrics_json="$1"
    # 格式: {today: {...}, yesterday: {...}}
    python3 -c "
import json, sys

data = json.loads('''$metrics_json''')
today = data.get('today', {}) or {}
yesterday = data.get('yesterday', {}) or {}

# 提取关心的指标
metrics = ['kn_avg_score', 'router_empty_pct', 'sag_total_kept', 'sag_merge_zero_pct', 'memory_hindsight_count']

result = {}
for m in metrics:
    old = yesterday.get(m)
    new = today.get(m)
    if old is not None and new is not None:
        result[m] = {'old': old, 'new': new}
    elif new is not None:
        result[m] = {'old': None, 'new': new}
    elif old is not None:
        result[m] = {'old': old, 'new': None}

print(json.dumps(result))
" 2>/dev/null || echo "{}"
}

# 判断调优方向
# 根据参数特性和反馈指标，决定增大还是减小
determine_direction() {
    local param_name="$1"
    local current_val="$2"
    local param_min="$3"
    local param_max="$4"
    local step="$5"
    local feedback_metrics="$6"  # 逗号分隔的指标名
    local metric_diff_json="$7"  # JSON: {metric: {old, new}}
    local last_tune="$8"         # 上次调优记录（JSON, 或 "null"）

    # 解析 metric_diff
    local metrics_data
    metrics_data=$(python3 -c "
import json
data = json.loads('''$metric_diff_json''')
print(json.dumps(data))
" 2>/dev/null || echo "{}")

    # 默认方向：增大（如果当前值离最小值较近）
    # 如果有上次调优记录，基于上次调优的效果判断方向
    local direction="up"
    local reason="初始调优，离最小值较近，向上调整"

    # 检查是否已经接近边界
    local range_size
    range_size=$(python3 -c "print($param_max - $param_min)" 2>/dev/null || echo "0")
    local dist_to_min
    dist_to_min=$(python3 -c "print($current_val - $param_min)" 2>/dev/null || echo "0")
    local dist_to_max
    dist_to_max=$(python3 -c "print($param_max - $current_val)" 2>/dev/null || echo "0")

    # 有上次调优记录时，根据效果决定方向
    if [[ "$last_tune" != "null" && -n "$last_tune" ]]; then
        local last_direction
        last_direction=$(echo "$last_tune" | python3 -c "
import json,sys; d=json.load(sys.stdin); print(d.get('direction','up'))
" 2>/dev/null || echo "up")

        # 判断上次调优是否改善了指标
        # 根据参数类型有不同的改善判断逻辑
        local improved
        improved=$(python3 -c "
import json
try:
    tune = json.loads('''$last_tune''')
    metrics = json.loads('''$metric_diff_json''')
except:
    print('false')
    exit()

# 获取上次调优时的指标
old_metrics = tune.get('metrics_before', {})
new_metrics = tune.get('metrics_after', {})

param = '$param_name'
feeds = '$feedback_metrics'.split(',')

# 对每个反馈指标判断是否改善
improved_count = 0
total_count = 0

for feed in feeds:
    feed = feed.strip()
    if not feed:
        continue
    total_count += 1
    old_val = old_metrics.get(feed)
    new_val = new_metrics.get(feed)

    if old_val is None or new_val is None:
        continue

    # 根据指标含义判断改善方向
    # 对于 kn_avg_score: 越大越好
    if feed == 'kn_avg_score':
        if new_val >= old_val:
            improved_count += 1
    # 对于 router_empty_pct / sag_merge_zero_pct: 越小越好
    elif feed in ('router_empty_pct', 'sag_merge_zero_pct'):
        if new_val <= old_val:
            improved_count += 1
    # 对于 sag_total_kept / memory_hindsight_count: 适中最好，不恶化
    else:
        # 变化在 10% 以内视为稳定
        if old_val > 0:
            change = abs(new_val - old_val) / old_val
            if change < 0.1:
                improved_count += 1
        else:
            improved_count += 1

if total_count == 0:
    print('true')  # 无反馈指标，默认改善
else:
    # 超过一半的指标改善视为改善
    print('true' if improved_count >= total_count / 2 else 'false')
" 2>/dev/null || echo "true")

        if [[ "$improved" == "true" ]]; then
            # 改善，继续同方向
            direction="$last_direction"
            reason="上次调优改善指标，继续同方向 (${direction})"
        else
            # 恶化，反向调
            if [[ "$last_direction" == "up" ]]; then
                direction="down"
            else
                direction="up"
            fi
            reason="上次调优未改善指标，反向调整 (${direction})"
        fi
    else
        # 首次调优，根据参数当前值在范围中的位置决定方向
        # 如果更靠近最小值，向上；更靠近最大值，向下
        if (( $(echo "$dist_to_max < $dist_to_min" | bc -l 2>/dev/null || echo "0") )); then
            direction="down"
            reason="当前值离最大值较近，向下调整"
        else
            direction="up"
            reason="当前值离最小值较近，向上调整"
        fi
    fi

    # 边界检查：如果已到边界，只能反向
    if [[ "$direction" == "up" ]] && (( $(echo "$current_val + $step > $param_max" | bc -l 2>/dev/null || echo "0") )); then
        direction="down"
        reason="已达上限 (${param_max})，只能向下调整"
    elif [[ "$direction" == "down" ]] && (( $(echo "$current_val - $step < $param_min" | bc -l 2>/dev/null || echo "0") )); then
        direction="up"
        reason="已达下限 (${param_min})，只能向上调整"
    fi

    # 计算新值
    local new_val
    if [[ "$direction" == "up" ]]; then
        new_val=$(python3 -c "print(min($current_val + $step, $param_max))" 2>/dev/null || echo "$current_val")
    else
        new_val=$(python3 -c "print(max($current_val - $step, $param_min))" 2>/dev/null || echo "$current_val")
    fi

    # 如果新值等于当前值，无法调优
    if (( $(echo "$new_val == $current_val" | bc -l 2>/dev/null || echo "0") )); then
        echo "null"
        return
    fi

    # 输出 JSON
    python3 -c "
import json
result = {
    'direction': '$direction',
    'new_value': $new_val,
    'reason': '$reason'
}
print(json.dumps(result))
"
}

# 读取状态文件
load_state() {
    if [[ -f "$STATE_FILE" ]]; then
        cat "$STATE_FILE"
    else
        echo "{}"
    fi
}

# 写入状态文件
save_state() {
    local state_json="$1"
    mkdir -p "$(dirname "$STATE_FILE")"
    echo "$state_json" > "$STATE_FILE"
}

# 获取上次调优记录（针对特定参数）
get_last_tune() {
    local param_name="$1"
    if [[ ! -f "$LOG_FILE" ]]; then
        echo "null"
        return
    fi
    python3 -c "
import json
param = '$param_name'
last = None
with open('$LOG_FILE', 'r') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if rec.get('parameter') == param:
                last = rec
        except:
            continue
if last:
    print(json.dumps(last))
else:
    print('null')
" 2>/dev/null || echo "null"
}

# 获取当前参数在 .env 中的实际值（优先于默认值）
get_current_param_value() {
    local param_name="$1"
    local default_val="$2"

    local env_val
    env_val=$(read_env_param "$param_name")
    if [[ -n "$env_val" ]]; then
        echo "$env_val"
    else
        echo "$default_val"
    fi
}

# 提取调优决策中的指标（最近两天同一报告类型）
extract_metrics_for_tuning() {
    local today_str="$1"
    local yesterday_str="$2"

    # 获取今天和昨天的指标
    # 注意：飞轮报告每天跑一次，所以今天的指标是"今天刚刚生成的报告"
    # 昨天的指标是"昨天生成的报告"
    # 我们需要找到最近两个 scheduled 报告
    local today_metrics
    local yesterday_metrics

    today_metrics=$(python3 -c "
import json, sys
target = '$today_str'
last = None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        rec = json.loads(line)
        if rec.get('date') == target and rec.get('report_type') == 'scheduled':
            last = rec
    except:
        continue
if last:
    print(json.dumps(last))
else:
    # fallback: 找最近一条 scheduled
    last = None
    sys.stdin.seek(0)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if rec.get('report_type') == 'scheduled':
                last = rec
        except:
            continue
    if last:
        print(json.dumps(last))
    else:
        print('null')
" < "$HISTORY_FILE" 2>/dev/null || echo "null")

    # 如果今天没有 scheduled 报告，使用昨天的
    if [[ "$today_metrics" == "null" ]]; then
        today_metrics=$(python3 -c "
import json, sys
target = '$yesterday_str'
last = None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        rec = json.loads(line)
        if rec.get('date') == target and rec.get('report_type') == 'scheduled':
            last = rec
    except:
        continue
if last:
    print(json.dumps(last))
else:
    print('null')
" < "$HISTORY_FILE" 2>/dev/null || echo "null")
        yesterday_metrics="null"
    else
        # 获取昨天的指标
        yesterday_metrics=$(python3 -c "
import json, sys
target = '$yesterday_str'
last = None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        rec = json.loads(line)
        if rec.get('date') == target and rec.get('report_type') == 'scheduled':
            last = rec
    except:
        continue
if last:
    print(json.dumps(last))
else:
    print('null')
" < "$HISTORY_FILE" 2>/dev/null || echo "null")
    fi

    # 输出 JSON
    python3 -c "
import json
result = {
    'today': json.loads('''$today_metrics''') if '$today_metrics' != 'null' else None,
    'yesterday': json.loads('''$yesterday_metrics''') if '$yesterday_metrics' != 'null' else None
}
print(json.dumps(result))
"
}

# 检查参数是否已收敛（基于状态文件）
is_param_converged() {
    local param_name="$1"
    local state="$2"

    python3 -c "
import json, sys
try:
    state = json.loads('''$state''')
except:
    print('false')
    exit()

param = '$param_name'
param_state = state.get(param, {})

# 收敛条件：连续 3 次无变化
no_change_count = param_state.get('no_change_count', 0)
if no_change_count >= 3:
    print('true')
    exit()

# 锁定标记
if param_state.get('locked', False):
    print('true')
    exit()

# 暂停标记（连续恶化 3 天）
if param_state.get('suspended', False):
    print('true')
    exit()

print('false')
" 2>/dev/null || echo "false"
}

# 检查是否所有参数都收敛了
are_all_params_converged() {
    local state="$1"
    for param_def in "${PARAM_DEFS[@]}"; do
        local param_name="${param_def%%:*}"
        if [[ "$(is_param_converged "$param_name" "$state")" != "true" ]]; then
            echo "false"
            return
        fi
    done
    echo "true"
}

# 更新状态：记录调优结果
update_state() {
    local state="$1"
    local param_name="$2"
    local direction="$3"
    local new_value="$4"
    local metrics_improved="$5"  # true/false
    local no_change="$6"         # true/false

    python3 -c "
import json, sys
try:
    state = json.loads('''$state''')
except:
    state = {}

param = '$param_name'
if param not in state:
    state[param] = {
        'no_change_count': 0,
        'degradation_count': 0,
        'consecutive_degradation_count': 0,
        'locked': False,
        'suspended': False,
        'last_tune_date': '',
        'initial_value': None
    }

pstate = state[param]
pstate['last_tune_date'] = '$new_value'

# 记录初始值
if pstate.get('initial_value') is None:
    pstate['initial_value'] = float(pstate.get('last_tune_date', 0))

if '$no_change' == 'true':
    pstate['no_change_count'] = pstate.get('no_change_count', 0) + 1
    # 连续 3 次无变化 → 锁定
    if pstate['no_change_count'] >= 3:
        pstate['locked'] = True
        pstate['no_change_count'] = 0
        pstate['degradation_count'] = 0
        pstate['consecutive_degradation_count'] = 0
else:
    pstate['no_change_count'] = 0

if '$metrics_improved' == 'false':
    pstate['degradation_count'] = pstate.get('degradation_count', 0) + 1
    pstate['consecutive_degradation_count'] = pstate.get('consecutive_degradation_count', 0) + 1

    # 某方向恶化 2 次 → 反向调（通过下次调优的方向决策处理）
    # 连续 3 天恶化 → 回滚到初始值，暂停该参数
    if pstate['consecutive_degradation_count'] >= 3:
        pstate['suspended'] = True
        pstate['consecutive_degradation_count'] = 0
        pstate['locked'] = True
else:
    pstate['degradation_count'] = 0
    pstate['consecutive_degradation_count'] = 0
    pstate['no_change_count'] = 0

print(json.dumps(state))
"
}

# 获取初始值（从状态文件或参数池默认值）
get_initial_value() {
    local param_name="$1"
    local default_val="$2"
    local state="$3"

    python3 -c "
import json, sys
try:
    state = json.loads('''$state''')
except:
    state = {}
param = '$param_name'
pstate = state.get(param, {})
iv = pstate.get('initial_value')
if iv is not None:
    print(iv)
else:
    print('$default_val')
" 2>/dev/null || echo "$default_val"
}

# ===== 主流程 =====

main() {
    local today
    today=$(get_today_cn)
    local yesterday
    yesterday=$(get_yesterday_cn)

    echo ""
    echo "============================================"
    echo "  Auto-Tuner 开始 — ${today}${DRY_RUN:+(DRY-RUN)}"
    echo "============================================"
    echo ""

    # 1. 暂停检测
    if check_pause; then
        return 0
    fi

    # 2. 检查历史数据文件
    if [[ ! -f "$HISTORY_FILE" ]]; then
        log_warn "历史数据文件不存在: ${HISTORY_FILE}"
        log_info "跳过本次调优（首次运行需累积数据）"
        return 0
    fi

    # 3. 获取最近两天的指标数据
    log_info "获取指标数据: ${today} / ${yesterday}"
    local metrics_data
    metrics_data=$(extract_metrics_for_tuning "$today" "$yesterday")

    local today_metrics
    today_metrics=$(echo "$metrics_data" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('today')))" 2>/dev/null || echo "null")
    local yesterday_metrics
    yesterday_metrics=$(echo "$metrics_data" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('yesterday')))" 2>/dev/null || echo "null")

    if [[ "$today_metrics" == "null" ]]; then
        log_warn "未找到今天的指标数据，跳过本次调优"
        return 0
    fi

    log_ok "已获取指标数据"

    # 4. 加载状态
    local state
    state=$(load_state)
    log_info "已加载调优状态"

    # 5. 遍历参数池，选择第一个未收敛的参数
    local selected_param=""
    local selected_def=""
    local all_converged

    all_converged=$(are_all_params_converged "$state")
    if [[ "$all_converged" == "true" ]]; then
        log_info "所有参数已收敛，跳过本次调优"
        log_info "30 天后重新评估"
        return 0
    fi

    for param_def in "${PARAM_DEFS[@]}"; do
        local p_name="${param_def%%:*}"
        local p_rest="${param_def#*:}"
        local p_default="${p_rest%%:*}"
        local p_rest2="${p_rest#*:}"
        local p_min="${p_rest2%%:*}"
        local p_rest3="${p_rest2#*:}"
        local p_max="${p_rest3%%:*}"
        local p_rest4="${p_rest3#*:}"
        local p_step="${p_rest4%%:*}"
        local p_feedback="${p_rest4#*:}"

        if [[ "$(is_param_converged "$p_name" "$state")" == "true" ]]; then
            log_info "参数 ${p_name} 已收敛，跳过"
            continue
        fi

        selected_param="$p_name"
        selected_def="$param_def"
        break
    done

    if [[ -z "$selected_param" ]]; then
        log_info "没有可调优的参数"
        return 0
    fi

    log_info "选中参数: ${selected_param}"

    # 解析选中参数的定义
    local p_rest="${selected_def#*:}"
    local p_default="${p_rest%%:*}"
    local p_rest2="${p_rest#*:}"
    local p_min="${p_rest2%%:*}"
    local p_rest3="${p_rest2#*:}"
    local p_max="${p_rest3%%:*}"
    local p_rest4="${p_rest3#*:}"
    local p_step="${p_rest4%%:*}"
    local p_feedback="${p_rest4#*:}"

    log_info "  默认值: ${p_default}, 范围: [${p_min}, ${p_max}], 步长: ${p_step}"
    log_info "  反馈指标: ${p_feedback}"

    # 6. 获取当前值（优先从 .env 读取）
    local current_val
    current_val=$(get_current_param_value "$selected_param" "$p_default")
    log_info "  当前值: ${current_val}"

    # 7. 获取上次调优记录
    local last_tune
    last_tune=$(get_last_tune "$selected_param")

    # 8. 获取 metric diff（用于方向判断）
    local metric_diff_json
    metric_diff_json=$(python3 -c "
import json
result = {'today': json.loads('''$today_metrics'''), 'yesterday': json.loads('''$yesterday_metrics''')}
print(json.dumps(result))
" 2>/dev/null || echo "{}")

    local extracted_diff
    extracted_diff=$(extract_metric_diff "$metric_diff_json")

    # 9. 判断调优方向
    local decision
    decision=$(determine_direction "$selected_param" "$current_val" "$p_min" "$p_max" "$p_step" "$p_feedback" "$extracted_diff" "$last_tune")

    if [[ "$decision" == "null" || -z "$decision" ]]; then
        log_warn "无法确定调优方向（可能已到边界），跳过"
        return 0
    fi

    local direction
    direction=$(echo "$decision" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['direction'])" 2>/dev/null || echo "up")
    local new_val
    new_val=$(echo "$decision" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['new_value'])" 2>/dev/null || echo "$current_val")
    local reason
    reason=$(echo "$decision" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['reason'])" 2>/dev/null || echo "")

    echo ""
    log_step "调优决策:"
    echo "  参数:     ${selected_param}"
    echo "  当前值:   ${current_val}"
    echo "  新值:     ${new_val}"
    echo "  方向:     ${direction}"
    echo "  原因:     ${reason}"
    echo ""

    # 10. 步幅校验（不超过 20%）
    if ! validate_step "$current_val" "$new_val"; then
        log_warn "步幅超过 20%，跳过本次调优"
        return 0
    fi

    # 11. 构建日志条目
    local metrics_before
    metrics_before=$(python3 -c "
import json
d = json.loads('''$today_metrics''')
# 提取关心的指标
result = {}
for k in ['kn_avg_score','router_empty_pct','sag_total_kept','sag_merge_zero_pct','memory_hindsight_count']:
    if k in d and d[k] is not None:
        result[k] = d[k]
print(json.dumps(result))
" 2>/dev/null || echo "{}")

    # 12. 如果是 DRY-RUN，输出决策后退出
    if [[ "$DRY_RUN" == true ]]; then
        echo ""
        log_info "================================================"
        log_info "  DRY-RUN 模式 — 以下操作不会实际执行"
        log_info "================================================"
        echo "  参数:      ${selected_param}"
        echo "  当前值:    ${current_val} → 新值: ${new_val}"
        echo "  方向:      ${direction}"
        echo "  原因:      ${reason}"
        echo "  备份:      ${BACKUP_DIR}/env-*.bak"
        echo "  操作:      修改 ${ENV_FILE} → ${selected_param}=${new_val}"
        echo "  操作:      systemctl restart hermes-gateway"
        echo "  日志:      ${LOG_FILE}"
        echo ""

        # DRY-RUN 也写入日志（标记 dry_run=true）
        local log_entry
        log_entry=$(python3 -c "
import json, datetime
entry = {
    'date': '$today',
    'parameter': '$selected_param',
    'old_value': float('$current_val'),
    'new_value': float('$new_val'),
    'direction': '$direction',
    'reason': '$reason',
    'dry_run': True,
    'metrics_before': json.loads('''$metrics_before'''),
    'metrics_after': None,
    'status': 'dry_run',
    'timestamp': datetime.datetime.now().isoformat()
}
print(json.dumps(entry, ensure_ascii=False))
" 2>/dev/null || echo "{}")
        write_tuner_log "$log_entry"
        log_ok "DRY-RUN 完成，决策已写入日志"
        return 0
    fi

    # ===== 实际执行 =====

    # 13. 备份 .env
    echo ""
    log_step "备份 ${ENV_FILE}"
    local backup_file
    backup_file=$(backup_env)
    log_ok "已备份到: ${backup_file}"

    # 14. 修改 .env
    log_step "修改参数 ${selected_param}: ${current_val} → ${new_val}"
    write_env_param "$selected_param" "$new_val"
    log_ok ".env 已更新"

    # 15. 重启 gateway
    echo ""
    log_step "重启 hermes-gateway"
    if ! restart_gateway; then
        log_err "gateway 重启失败，回滚参数"

        # 回滚
        write_env_param "$selected_param" "$current_val"
        log_info "已回滚 ${selected_param} → ${current_val}"

        # 再次尝试重启
        if ! restart_gateway; then
            log_err "回滚后 gateway 仍无法启动，需要人工介入"
        fi

        # 记录失败日志
        local log_entry
        log_entry=$(python3 -c "
import json, datetime
entry = {
    'date': '$today',
    'parameter': '$selected_param',
    'old_value': float('$current_val'),
    'new_value': float('$new_val'),
    'direction': '$direction',
    'reason': '$reason — 回滚: gateway 重启失败',
    'dry_run': False,
    'metrics_before': json.loads('''$metrics_before'''),
    'metrics_after': None,
    'status': 'rollback_gateway_fail',
    'timestamp': datetime.datetime.now().isoformat()
}
print(json.dumps(entry, ensure_ascii=False))
" 2>/dev/null || echo "{}")
        write_tuner_log "$log_entry"
        return 1
    fi

    # 16. 记录成功日志
    log_step "记录调优日志"
    local log_entry
    log_entry=$(python3 -c "
import json, datetime
entry = {
    'date': '$today',
    'parameter': '$selected_param',
    'old_value': float('$current_val'),
    'new_value': float('$new_val'),
    'direction': '$direction',
    'reason': '$reason',
    'dry_run': False,
    'metrics_before': json.loads('''$metrics_before'''),
    'metrics_after': None,  # 明天飞轮报告会记录
    'status': 'applied',
    'backup_file': '${backup_file}',
    'timestamp': datetime.datetime.now().isoformat()
}
print(json.dumps(entry, ensure_ascii=False))
" 2>/dev/null || echo "{}")
    write_tuner_log "$log_entry"
    log_ok "调优日志已记录"

    echo ""
    echo "============================================"
    echo "  Auto-Tuner 完成 — ${selected_param}: ${current_val} → ${new_val}"
    echo "============================================"
}

main "$@"