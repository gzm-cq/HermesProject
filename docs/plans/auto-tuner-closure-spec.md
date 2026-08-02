# Auto-Tuner 闭环修复 SPEC（v2 — 审计修正版）

## 目标

修复 auto-tuner 的闭环断裂问题，让参数调优不再每天反复调同一个参数，且每次调优生效后才进行下一次。

## 审计方法

结合三方面数据交叉验证：
1. **源码**：`scripts/cron-wrappers/auto-tuner.sh`（968 行，逐函数审计）
2. **WSL 运行环境**：state 文件、日志、.env、daily-summary-history、gateway 重启历史
3. **时序分析**：调优时间 vs gateway 重启时间 vs 指标生成时间

## 现状

### 运行环境实况

| 数据项 | 值 |
|--------|-----|
| state 文件 | **不存在**（`update_state()` 从未被调用） |
| 日志条数 | 5 条，全部是 KN_MIN_SCORE，4 条 pending_restart |
| .env 中参数 | 只有 `KN_MIN_SCORE=0.65`，其他 3 个从未被选中 |
| daily-summary | 27 天数据（07-08 ~ 07-28） |
| gateway 重启 | 频繁但不规律（07-26 当天 6 次，07-28 07:29-07:33 连续 8 次） |
| cron 调度 | 无 crontab/systemd timer，由 hermes 内部调度器驱动 |
| 调用链 | `flywheel-health-report.sh` line 100 调用 `auto-tuner.sh`（08:00） |

### 实际运行轨迹 vs Gateway 重启时序

```
07-25 18:41  dry_run    0.60→0.65  (不写 .env)
07-26 08:00  tune       0.65→0.70  pending_restart
              gateway 重启 16:24 ✅ → 0.70 生效
07-27 08:00  tune       0.70→0.75  pending_restart
              gateway 上次重启 07:35 ❌（在调优之前！）
              0.75 未生效 → 07-28 07:29 重启时 .env 已被覆盖
07-28 08:00  tune       0.75→0.70  pending_restart（撞上限 0.8 反向）
              gateway 上次重启 07:33 ❌（在调优之前！）
              0.70 未生效 → 07-29 08:26 重启时 .env 已被覆盖
07-29 08:00  tune       0.70→0.65  pending_restart
              gateway 重启 08:26 ✅ → 0.65 生效
```

**核心问题**：调优器每天 08:00 覆写 .env，但 gateway 重启时间不固定。中间 3 次调优（0.70、0.75、0.70）在生效前就被下一次覆写，从未被实际评估。

### 指标数据（daily-summary）

| 日期 | kn_avg_score | router_empty_pct | sag_total_kept | sag_merge_zero_pct | mem_hindsight |
|------|-------------|-----------------|---------------|-------------------|--------------|
| 07-24 | 0 | 0.9 | 79 | 34.9 | 6 |
| 07-25 | 0.573 | 1.2 | 219 | 2.6 | 0 |
| 07-26 | 0.573 | 6.0 | 369 | 0.0 | 2 |
| 07-27 | 0.534 | 7.4 | 273 | 0.0 | 0 |
| 07-28 | 0.598 | 2.9 | 157 | 0.0 | 4 |

指标波动与 KN_MIN_SCORE 调优无相关性，更多是日常使用噪声。

## 审计发现：9 个问题

### 原 SPEC 诊断的 5 个问题

| # | 问题 | 审计结论 | 详情 |
|---|------|---------|------|
| 1 | `update_state()` 从未被调用 | ✅ **准确** | [main() line 960-961](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L960-L961) 写完日志直接结束。state 文件不存在，`is_param_converged()` 永远返回 false |
| 2 | `metrics_after` 永远为 None | ✅ **准确** | [line 909, 953](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L909) 写死 `None`，5 条日志全部 `metrics_after: null` |
| 3 | 方向判断用错指标 | ✅ **准确** | [line 307-308](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L307-L308) 用 `tune.metrics_before` vs `tune.metrics_after`。`metric_diff_json` 被传入但**完全未使用**（line 301 解析后从未引用） |
| 4 | 无重启验证 | ✅ **准确** | 没有 `verify_restart()` 函数 |
| 5 | 其他参数未写入 .env | ❌ **误诊** | [write_env_param() line 99-104](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L99-L104) **已有追加逻辑**。真正原因是 state 从不存在 → KN_MIN_SCORE 永不收敛 → 每次都选第一个参数 |

