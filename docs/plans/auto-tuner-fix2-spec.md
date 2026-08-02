# Auto-Tuner 修复 SPEC v3 — 参数轮询与收敛判定修复

## 目标

修复 auto-tuner 永远只调 KN_MIN_SCORE 的问题，让 4 个参数轮询调优，且收敛后自动切换。

## 现状

| 数据 | 值 |
|------|-----|
| state 文件 | 只有 `KN_MIN_SCORE`，`no_change_count=0, locked=false` |
| 日志 8 条 | 全部是 KN_MIN_SCORE，其他 3 个参数从未被选中 |
| 根因 1 | 参数遍历选第一个未收敛的，KN_MIN_SCORE 排第一且永不收敛，其他参数永远没机会 |
| 根因 2 | `no_change` 只在值不变时触发，但每次调优值都变，`no_change_count` 永远是 0 |
| 根因 3 | `metrics_improved` 用今天 vs 昨天的指标差，而非上次调优前后的指标对比 |

## 改动范围

只改 `scripts/cron-wrappers/auto-tuner.sh`，不改其他文件。

## 修复方案

### Fix A: 参数轮询逻辑 — 优先选未尝试过的参数

**问题**：当前遍历 PARAM_DEFS，选第一个 `is_param_converged()` 返回 false 的。KN_MIN_SCORE 排第一且永不收敛，其他 3 个参数永远没机会。

**修复**：在遍历时，优先选"从未进入 state 的参数"（未尝试过）。只有当所有参数都已尝试过，才选第一个未收敛的。

```bash
# 替换当前的选择逻辑（line 934-954）
# 新逻辑：
local selected_param=""
local selected_def=""
local first_unconverged_param=""
local first_unconverged_def=""

for param_def in "${PARAM_DEFS[@]}"; do
    local p_name="${param_def%%:*}"
    local p_converged
    p_converged=$(is_param_converged "$p_name" "$state")
    
    # 参数不在 state 中 → 未尝试过，优先选
    if ! echo "$state" | python3 -c "import json,sys; s=json.load(sys.stdin); print('$p_name' in s)" 2>/dev/null | grep -q true; then
        selected_param="$p_name"
        selected_def="$param_def"
        log_info "选中未尝试过的参数: ${selected_param}"
        break
    fi
    
    if [[ "$p_converged" != "true" ]]; then
        # 记录第一个未收敛的（备选）
        if [[ -z "$first_unconverged_param" ]]; then
            first_unconverged_param="$p_name"
            first_unconverged_def="$param_def"
        fi
    fi
done

# 如果所有参数都已尝试过，选第一个未收敛的
if [[ -z "$selected_param" && -n "$first_unconverged_param" ]]; then
    selected_param="$first_unconverged_param"
    selected_def="$first_unconverged_def"
    log_info "所有参数已尝试，选中第一个未收敛的: ${selected_param}"
fi
```

### Fix B: 振荡收敛检测

**问题**：`no_change` 只在值不变时触发，但每次调优值都变，永不收敛。

**修复**：在 `update_state()` 中增加振荡检测。当参数方向反转（up→down→up）时，视为振荡收敛。

在 state 中增加 `direction_history` 数组，记录最近 3 次调优方向。当检测到方向反转（如 up→down→up）时，将 `no_change_count += 2` 快速触发收敛。

```python
# 在 update_state() 的 Python 代码中增加
# 方向历史记录
if 'direction_history' not in pstate:
    pstate['direction_history'] = []
pstate['direction_history'].append('$direction')
# 只保留最近 3 次
if len(pstate['direction_history']) > 3:
    pstate['direction_history'] = pstate['direction_history'][-3:]

# 振荡检测：方向反转（up→down→up 或 down→up→down）
if len(pstate['direction_history']) >= 3:
    dh = pstate['direction_history']
    if dh[-3] != dh[-2] and dh[-2] != dh[-1] and dh[-3] == dh[-1]:
        # 振荡收敛！快速触发收敛
        pstate['no_change_count'] = pstate.get('no_change_count', 0) + 2
        pstate['direction_history'] = []  # 清空避免重复触发
```

### Fix C: metrics_improved 基准修正

**问题**：`metrics_improved` 用的是 `extracted_diff`（今天 vs 昨天的指标差），而非上次调优前后的指标对比。

**修复**：用 `last_tune.metrics_before` 对比 `today_metrics` 来判断改善。

```python
# 替换第 1135-1168 行的 metrics_improved 计算
# 旧: diff = json.loads('''$extracted_diff''')
# 新: 用 last_tune.metrics_before vs today_metrics
tune = json.loads('''$last_tune''')
before = tune.get('metrics_before', {})
today = json.loads('''$today_metrics''')

feeds = '$p_feedback'.split(',')
improved_count = 0
total_count = 0
for feed in feeds:
    feed = feed.strip()
    if not feed: continue
    total_count += 1
    old_val = before.get(feed)
    new_val = today.get(feed)
    if old_val is None or new_val is None: continue
    
    if feed == 'kn_avg_score':
        if new_val >= old_val: improved_count += 1
    elif feed in ('router_empty_pct', 'sag_merge_zero_pct'):
        if new_val <= old_val: improved_count += 1
    else:
        if old_val > 0:
            if abs(new_val - old_val) / old_val < 0.1: improved_count += 1
        else:
            improved_count += 1

print('true' if total_count == 0 or improved_count >= total_count / 2 else 'false')
```

### 不改的
- 不改其他参数池定义（`PARAM_DEFS` 数组）
- 不改 flywheel-health-report.sh
- 不改 verify_restart() / update_log_entry() / 30 天暂停逻辑（这些已在 v2 修复中完成）

## 执行流程

1. developer 按上述 3 个 Fix 修改 `auto-tuner.sh`
2. 主 session 跑 `--dry-run` 验证
3. 部署到运行环境