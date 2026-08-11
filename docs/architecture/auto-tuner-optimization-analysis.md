# Auto-Tuner 参数自优化逻辑分析与改造方案（2026-08-10）

> 目标：弄清 Router 飞轮里 Auto-Tuner 为什么"不产生优化作用"，并给出可落地的改造方案。
> 范围：`scripts/flywheel-health-report/auto_tuner/tuner.py` + `config.py`（PARAM_DEFS / FEEDBACK_KEYS / TH）+ `analyzers/kn_judge.py`。
> 实证来源：生产 `/root/.hermes/data/flywheel/auto-tuner-{state,log}.jsonl` + `daily-summary-history.jsonl`。

> ⚠️ **状态更新（2026-08-11）**：本文为 2026-08-10 的问题分析稿。后续用户拍板**彻底移除 token 预算控制、仅保留实际消耗观测**（见 [data-flywheel-system-map.md §4.8](data-flywheel-system-map.md)），故文中 `token_budget*` 参数（§2.2 实证、§5.2 参数表）及 P1-1 目标函数里的 `− w3·token_cost` 负项**均已不适用**——预算相关调参纯属空转，已随预算移除一并下线。实际落地方向见 [data-flywheel-closed-loop.md](data-flywheel-closed-loop.md)：mask 级 KN Judge（h/kt/sag 分维度评分）+ 信任门控修复，使自优化器真正闭环。

---

## 1. 当前闭环（精简）

```
cron 每日一次
  └─ main()
       1. check_pause()                        暂停手动闸门
       2. handle_pending_restart()            验证上次调优是否生效（gateway 重启 + 抓 metrics_after + 判改善 + 写 state）
       3. 提取今天/昨天 scheduled 指标的 feedback 字段（_extract_metrics_before）
       4. are_all_params_converged()          全收敛则跳过
       5. select_param_to_tune(state)         选 1 个参数（virgin/remaining × confident/unconfident）
       6. read_env_param 当前值
       7. determine_direction()               方向决策
       8. validate_step()                     步幅安全
       9. 写 .env + 飞书提醒重启 + 记 pending_restart + update_state
```

**方向决策 `determine_direction` 的三条路径**：
- **首调（无 last_tune）**：位置策略——离哪个边界近就往反方向走。
- **有 last_tune 且已 applied（有 metrics_after）**：用 `_is_metric_improved` 多数票判改善 → 同向/反向。
- **有 last_tune 但 pending_restart（无 metrics_after）**：走位置策略。

**改善判定 `_is_metric_improved(name, direction, old, new)`**：
- `up_better`：`new >= old`
- `down_better`：`new <= old`
- `stable_ok`：`|Δ|/old < 10%` 即视为"改善"（不恶化就是好）

**反馈键方向（`_parse_feedback`）**：`kn_judge_*`、`kn_avg_score`=up_better；`router_empty_pct`/`sag_merge_zero_pct`/`router_error_rate`=down_better；其余产出类（sag_total_kept、memory_hindsight_count、skill_*、hindsight_count…）=stable_ok。

---

## 2. 实证：生产数据铁证

### 2.1 KN_MIN_SCORE 的 6 次调优（撞边界反转，无最优信号）
```
07-26 0.65→0.70 up   上次改善，继续同向
07-27 0.70→0.75 up   上次改善，继续同向
07-28 0.75→0.70 down 已达上限(0.8)，只能向下   ← 撞顶被迫反向
07-29 0.70→0.65 down 上次改善，继续同向
07-30 0.65→0.60 down 上次改善，继续同向
07-31 0.60→0.55 down 上次改善，继续同向
```
全程 reason 都是"上次改善、继续同向"。没有任何一步说"这里最优、停"。参数在 [0.55, 0.75] 间被推来推去，**0.75 未必比 0.65 好**，只是"没恶化"。

### 2.2 2026-08 全部 4 次调优 = 位置策略（随机游走）
```
08-03 token_budget_hindsight_ratio 0.4→0.45 up  当前值离最小值较近，向上调整
08-04 sag_search_threshold         0.5→0.55 up  当前值离最小值较近，向上调整
08-06 token_budget                4000→4500 up  当前值离最小值较近，向上调整
08-09 KN_MAX_RESULTS              3.0→4.0  up   当前值离最小值较近，向上调整
```
这些是 virgin 首调，走"位置策略"分支——**与任何指标改善无关**，等于把参数从默认往一个边界推。

### 2.3 kn_judge 反馈源长期缺失 / 极不稳定
```
date       samp   rel     avg     sag_kept
07-29      None   None    None    142
...  (07-30 ~ 08-06 连续 9 天 kn_judge 全 None) ...
08-07      65     0.5692  0.4523  230     ← 唯一可信日
08-08      8      None    None    108     ← 样本骤降
08-09      0      None    None    3       ← judge 没跑
```
**后果**：07-29~08-06 这 9 天里，所有依赖 `kn_judge_*` 的 KN 参数（占 PARAM_DEFS 一大半）反馈键全 None → 改善判定退化成"无反馈默认改善"或"位置策略"= **纯随机游走**。

### 2.4 恒同污染票未清干净
`sag_merge_zero_pct` 在所有历史里 **恒 = 0.0**，但 `_parse_feedback` 把它当 `down_better`（越低越好）。`0 <= 0` 恒真 → 它**永远投"改善"票**。与之前摘除的 `token_exhaust_pct`（恒为 0 的同向污染）是同一类问题，仍挂在 `KN_SAG_SEARCH_TOP_K` 的反馈里（`sag_merge_zero_pct,sag_total_kept`）。

---

## 3. 失效根因（6 条，均被实证支持）