### 原 SPEC 遗漏的 4 个问题

| # | 问题 | 代码位置 | 详情 |
|---|------|---------|------|
| 6 | **`update_state()` 自身有 bug** | [line 655-659](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L655-L659) | `last_tune_date` 存的是参数值（`$new_value`）而非日期；`initial_value` 在 `last_tune_date` 被赋值后读取，得到的是新值而非调优前的值 |
| 7 | **`validate_step()` 20% 规则对整数参数不可用** | [line 116-130](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L116-L130) | `sag_max_inject` 3→4 = 33.3% ❌，`sag_search_top_k` 3→4 = 33.3% ❌。2/4 参数永远无法调优 |
| 8 | **`extract_metrics_for_tuning()` 的 seek bug** | [line 504](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L504) | `sys.stdin.seek(0)` 在 pipe 上不可用（已实测确认），fallback 逻辑崩溃。实践中不触发（当天报告总是存在），但属于潜在隐患 |
| 9 | **30 天暂停无实际实现** | [line 767-770](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L767-L770) | 所有参数收敛时只 `return 0`，不创建 pause 文件，不设暂停日期。`check_pause()` 也只检查文件存在性，不检查日期。第二天会再次运行、再次返回，无实际暂停效果 |

## 改动范围

### 只改一个文件

`scripts/cron-wrappers/auto-tuner.sh`

### 不改的

- 不自动重启 gateway（cron job 不允许）
- 不改 flywheel-health-report.sh
- 不改参数池定义（`PARAM_DEFS` 数组）
- 不改 auto-tuner-cron.sh（目前未启用，由 flywheel 调用）

## 修复方案

### 闭环时序设计

```
每次 auto-tuner 运行（08:00）：

1. 检查上次调优记录（last_tune）
   ├─ 无记录 → 直接进入新调优（步骤 4）
   ├─ status=pending_restart
   │   ├─ verify_restart() = true（gateway 已重启）
   │   │   → 更新日志 status=applied
   │   │   → 进入步骤 2（验证效果）
   │   └─ verify_restart() = false（gateway 未重启）
   │       → 跳过本次调优
   │       → 发飞书提醒"请重启 gateway 使参数生效"
   │       → return 0
   └─ status=applied → 进入步骤 2

2. 验证上次调优效果
   → 用今天的指标（metric_diff_json.today）vs last_tune.metrics_before 比较
   → 判断 improved / degraded / no_change
   → 调用 update_state() 更新收敛状态

3. 检查收敛
   ├─ 当前参数收敛 → 切换下一个未收敛参数
   └─ 全部收敛 → 创建 30 天暂停文件，return 0

4. 新调优
   → determine_direction() 用 metric_diff_json 判断方向
   → 写 .env、发飞书通知、记日志（status=pending_restart）
   → 调用 update_state() + save_state()
```

### 修复清单

| # | 修复 | 优先级 | 原 SPEC | 变化 |
|---|------|--------|---------|------|
| 1 | main() 末尾调用 `update_state()` + `save_state()` | P0 | 有 | **修正**：需额外传入 `today` 和 `current_val` |
| 2 | `determine_direction()` 改用 `metric_diff_json` | P0 | 有 | **修正**：用 `metric.get(feed, {}).get('old/new')` 格式 |
| 3 | 新增 `verify_restart()` | P0 | 有 | **修正**：用 monotonic timestamp 比较，不用字符串日期 |
| 4 | `update_state()` 修复 bug | P0 | **无** | **新增**：`last_tune_date` 存日期，`initial_value` 存调优前值 |
| 5 | `validate_step()` 对整数参数特殊处理 | P0 | **无** | **新增**：整数参数按绝对步数判断（≤1 步允许） |
| 6 | main() 开头处理 pending_restart | P0 | **无** | **新增**：未重启则跳过 + 提醒，已重启则更新日志为 applied |
| 7 | 30 天暂停实现 | P1 | 有但描述不足 | **修正**：创建 pause 文件含到期日期，`check_pause()` 检查日期 |
| 8 | 新增 `update_log_entry()` 函数 | P1 | 有但无实现 | **新增**：更新日志中 pending_restart → applied |
| 9 | 修复 `extract_metrics_for_tuning()` seek bug | P2 | **无** | **新增**：fallback 用两次读取代替 seek |

