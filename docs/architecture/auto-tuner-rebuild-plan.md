# Auto-Tuner 改造实施方案（SCHEME）— P0 + P1 + P2

> 依据 `auto-tuner-optimization-analysis.md` 的根因分析，落地"完整优化器"。
> 目标：把 Auto-Tuner 从"随机游走 + 噪声锁定"改造成"有明确目标函数 + 可信反馈 + best-so-far 梯度搜索"的真优化器。
> 前置约束（已探查确认）：trace.log 历史仅 46 条 recall_success，judge `min_sample=50` 在日窗口下几乎永远不满足 → `kn_judge_relevant_rate` 长期 None；近期 recall_success 无四路命中字段，judge 不分 mask。

---

## 0. 改造范围（文件级）

| 文件 | 改动 |
|------|------|
| `analyzers/kn_judge.py` | P0-1：低样本可用性（min_sample 下调 + 小样本仍产出 relevant_rate/avg_relevance 带 `kn_judge_low_confidence` 标记；或滚动多日窗口累积样本） |
| `config.py` | P0-2：PARAM_DEFS 摘除 `sag_merge_zero_pct` 恒同票；P1：重定义各参数 feedback 为目标函数键；SAG 参数暂移出 |
| `auto_tuner/tuner.py` | P0-3：样本不足跳过该参数（堵 determine_direction 的 pending 位置策略分支）；P1/P2：objective 计算 + best-so-far + 粗→细搜索 + 收敛改写 |
| `analyzers/sag.py`（可选增强） | 新增 SAG 召回质量指标（命中后相关性），供后续把 SAG 参数重新纳入自优化 |

---

## 1. P0 止血（必须先做，否则后续建立在不可信反馈上）

### P0-1 judge 低样本可用性（根因修复）
现状：`run_judge_within_window` 在 `total_windowed < min_sample(50)` 时返回 `{kn_judge_sample_count, kn_judge_error}` —— **不含 relevant_rate** → report 写入 None → auto-tuner 退化。
改动（二选一，推荐 a）：
- **a) 下调 min_sample + 小样本仍 judge**：`min_sample` 由 50 降到 8~12；`sample < min_sample` 时仍用现有 windowed 全部样本跑 judge（不强制 200），产出 `kn_judge_relevant_rate` + 置 `kn_judge_low_confidence=True`（样本数写入 `kn_judge_sample_count`）。auto-tuner 读此标记降低该日反馈权重。
- **b) 滚动多日窗口**：`_since` 改为近 3 天累积（CN 窗口 ×3），保证样本量，但反馈滞后 3 天、与"调优次日判改善"的日期链冲突，需配套改 auto-tuner 的日期对齐。**不推荐**（引入新的日期错配）。

### P0-2 摘除恒同污染票
`sag_merge_zero_pct` 在所有历史恒 = 0.0，被 `_parse_feedback` 当 `down_better` → 0≤0 恒投"改善"票。从所有 PARAM_DEFS 的 feedback_csv 移除（同已处理的 token_exhaust_pct）。

### P0-3 样本不足 = 跳过该参数
现状：judge 样本不足时 `determine_direction` 走"位置策略"（与指标无关的随机推）。改为：依赖 `kn_judge_*` 的参数，若当日 `kn_judge_low_confidence` 或样本 < 阈值 → 在 `select_param_to_tune` 中归入 unconfident 组，**且当所有候选都 unconfident 时本次不调优**（而非退化为位置策略）。堵住随机游走入口。

---

## 2. P1 目标函数化

### P1-1 单目标函数
定义归一化目标（每个参数调优都朝**最大化 objective**）：
```
objective = w_rel * rel_rate
          + w_empty * (1 - empty_pct/100)
          - w_cost * token_penalty        # 观测约束，不截断
```
推荐默认权重：`w_rel=0.65, w_empty=0.35, w_cost=0`（token 仅观测、不分参数，本次不进目标；若后续要控成本再开 w_cost）。
- `rel_rate` ∈ [0,1]（来自 kn_judge，经 P0-1 后每天有值）
- `empty_pct` = `router_empty_pct`（召回全空比率，越低越好）

### P1-2 因果绑定（务实版：作用域代理，不做 mask 级）
受限于 judge 不分 mask + trace 无四路命中字段，**本次不做 mask 级 judge 改造**。采用作用域代理：
- **KN 参数**（MIN_SCORE / MAX_RESULTS / MAX_TEXT_LENGTH / TEMPORAL_* / LAMBDA_MRR / SCORE_SPAN_* / CROSS_DOMAIN_DEDUP / CAUSAL_BOOST_*）：反馈键统一改为 `kn_judge_relevant_rate, router_empty_pct`（即 objective 的两项输入）。这些是召回排序/门槛参数，与全局召回质量（judge 评估对象）因果最强。
- **SAG 参数**（SAG_MAX_INJECT / SEARCH_TOP_K / MIN_SCORE / POINTER_THRESHOLD）：当前唯一可用反馈是 `sag_total_kept`（产出计数，非质量），调它只优化"保留更多"而非"质量更好"。**务实处理：从 PARAM_DEFS 移出 SAG 参数**，避免优化无意义代理。待 `analyzers/sag.py` 新增"SAG 命中质量"指标后再纳入（列为后续增强，不在本次）。