**R1 — 反馈源 kn_judge 长期缺失/不稳定（最致命）**
judge 在 9 天里完全没数据、偶尔样本骤降。反馈源都不可信，"优化"自然退化为随机。前置依赖未满足。

**R2 — 首调 virgin 全走"位置策略"（=随机）**
首次调优不依赖任何指标，只按"离边界远近"推。08 月 4 次调优全是这条。等于无目标地挪参数。

**R3 — 改善判定退化为"没恶化就算好"（stable_ok 主导）**
`stable_ok` 用 `|Δ|/old < 10%` 判改善。sag_total_kept 等产出计数一天内难超 10% 变化 → 几乎恒判改善 → 永远同向 → 撞边界才反转。KN_MIN_SCORE 实证即是此模式。

**R4 — 恒同污染票未清干净**
`sag_merge_zero_pct` 恒=0 当 down_better，永久投改善票（R4 与已修的 token_exhaust_pct 同类）。

**R5 — 无目标函数 / 无 best-so-far 记忆**
没有"什么算最优"的明确定义，没有历史最佳值。改善只看"本次 vs 上次 before"，无全局搜索，参数在边界间被推来推去。

**R6 — 收敛=no_change 计数到阈值，不是找到最优**
`NO_CHANGE_LOCK_THRESHOLD=3`：撞边界/震荡后无变化→锁定。锁定值是"运动停止点"，未必最优。

---

## 4. 改造方案（按优先级，可分层落地）

### P0 — 止血：让调优"不乱动"（最小风险）
- **P0-1 修复 kn_judge 反馈源稳定性**：排查 07-29~08-06 全 None、08-08/09 骤降的根因（daily-report 的 judge 触发条件 `min_age_breakpoint_hours` / 数据窗口 / LLM 调用失败兜底是否静默吞掉）。确保 judge 每天稳定产出 ≥50 样本，否则整个自优化无意义。
- **P0-2 摘除恒同污染票**：`sag_merge_zero_pct` 从所有反馈键移除（同 token_exhaust_pct 处理）；或改为"有非零样本时才评估"。
- **P0-3 样本不足=不调该参数**：现在 judge 样本不足走"位置策略=随机"，改为"标记 unconfident、跳过该参数，等样本充足"（select_param_to_tune 已有 confident 概念，堵住 determine_direction 的 pending 兜底分支）。

### P1 — 目标函数化：真正朝目标优化（中等改造）
- **P1-1 定义单目标函数**：例如 `objective = w1·kn_judge_relevant_rate + w2·(1−router_empty_pct) − w3·token_cost`（token 只作负项、不截断，与"只观测"决策一致）。所有参数调优都朝**最大化 objective** 方向。

> 注：2026-08-11 决策改为**彻底移除 token 预算**，故 `token_cost` 负项不再适用；objective 实际改为围绕 mask 级 KN Judge 分维度相关率（`kn_judge_relevant_rate_{h,kt,sag}`）构建（见 data-flywheel-closed-loop.md §5）。
- **P1-2 因果绑定反馈键**：每个参数只绑"它真实影响、可观测的质量指标"，而非全局平均或纯计数。
  - KN_MIN_SCORE / KN_LAMBDA_MRR / KN_SCORE_SPAN_* 影响召回排序质量 → 应绑 **kn_judge 按 mask 分维度**的评分（需 judge 输出 hindsight/sag 子集分，目前疑似不区分）。
  - SAG 参数 → 绑"SAG 召回后被实际采纳/命中"的质量指标，不是 `sag_total_kept` 计数。
  - 没有细分指标的参数：**宁可不调**，也别优化无意义代理。
- **P1-3 引入 best-so-far 记忆**：记录每个参数的 `(value, objective)` 历史；改善判定 = 本次 objective > 该参数历史 best（替代"vs 上次 before"）。

### P2 — 搜索策略：粗→细区间搜索（完整真优化器）
- **P2-1 粗→细步长**：首次调用大步长探索（如 ±2 step），找到改善方向后切小步长精调；反向则减半步长再试，仍差则回滚到 best 并标记 explore。
- **P2-2 收敛定义改写**：围绕 best 的小邻域内目标不再提升 N 次 → 收敛（替代 no_change 计数）。
- **P2-3 振荡后进入 explore 新模式**，而非直接锁定。

### P3 — 工程加固
- `COOLDOWN_DAYS_AFTER_APPLY` 设为 1~2，让指标稳定一天再判改善，避免日期错配假改善。
- 保留振荡检测，但振荡惩罚后转入 explore。

---

## 5. 改造前后对比

| 维度 | 现状（实证） | 改造后（目标） |
|------|------|------|
| 反馈源 | kn_judge 9 天缺失 | 每天稳定 ≥50 样本 |
| 首调方向 | 位置策略（随机） | 基于 objective 的粗探索 |
| 改善判定 | stable_ok 噪声锁定 | objective > best |
| 污染票 | sag_merge_zero 恒投改善 | 摘除 |
| 样本不足 | 走随机位置策略 | 跳过该参数 |
| 收敛 | 撞边界 no_change 锁定 | best 邻域不再提升才收敛 |
| 调优结果 | 参数在边界间游走 | 朝 objective 最优点收敛 |

---

## 6. 风险与回滚
- P0 改动小、可逆（只动 config 反馈键 + 判定分支），风险低。
- P1/P2 改动调优核心逻辑，需先在 `--dry-run` 模式跑若干天验证 objective 单调改善，再切真实写 .env。
- 任何阶段出问题：`auto-tuner.pause` 文件可立即暂停；state 中 `initial_value` + `rollback_param_to_baseline` 可回滚到基线。
- judege 稳定性（P0-1）是 P1/P2 生效的前置，必须先解决，否则后续改造建立在不可信反馈上。