### 删除的 Fix

- ~~原 Fix 4（.env 参数检查）~~：`write_env_param()` 已有追加逻辑，不需要

## 修改细节

### Fix 1: update_state() 修复 bug + 增加参数

**问题**：
- [line 655](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L655): `pstate['last_tune_date'] = '$new_value'` 存的是参数值
- [line 658-659](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L658-L659): `initial_value` 读取的是刚被赋值的 `last_tune_date`（= 新值）

**修复**：函数签名增加 `today` 和 `current_val` 参数：

```bash
update_state() {
    local state="$1"
    local param_name="$2"
    local direction="$3"
    local new_value="$4"
    local metrics_improved="$5"
    local no_change="$6"
    local tune_date="$7"       # 新增：今天日期
    local current_val="$8"     # 新增：调优前的当前值
```

Python 内部：
```python
# 修复前: pstate['last_tune_date'] = '$new_value'
# 修复后:
pstate['last_tune_date'] = '$tune_date'

# 修复前: pstate['initial_value'] = float(pstate.get('last_tune_date', 0))
# 修复后: 先记录初始值再更新
if pstate.get('initial_value') is None:
    pstate['initial_value'] = float('$current_val')
```

### Fix 2: determine_direction() 改用 metric_diff_json

**问题**：[line 297-352](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L297-L352) 中 `metric_diff_json` 被解析但从未使用，改善判断依赖永远为 None 的 `tune.metrics_after`。

**修复**：用 `metric_diff_json` 的 `{metric: {old, new}}` 格式替代 `tune.metrics_before/after`：

```python
# 修复前:
old_metrics = tune.get('metrics_before', {})
new_metrics = tune.get('metrics_after', {})

# 修复后:
metrics = json.loads('''$metric_diff_json''')
# metric_diff_json 格式: {"kn_avg_score": {"old": 0.573, "new": 0.534}, ...}

for feed in feeds:
    feed = feed.strip()
    if not feed:
        continue
    total_count += 1
    m = metrics.get(feed, {})
    old_val = m.get('old')
    new_val = m.get('new')
    if old_val is None or new_val is None:
        continue
    # ... 后续改善判断逻辑不变
```

### Fix 3: verify_restart()

```bash
verify_restart() {
    local tune_timestamp="$1"  # 上次调优的 ISO 时间戳

    if ! systemctl is-active --quiet hermes-gateway; then
        return 1
    fi

    # 用 monotonic 时间戳比较，避免时区问题
    local gateway_start_epoch
    gateway_start_epoch=$(systemctl show hermes-gateway \
        --property=ActiveEnterTimestampMonotonic 2>/dev/null | cut -d= -f2)

    local tune_epoch
    tune_epoch=$(date -d "$tune_timestamp" +%s 2>/dev/null || echo "0")

    # gateway 的 monotonic 时间戳是纳秒，需转换
    # 也可以用 wall clock: ActiveEnterTimestamp
    local gateway_start_wall
    gateway_start_wall=$(systemctl show hermes-gateway \
        --property=ActiveEnterTimestamp 2>/dev/null | cut -d= -f2)
    local gateway_epoch
    gateway_epoch=$(date -d "$gateway_start_wall" +%s 2>/dev/null || echo "0")

    if [[ "$gateway_epoch" -gt "$tune_epoch" ]]; then
        return 0  # 已重启
    fi
    return 1  # 未重启
}
```

### Fix 4: main() 开头处理 pending_restart

在 main() 的参数选择之前（line 760 左右），插入：

```bash
# 检查上次调优是否已生效
local last_tune_any
last_tune_any=$(python3 -c "
import json
last = None
with open('$LOG_FILE', 'r') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try:
            rec = json.loads(line)
            if not rec.get('dry_run', False):
                last = rec
        except: continue
if last:
    print(json.dumps(last))
else:
    print('null')
" 2>/dev/null || echo "null")

if [[ "$last_tune_any" != "null" ]]; then
    local last_status
    last_status=$(echo "$last_tune_any" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    local last_timestamp
    last_timestamp=$(echo "$last_tune_any" | python3 -c "import json,sys; print(json.load(sys.stdin).get('timestamp',''))" 2>/dev/null)

    if [[ "$last_status" == "pending_restart" ]]; then
        if verify_restart "$last_timestamp"; then
            # 已重启 → 更新日志为 applied
            log_ok "上次调优已生效（gateway 已重启）"
            update_log_entry "$last_tune_any" "applied"
        else
            # 未重启 → 跳过本次调优，提醒重启
            log_warn "上次调优尚未生效（gateway 未重启），跳过本次调优"
            notify_restart_reminder "$last_tune_any"
            return 0
        fi
    fi
fi
```