### P1-3 best-so-far 记忆
state 每参数新增：`obj_history: [(date, value, objective)]`、`best_value`、`best_objective`。
改善判定改为：**本次 objective > best_objective**（带小 margin 防抖动），而非"vs 上次 before"。

---

## 3. P2 搜索策略

重写 `determine_direction` 为基于 objective 的梯度搜索：
1. **首调/从未调**：粗探——朝历史 best 方向大步（±2 step），若无 best 则取区间中点试探。
2. **已有历史**：朝 best 方向小步（1 step）精调；若目标下降（objective < best - margin）→ 反向减半步长再试一次；仍差 → 回滚到 best_value 并标记 `explore`（进入下一轮换方向探索）。
3. **收敛定义改写**：`best` 邻域（±1 step）内 objective 不再提升 ≥ `NO_IMPROVE_LOCK_THRESHOLD`（建议 3）次 → 收敛锁定。替代原 `no_change_count`。
4. **振荡**：方向来回（up/down/up）检测保留，但振荡后转入 `explore` 新模式（换一个未充分探索的区间点），而非直接锁定。

---

## 4. 验证与上线

1. **dry-run 验证**：`python -m flywheel_health_report.auto_tuner.tuner --dry-run` 连续跑若干天（或回放 history 模拟），确认：
   - judge 低样本日仍能产出 relevant_rate（带 low_confidence）
   - 调优方向由 objective 梯度驱动，而非位置策略/随机
   - SAG 参数不再被选中
   - 无恒同票污染
2. **灰度上线**：确认 dry-run 连续 N 天 objective 单调/改善后，切真实写 .env（保留 `auto-tuner.pause` 紧急制动 + `initial_value` 回滚）。
3. **回滚**：`rollback_param_to_baseline` + state 中 `initial_value` 保持可用。

---

## 5. 决策分叉（已确认）

- **mask 级 judge 改造**：用户拍板 **采纳**（原"本次不含"选项升级为实际方案）。
  理由：全局单分无法让参数绑定到它真正影响的那一路——例如 KN_MIN_SCORE 同时影响 h/kt 两路、SAG_* 只影响 sag 路，全局分一平均就把因果淹没了。改为单次 LLM 返回 `{overall,hindsight,knowledge_tree,sag}` 结构化评分，per-mask 聚合 + 因果绑定。

## 6. 实际落地（mask 级，已完成 2026-08-10）

> 方案从"P0 止血 + P1 目标函数"升级为"P0+P1+P2 + mask 级 judge 因果绑定"。详见 `auto-tuner-optimization-analysis.md` 与本轮改动说明。

- **根因**：日窗口 `min_sample=50` 在生产仅 ~46 条 recall_success 下永远不满足 → kn_judge 长期 None → 退化位置策略随机游走；且全局单分无法做因果绑定。
- **改动文件**：`recall_logger.py`(`_normalize_source`) / `analyzers/kn_judge.py`(mask 级 + 30 天滚动窗口) / `config.py`(`mask_window_days=30`+`mask_min_sample=12`+15 参数 mask 因果绑定) / `auto_tuner/tuner.py`(逐键信任门控 + 粗→细 2× 首调 + best-so-far 回滚) / `report.py`(mask 键写入)。
- **测试**：新增 `tests/test_analyzers/test_kn_judge_mask.py`（18 用例）；回归 flywheel-health-report **70 passed**、knowledge-navigation **237 passed**。
- **测试抓出生产 bug**：`run_judge_within_window` 中 naive/aware datetime 比较 `TypeError`，已修。
- **部署**：knowledge-navigation(38,重启网关) + flywheel-health-report(28) 经 `deploy/deploy.sh --yes` 上线；`--dry-run` 验证状态机正常执行。
- **效果预期**：滚动窗口使 judge 样本升至 ~28–46/路（≥`min_sample=20`、`mask_min_sample=12`），judge 从长期不可信变为可信，自优化器由"随机游走"升级为"per-route 可信反馈 + 粗→细梯度 + best-so-far 记忆"的真优化器；第四路(SAG↔梦境↔Wiki)质量现可经 sag 路 judge 反向驱动 SAG_* 参数。
