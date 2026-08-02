# Auto-Tuner 全面修复 SPEC v4

## 目标

按评估建议全面修复 auto-tuner.sh 的代码质量、安全性和参数优化问题。

## 改动范围

只改 `scripts/cron-wrappers/auto-tuner.sh`，不改其他文件。

## 修复清单

### P0 — 必改

#### Fix 1: 移除 `set -e`，改用显式错误处理（第 15 行）

**问题**：`set -euo pipefail` + `grep -q` 组合是常见陷阱。`grep -q` 在找到匹配后立即退出（SIGPIPE），在 `set -e` 下可能导致脚本意外终止。

**修复**：移除 `set -e`，保留 `set -uo pipefail`。所有关键操作（Python 调用、文件操作、systemctl）加显式返回值检查。

```bash
# 改前
set -euo pipefail

# 改后
set -uo pipefail
```

#### Fix 2: Python 错误日志化（全局替换 30+ 处）

**问题**：所有 `python3 -c ... 2>/dev/null || echo "fallback"` 静默吞掉错误，用户完全不知道 Python 报错。

**修复**：引入 `_py()` 辅助函数，将 Python 错误输出到 stderr 而非吞掉。或改为 `2>&1` 通过 `tee` 在日志中保留。

具体方案：所有重要的 Python 调用，将 `2>/dev/null` 改为 `2>&1` 或 `2>>${LOG_FILE/.jsonl/.err}`。`|| echo "fallback"` 保留作为最后手段。

```bash
# 改前
python3 -c "..." 2>/dev/null || echo "null"

# 改后
python3 -c "..." 2>/tmp/auto-tuner-err.log || { log_err "Python 错误，详见 /tmp/auto-tuner-err.log"; echo "null"; }
```

#### Fix 3: JSONL 三次读取 → 一次读取（`extract_metrics_for_tuning()` 第 608-677 行）

**问题**：`extract_metrics_for_tuning()` 从 stdin 读取同一个 `$HISTORY_FILE` 3 次。

**修复**：先读取全部 JSONL 到一个变量，然后传标准输入给 Python，在 Python 中一次性处理今天和昨天的数据。

```python
# 改为一次读取全部行
all_lines = list(sys.stdin)
today_target = '...'
yesterday_target = '...'
today_last = None
yesterday_last = None
for line in all_lines:
    rec = json.loads(line)
    if rec.get('report_type') == 'scheduled':
        if rec.get('date') == today_target:
            today_last = rec
        if rec.get('date') == yesterday_target:
            yesterday_last = rec
```

#### Fix 4: 原子状态文件写入（`save_state()` 第 547-551 行）

**问题**：`save_state()` 直接覆盖写，如果写入过程中脚本中断，state 文件损坏。

**修复**：先写入临时文件，再 rename。

```bash
save_state() {
    local state_json="$1"
    mkdir -p "$(dirname "$STATE_FILE")"
    # 原子写入：先写临时文件，再 rename
    echo "$state_json" > "${STATE_FILE}.tmp"
    mv "${STATE_FILE}.tmp" "$STATE_FILE"
}
```

### P1 — 应改

#### Fix 5: 参数池增加新参数

**问题**：当前 4 个参数反馈信号都弱。增加有明确反馈指标的参数。

**修复**：在 PARAM_DEFS 中增加：

```
"sag_search_threshold:0.5:0.3:0.8:0.05:sag_on_pct,sag_total_kept"
"token_budget:4000:2000:8000:500:token_exhaust_pct"
```

说明：
- `sag_search_threshold`：SAG 搜索阈值，控制 `sag_on_pct`（当前 25-68%，有明确信号）
- `token_budget`：Token 预算，控制 `token_exhaust_pct`（当前 2.7-7%，有明确信号）

#### Fix 6: 增加超时保护

**问题**：脚本没有超时保护，如果 Python 调用卡住，整个脚本会挂死。

**修复**：用 `timeout` 包装关键 Python 调用。

```bash
# 在关键 Python 调用前加 timeout
timeout 30 python3 -c "..." || { log_err "Python 超时"; echo "null"; }
```

#### Fix 7: dry-run 不写日志文件

**问题**：dry-run 写入 `auto-tuner-log.jsonl`，干扰正常调优记录查询。

**修复**：dry-run 只输出到 stdout，不写日志文件。

### P2 — 选改

#### Fix 8: 浮点精度清理

**问题**：日志中 `0.7000000000000001`、`0.5499999999999999`。

**修复**：在 Python 输出时用 `round(val, 2)` 或 `f"{val:.2f}"`。

## 不改的
- 不改飞轮健康报告调用链
- 不改 `.env` 文件格式
- 不改参数值的业务含义（只改参数池定义）
- 不改 `verify_restart()` / `update_log_entry()` 等已有闭环逻辑

## 执行流程

1. developer 按上述 8 个 Fix 修改 `auto-tuner.sh`
2. 主 session 跑 `bash -n` 语法检查
3. 主 session 跑 `--dry-run` 验证
4. 部署到运行环境