### Fix 5: validate_step() 对整数参数特殊处理

```bash
validate_step() {
    local old_val="$1"
    local new_val="$2"
    local step="$3"  # 新增：步长参数

    if [[ "$old_val" == "0" ]]; then
        return 0
    fi

    # 整数参数（步长 >= 1）按绝对步数判断，不走百分比
    if (( $(echo "$step >= 1" | bc -l 2>/dev/null || echo "0") )); then
        local abs_diff
        abs_diff=$(python3 -c "print(abs($new_val - $old_val))" 2>/dev/null || echo "0")
        if (( $(echo "$abs_diff <= $step" | bc -l 2>/dev/null || echo "0") )); then
            return 0
        else
            log_err "步幅 ${abs_diff} 超过步长 ${step}，跳过"
            return 1
        fi
    fi

    # 浮点参数走 20% 百分比规则
    local change_pct
    change_pct=$(python3 -c "print(abs(($new_val - $old_val) / $old_val * 100))" 2>/dev/null || echo "0")
    if (( $(echo "$change_pct > 20.0" | bc -l 2>/dev/null || echo "1") )); then
        log_err "步幅 ${change_pct}% 超过 20% 上限，跳过"
        return 1
    fi
    return 0
}
```

调用处也需要传入 step：`validate_step "$current_val" "$new_val" "$p_step"`

### Fix 6: update_log_entry()

```bash
# 更新日志条目状态（pending_restart → applied）
update_log_entry() {
    local tune_entry="$1"  # JSON
    local new_status="$2"

    local param_name tune_date
    param_name=$(echo "$tune_entry" | python3 -c "import json,sys; print(json.load(sys.stdin).get('parameter',''))" 2>/dev/null)
    tune_date=$(echo "$tune_entry" | python3 -c "import json,sys; print(json.load(sys.stdin).get('date',''))" 2>/dev/null)

    python3 -c "
import json
param = '$param_name'
date = '$tune_date'
new_status = '$new_status'

lines = []
with open('$LOG_FILE', 'r') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if rec.get('parameter') == param and rec.get('date') == date:
                rec['status'] = new_status
            lines.append(json.dumps(rec, ensure_ascii=False))
        except:
            lines.append(line)

with open('$LOG_FILE', 'w') as f:
    for line in lines:
        f.write(line + '\n')
" 2>/dev/null
}
```

### Fix 7: 30 天暂停实现

```bash
# 所有参数收敛时
if [[ "$all_converged" == "true" ]]; then
    local pause_until
    pause_until=$(date -d '+30 days' +%Y-%m-%d)
    echo "{\"pause_until\": \"${pause_until}\", \"reason\": \"所有参数已收敛\"}" > "$PAUSE_FILE"
    log_info "所有参数已收敛，暂停至 ${pause_until}"
    return 0
fi
```

`check_pause()` 修改：
```bash
check_pause() {
    if [[ ! -f "$PAUSE_FILE" ]]; then
        return 1
    fi
    # 检查暂停是否已过期
    local pause_until
    pause_until=$(python3 -c "
import json
try:
    d = json.load(open('$PAUSE_FILE'))
    print(d.get('pause_until', ''))
except:
    print('')
" 2>/dev/null)

    if [[ -n "$pause_until" ]]; then
        local today_epoch pause_epoch
        today_epoch=$(date +%s)
        pause_epoch=$(date -d "$pause_until" +%s 2>/dev/null || echo "0")
        if [[ "$today_epoch" -ge "$pause_epoch" ]]; then
            log_info "暂停已到期（${pause_until}），删除暂停文件，恢复调优"
            rm -f "$PAUSE_FILE"
            return 1
        fi
        log_info "暂停中（至 ${pause_until}），跳过本次调优"
        return 0
    fi

    # 无日期的暂停文件（手动暂停）
    local reason
    reason=$(head -1 "$PAUSE_FILE" 2>/dev/null || echo "手动暂停")
    log_info "暂停文件存在，原因: ${reason}"
    return 0
}
```

### Fix 8: extract_metrics_for_tuning() seek bug

[line 504](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L504) 的 `sys.stdin.seek(0)` 在 pipe 上不可用。改为先收集所有行再处理：

```python
# 修复前:
sys.stdin.seek(0)
for line in sys.stdin:
    ...

# 修复后: 先读取所有行
all_lines = sys.stdin.readlines()
for line in all_lines:
    ...
```

### Fix 9: main() 末尾调用 update_state()

在 [line 961](file:///d:/HermesProject/scripts/cron-wrappers/auto-tuner.sh#L961) 之后追加：

```bash
# 判断上次调优是否改善（用 metric_diff 的 today vs yesterday）
local metrics_improved="true"
local no_change="false"

if [[ "$last_tune" != "null" ]]; then
    metrics_improved=$(python3 -c "
import json
try:
    tune = json.loads('''$last_tune''')
    diff = json.loads('''$extracted_diff''')
except:
    print('true')
    exit()

before = tune.get('metrics_before', {})
feeds = '$p_feedback'.split(',')

improved_count = 0
total_count = 0
for feed in feeds:
    feed = feed.strip()
    if not feed: continue
    total_count += 1
    m = diff.get(feed, {})
    old_val = m.get('old')
    new_val = m.get('new')
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
" 2>/dev/null || echo "true")

    if (( $(echo "$new_val == $current_val" | bc -l 2>/dev/null || echo "0") )); then
        no_change="true"
    fi
fi

local new_state
new_state=$(update_state "$state" "$selected_param" "$direction" "$new_val" \
    "$metrics_improved" "$no_change" "$today" "$current_val")
save_state "$new_state"
log_ok "调优状态已保存"
```

## 问题清单

### P0 — 修复闭环断裂

- [ ] **Fix 1**: `update_state()` 修复 bug（`last_tune_date` 存日期，`initial_value` 存调优前值）
- [ ] **Fix 2**: `determine_direction()` 改用 `metric_diff_json` 判断改善
- [ ] **Fix 3**: 新增 `verify_restart()` 检查 gateway 是否已重启
- [ ] **Fix 4**: main() 开头处理 pending_restart（未重启→跳过+提醒，已重启→更新日志）
- [ ] **Fix 5**: `validate_step()` 对整数参数按绝对步数判断
- [ ] **Fix 6**: main() 末尾调用 `update_state()` + `save_state()`

### P1 — 补充完善

- [ ] **Fix 7**: 新增 `update_log_entry()` 更新日志状态
- [ ] **Fix 8**: 30 天暂停实现（pause 文件含到期日期）
- [ ] **Fix 9**: 修复 `extract_metrics_for_tuning()` seek bug

### P2 — 增强（暂不实施）

- 步长自适应递减（接近最优值时自动缩小步长）
- 异常检测：如果指标数据与之前差异过大（system state change），暂不调优
- 调优后第二天指标恶化超标时自动回滚

## 测试

1. `bash auto-tuner.sh --dry-run` — 检查决策逻辑（不会写 .env/state）
2. 检查 state 文件生成：`cat /root/.hermes/data/flywheel/auto-tuner-state.json`
3. 检查收敛判断：连续 3 次无变化后 `is_param_converged` 返回 true
4. 检查参数切换：KN_MIN_SCORE 收敛后，下次选中 sag_max_inject
5. 检查 validate_step：sag_max_inject 3→4 不再被 20% 规则拦截
6. 检查 pending_restart 流程：模拟 gateway 未重启 → 跳过 + 提醒
7. 检查 30 天暂停：所有参数收敛后 pause 文件包含到期日期

## 执行流程

1. 主 session 写 SPEC（本文档）
2. developer 修改 auto-tuner.sh（Fix 1-9）
3. 主 session 验证：`bash -n auto-tuner.sh` 语法检查 + 逻辑审查
4. 主 session 部署：`./deploy/deploy.sh deploy cron-wrappers --yes`
5. 主 session 手动触发验证：`bash ~/.hermes/scripts/auto-tuner.sh --dry-run`
6. 观察下一次自动运行（08:00）的日志，确认闭环逻辑
