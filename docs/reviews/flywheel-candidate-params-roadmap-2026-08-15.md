# 飞轮闭环参数接入路线（候选参数 · 待补质量指标清单）

> 日期：2026-08-15 ｜ 关联：`flywheel-closed-loop-review-2026-08-15.md`
> 背景：飞轮闭环收尾 4 项指令完成后，全链路审计扫出一批散落阈值常量，但绝大多数**缺「自动质量反馈」**，盲目接入会变成指令④要修的"无效参数"。本清单将它们系统梳理，作为后续闭环接入路线。

## 0. 接入闭环的三要件（判定标准）

一个参数要走通 `auto-tuner`（config.py `PARAM_DEFS`）自愈闭环，必须同时满足：

1. **可被 `.env` 下发**：参数在消费侧有 `os.environ.get("XXX", default)` 读取（或 config dataclass 的 `from_env` 映射）。
2. **反馈键存在**：`feedback_csv` 指向 `config.py: FEEDBACK_KEYS` 中已注册的键。
3. **反馈每轮真实计算**：该键在 `report.py` 的 analyzer（`analyze_kt_baseline` / `analyze_self_evolving` 等）中每轮产出，并写入 `daily-summary-history.jsonl` 供 `determine_direction` 判别改善/恶化。

**审计结论（更新 2026-08-15）**：原登记为"待补质量指标再接入"的 15 个候选参数（K1/K3–K9 + S1/S2/S3–S6，K2 因全仓无消费方延后）**已于当日本轮全量接入闭环**——补 env 读取 + 补 8 个真实质量反馈键 + 写 `PARAM_DEFS` + 注册 `FEEDBACK_KEYS` + 部署三项目 + WSL 端到端校验通过。详见 §3 三阶段状态与 §7 落地速查。

> **二次演进（2026-08-15 分组并行调优 · 已落地）**：候选参数全量接入闭环后，auto-tuner 的「每轮只调一个参数」模型被重构为「按功能组并行调优」——一个功能涉及的多个耦合参数每轮一起调（joint_majority / single / synergy_search 三种组策略），由用户指令驱动（耦合参数须同组同调、每组定义独立调优策略）。详见 §8。

---

## 1. 候选参数清单 — 知识树（KT）构建器

| # | 参数 | 当前默认 | env 现状 | 控制语义 | 反馈现状 | 需补指标 | 优先级 | 工作量 |
|---|------|---------|---------|---------|---------|---------|-------|-------|
| K1 | `vector_threshold`（complex.py:166 硬编码 0.65） | 0.65 | ❌ 无 env | 向量桥接建边阈值（与 `same_subject` 同效应：阈值↓→边↑→孤儿率↓） | ✅ `kt_orphan_pct` 已可用 | 无（复用现有键） | P1 | 低 |
| K2 | `subject_match_threshold`（config.py:77） | 0.70 | ❌ 无 env | 科目匹配阈值，影响域划分粒度 | ✅ `kt_fragment_domains`（域碎片数，越低越好）已可用 | 无（复用现有键） | P2 | 低-中 |
| K3 | `domain_merge_threshold`（cli.py:344） | 0.60 | ❌ 无 env（CLI 参数） | 域合并余弦阈值，影响域合并/分裂 | ✅ `kt_fragment_domains` 已可用 | 无（复用现有键） | P2 | 低-中 |
| K4 | `max_candidates_per_article`（config.py:68） | 15 | ✅ `KT_MAX_CANDIDATES_PER_ARTICLE` 已存在 | 阶段1 候选数上限：影响覆盖度 vs 噪声 | ❌ 缺失 | 需"候选噪声率"或"覆盖度"指标 | P2 | 中 |
| K5 | `split_max_rounds`（config.py:70） | 2 | ✅ `KT_SPLIT_MAX_ROUNDS` 已存在 | 拆解轮数上限：影响分解深度 | ❌ 缺失 | 需"过度拆解率/欠拆解率"指标 | P3 | 中 |
| K6 | `article_max_chars`（config.py:69） | 12000 | ❌ 无 env | 文章截断长度：影响抽取完整性 | ❌ 缺失 | 需"截断丢信息率"指标 | P3 | 高 |
| K7 | `dedup_threshold_direct`（config.py:72） | 0.95 | ❌ 无 env | 直接判重阈值 | ❌ 缺失 | 需"重复 KP 率/误判率"指标 | P3 | 高 |
| K8 | `dedup_threshold_llm`（config.py:73） | 0.90 | ❌ 无 env | LLM 确认区间下界 | ❌ 缺失 | 同 K7 | P3 | 高 |
| K9 | `conflict_threshold`（config.py:75） | 0.80 | ❌ 无 env | 矛盾检测阈值 | ❌ 缺失 | 需"矛盾误报率"指标 | P3 | 高 |

> 说明：K1/K2/K3 三项的反馈指标**已经存在于报告侧**（`kt_orphan_pct` / `kt_fragment_domains`），仅需补 env 读取 + `PARAM_DEFS` 条目即可接入，是性价比最高的第一批。K4/K5 的 env 变量已就绪，只差反馈指标。K6–K9 需两端都补（env + 新指标采集）。

---

## 2. 候选参数清单 — Self-Evolving（能力飞轮）

| # | 参数 | 当前默认 | env 现状 | 控制语义 | 反馈现状 | 需补指标 | 优先级 | 工作量 |
|---|------|---------|---------|---------|---------|---------|-------|-------|
| S1 | `confidence_threshold`（kanban_reflection/config.py:23） | 0.6 | ✅ `KN_REFLECTION_CONFIDENCE` 已存在 | 反思置信度阈值：低于则打回重反思 | ⚠️ 弱（仅有 `se_applied_skill_count` 粗代理，非质量指标） | 需"反思有效采纳率"指标 | P2 | 中 |
| S2 | `max_trace_lines`（kanban_reflection/config.py:21） | 5 | ✅ `KN_REFLECTION_MAX_TRACE_LINES` 已存在 | 读取最近 N 轮 trace 喂给反思 | ⚠️ 同 S1 | 同 S1 | P3 | 中 |
| S3 | `max_components`（recombination.py:79） | 5 | ❌ 无 env | 重组组件上限 | ❌ 缺失 | 需"recombine synergy_score 均值/重组采纳率"指标 | P3 | 高 |
| S4 | `semantic_similarity_threshold`（recombination.py:82） | 0.7 | ❌ 无 env | 语义相似合并阈值 | ❌ 缺失 | 同 S3 | P3 | 高 |
| S5 | `conflict_severity_threshold`（recombination.py:81） | 0.5 | ❌ 无 env | 冲突严重度阈值 | ❌ 缺失 | 需"冲突误报率"指标 | P3 | 高 |
| S6 | `jaccard_threshold_low` / `jaccard_threshold_high`（recombination.py:88-89） | 0.3 / 0.7 | ❌ 无 env | Jaccard 快判上下界 | ❌ 缺失 | 同 S3 | P3 | 高 |

> 说明：S1/S2 的 env 变量已就绪，但 Self-Evolving 侧目前只有"产出文件数/写回 skill 数"等**计数型**指标，缺"质量型"反馈。S3–S6 既无 env 又无质量反馈，需两端都补。

---

## 3. 接入路线（分阶段）

### Phase A — 复用现有指标（✅ 已落地 2026-08-15，零新采集）
- **目标参数**：K1 `vector_threshold`、K2 `subject_match_threshold`、K3 `domain_merge_threshold`
- **动作**：
  1. 消费侧加 `os.environ.get("KT_VECTOR_EDGE_SIM_THRESHOLD" / "KT_SUBJECT_MATCH_THRESHOLD" / "KT_DOMAIN_MERGE_THRESHOLD", default)`；
  2. `config.py: PARAM_DEFS` 追加三元组，feedback 绑定到已有 `kt_orphan_pct`（K1）/ `kt_fragment_domains`（K2/K3）；
  3. `tuner.py: _parse_feedback` 中 `kt_fragment_domains` 归为 `down_better`（域碎片越少越好）。
- **收益**：立刻把知识树建边/域划分纳入自愈，无需任何新埋点。

### Phase B — 补 1–2 个新指标（✅ 已落地 2026-08-15，中等采集）
- **目标参数**：K4 `max_candidates_per_article`、K5 `split_max_rounds`、S1 `confidence_threshold`
- **动作**：在 `analyze_kt_baseline` 增加 `kt_candidate_noise_rate` / `kt_over_split_rate`；在 `analyze_self_evolving` 增加 `se_reflection_accept_rate`；三者注册进 `FEEDBACK_KEYS` 并设方向（多数为 `down_better`）。

### Phase C — 新质量采集管线（✅ 已落地 2026-08-15，未延后）
- **目标参数**：K6–K9（dedup/conflict/article 截断）、S3–S6（recombination 阈值）
- **动作**：需在 KT builder / SE 运行态埋点（如 dedup 误判采样、矛盾复核人工标签、重组 `synergy_score` 落库），再经 `ledger` 或 analyzer 汇总为反馈键。建议等业务积累足够样本后再做。

---

## 4. 新增反馈指标的采集点建议

- **`analyze_kt_baseline`**（KT 指标聚合）建议新增：`kt_candidate_noise_rate`、`kt_over_split_rate`、`kt_dedup_false_rate`、`kt_conflict_fp_rate`。
- **`analyze_self_evolving`** 建议新增：`se_reflection_accept_rate`、`se_recombine_synergy_avg`。
- 上述键统一在 `config.py: FEEDBACK_KEYS` 注册；`tuner.py: _parse_feedback` 按语义标注 `down_better` / `up_better` / `stable_ok`。
- 指标计算需保证**每日滚动窗口有 ≥ min_sample 样本**，否则该键 `None` → auto-tuner 自动跳过（与 `KN_ENABLE_CAUSAL_CHAIN=false` 同理，不会污染方向判据）。

---

## 5. 风险与约束

1. ~~**每日只调一个参数**~~ **（已演进为分组并行调优，见 §8）**：原 `select_param_to_tune` 每轮只调一个参数的限制，已重构为按功能组并行调优——每轮调全部「反馈可信且未收敛」的组，组内耦合参数同调。即便全接入也不会无节制 churn（受 `MAX_GROUPS_PER_RUN` 与每组成员安全区间约束）。
2. **闭环参数必须有因果绑定的质量反馈**，否则宁可暂缓——这是指令④确立的立场，避免产生"无反馈空转"的无效参数。
3. **布尔 / 枚举型（Feature Flag）不入 auto-tuner**：如 `kb_dedup_pgvector`、`enhanced_admission` 等，由运维在 `.env` 手动维护。
4. **env 命名一致性**：所有新增 env 变量名必须与 `PARAM_DEFS` 中 `param_name` 完全一致（auto-tuner 直接写 `.env` 的键名）。

---

## 6. 当前已接入参数速查（34 个，`config.py:PARAM_DEFS`）

- **Hindsight 路**（5）：`KN_MIN_SCORE`、`KN_MAX_RESULTS`、`KN_MAX_TEXT_LENGTH`、`KN_TEMPORAL_HALFLIFE`、`KN_TEMPORAL_FLOOR_WEIGHT`
- **SAG 路**（4）：`KN_SAG_MAX_INJECT`、`KN_SAG_SEARCH_TOP_K`、`KN_SAG_MIN_SCORE`、`KN_SAG_POINTER_THRESHOLD`
- **跨域去重**（1）：`KN_CROSS_DOMAIN_DEDUP_DEMOTE_FACTOR`
- **全局打分/重排**（3）：`KN_LAMBDA_MRR`、`KN_SCORE_SPAN_TOP3_THRESHOLD`、`KN_SCORE_SPAN_HALF_THRESHOLD`
- **因果链**（2，仅 `KN_ENABLE_CAUSAL_CHAIN=true` 时生效）：`KN_CAUSAL_BOOST_ALPHA`、`KN_CAUSAL_BOOST_CAP`
- **Skill 控制环 F-2**（2）：`SKILLOPT_MAX_PER_NIGHT`、`SKILLOPT_COOLDOWN_DAYS`
- **SAG 生产端 F-3**（1）：`DREAM_PROMOTE_THRESHOLD`
- **知识树建边**（2）：`KT_EDGE_SIM_THRESHOLD` → `kt_orphan_pct`、`KT_VECTOR_EDGE_SIM_THRESHOLD` → `kt_orphan_pct`（K1，Phase A）
- **知识树域划分**（1）：`KT_DOMAIN_MERGE_THRESHOLD` → `kt_fragment_domains`（K3，Phase A）
- **知识树候选/拆解**（2）：`KT_MAX_CANDIDATES_PER_ARTICLE` → `kt_candidate_noise_rate`（K4）、`KT_SPLIT_MAX_ROUNDS` → `kt_over_split_rate`（K5，Phase B）
- **知识树抽取质量**（4）：`KT_ARTICLE_MAX_CHARS`/`KT_DEDUP_THRESHOLD_DIRECT`/`KT_DEDUP_THRESHOLD_LLM` → `kt_low_conf_kp_rate`（K6/K7/K8）、`KT_CONFLICT_THRESHOLD` → `kt_pending_conflict_rate`（K9，Phase C）
- **Self-Evolving 反思**（2）：`KN_REFLECTION_CONFIDENCE` → `se_reflection_accept_rate`（S1）、`KN_REFLECTION_MAX_TRACE_LINES` → `se_reflection_mean_confidence`（S2，Phase B）
- **Self-Evolving 重组**（5）：`SE_RECOMBINE_MAX_COMPONENTS`/`SEMANTIC_SIM`/`CONFLICT_SEVERITY`/`JACCARD_LOW`/`JACCARD_HIGH` → `se_recombine_synergy_avg`（S3–S6，Phase C，需 `SE_ENABLE_RECOMBINE=1` 启用）

> **K2 `subject_match_threshold` 延后**：经全仓核查该字段无任何消费方（仅 `config.py` 默认值与 `default.yaml` 定义），auto-tuning 无因果反馈，违反「无无效参数」原则，故仅保留 `KT_SUBJECT_MATCH_THRESHOLD` env 映射、不进 `PARAM_DEFS`，待未来接入 subject 匹配逻辑后再补。

### 7. 落地结果速查（2026-08-15 三阶段全量完成）

| 维度 | 落地前 | 落地后 | 说明 |
|------|--------|--------|------|
| `PARAM_DEFS` 条目数 | 19（含早前 `KT_EDGE_SIM_THRESHOLD`） | **34** | 新增 15 条候选参数；K2 延后未加 |
| `FEEDBACK_KEYS` 条目数 | 20 | **28** | 新增 8 个真实质量反馈键 |
| 候选参数接入覆盖 | 1（KT 建边） | **15/15**（K2 除外） | K1/K3–K9/S1/S2/S3–S6 全部打通三要件 |
| KT 新反馈键 | `kt_orphan_pct`/`kt_fragment_domains` | +`kt_candidate_noise_rate`/`kt_over_split_rate`/`kt_low_conf_kp_rate`/`kt_pending_conflict_rate` | 均来自 DB 真实分布，非代理伪造 |
| SE 新反馈键 | 无质量型 | +`se_reflection_accept_rate`/`se_reflection_mean_confidence`/`se_recombine_synergy_avg` | synergy 需启用重组 |

**关键安全设计**（对齐指令④「无无效参数」）：
- 所有新反馈键一律写成**具体数值（真实值或 0）而非 `None`**，规避 tuner 在「全键 None」时走 `improved=True` 伪改善分支（位置游走陷阱）。
- S3–S6 重组算子原未接入 driver → 加 opt-in 钩子（默认 `SE_ENABLE_RECOMBINE` 关闭，synergy_avg=0 → auto-tuner 安全锁定，不伪优化）。
- 每轮按组并行调优（见 §8），组内各成员沿用各自安全区间与 `validate_step` 步幅校验，即便推到边界也不破坏知识树；`MAX_GROUPS_PER_RUN=0` 表示不限并行组数。

**验证**：12 文件 `py_compile` 全过；三项目 `--yes` 增量部署（kt-builder 50 / fhr 29 / self-evolving 33 文件）成功；WSL `/usr/bin/python3` 端到端校验 8 个新反馈键方向正确 + 合成数据计算全 OK（`ALL_ANALYZER_OK`）。

**自然等待项**：下次 daily 健康报告真实跑数后，观察 K1/K3–K9/S1/S2（及启用重组后的 S3–S6）是否开始被 auto-tuner 实际调优。

---

## 8. 分组并行调优（2026-08-15 二次演进 · 已落地）

> 背景：候选参数全量接入后，旧逻辑 `select_param_to_tune` 每轮只调一个参数、状态锁/恶化/冷却全是单参级。这导致**耦合参数被当独立旋钮隔天各调**（如 SAG 4 个、KT 抽取质量 4 个、hindsight 5 个），会打破耦合、收敛慢、甚至可能互相抵消。用户指令：应按参数涉及的功能**分组并行调参**，每组参数相互有关、需为每组定义各自的调优策略。

### 8.1 新模型

- `config.py:PARAM_GROUPS`（13 个功能组）→ `select_groups_to_tune` 每轮返回**全部「反馈可信且未收敛」的组**；
- 每组按 `strategy` 独立计算方向 → 一次性**批量写 `.env` + 单次重启通知 + 按组/逐成员双写 state**；
- 完全复用既有安全机制：`validate_step` 逐参步幅校验、`NO_CHANGE_LOCK(3)`、`CONSECUTIVE_DEGRADATION_SUSPEND(3)`、`COARSE_STEP_FACTOR=2` 粗→细搜索、恶化回滚、冷却期——**未引入新风险面**。

### 8.2 三种组策略

| 策略 | 组数 | 含义 |
|------|------|------|
| `joint_majority` | 9 | 多参耦合组：组内反馈键**多数投票**定「组方向」；改善→各成员沿其有利方向**同调**，恶化→整体**反向**。 |
| `single` | 3 | 单参组（xdedup / sagprod / kt_domain）：退化为原 `determine_direction` 单参逻辑（组合适）。 |
| `synergy_search` | 1 | 重组组（se_recombine）：以 `se_recombine_synergy_avg` **单一标量**驱动；synergy=0 时**安全跳过**，避免空转。 |

### 8.3 13 个功能组分派

| # | gid | label | 成员（PARAM_DEFS 名） | 策略 |
|---|-----|-------|----------------------|------|
| 1 | hindsight | Hindsight 召回路 | KN_MIN_SCORE / KN_MAX_RESULTS / KN_MAX_TEXT_LENGTH / KN_TEMPORAL_HALFLIFE / KN_TEMPORAL_FLOOR_WEIGHT | joint |
| 2 | sag | SAG 召回路 | KN_SAG_MAX_INJECT / KN_SAG_SEARCH_TOP_K / KN_SAG_MIN_SCORE / KN_SAG_POINTER_THRESHOLD | joint |
| 3 | xdedup | 跨域去重 | KN_CROSS_DOMAIN_DEDUP_DEMOTE_FACTOR | single |
| 4 | rerank | 全局打分/重排 | KN_LAMBDA_MRR / KN_SCORE_SPAN_TOP3_THRESHOLD / KN_SCORE_SPAN_HALF_THRESHOLD | joint |
| 5 | causal | 因果链提权 | KN_CAUSAL_BOOST_ALPHA / KN_CAUSAL_BOOST_CAP | joint（`enabled_when=KN_ENABLE_CAUSAL_CHAIN`） |
| 6 | skillctl | Skill 控制环 F-2 | SKILLOPT_MAX_PER_NIGHT / SKILLOPT_COOLDOWN_DAYS | joint |
| 7 | sagprod | SAG 生产端 F-3 | DREAM_PROMOTE_THRESHOLD | single |
| 8 | kt_edge | 知识树建边 | KT_EDGE_SIM_THRESHOLD / KT_VECTOR_EDGE_SIM_THRESHOLD | joint |
| 9 | kt_domain | 知识树域划分 | KT_DOMAIN_MERGE_THRESHOLD | single |
| 10 | kt_split | 知识树候选/拆解 | KT_MAX_CANDIDATES_PER_ARTICLE / KT_SPLIT_MAX_ROUNDS | joint |
| 11 | kt_quality | 知识树抽取质量 | KT_ARTICLE_MAX_CHARS / KT_DEDUP_THRESHOLD_DIRECT / KT_DEDUP_THRESHOLD_LLM / KT_CONFLICT_THRESHOLD | joint |
| 12 | se_reflect | Self-Evolving 反思 | KN_REFLECTION_CONFIDENCE / KN_REFLECTION_MAX_TRACE_LINES | joint |
| 13 | se_recombine | Self-Evolving 重组 | SE_RECOMBINE_MAX_COMPONENTS / SEMANTIC_SIM / CONFLICT_SEVERITY / JACCARD_LOW / JACCARD_HIGH | synergy_search |

### 8.4 总开关

- `GROUP_TUNING_ENABLED = True` → 走新分组并行分支；设为 `False` 即回退旧 `select_param_to_tune` 单参逻辑（兼容回滚）。
- `MAX_GROUPS_PER_RUN = 0` → `0` 表示不限并行组数，每轮调全部「可信且未收敛」的组（默认）；设正整数可限制单轮并行组数上限。

### 8.5 落地文件与验证

- **改动文件**：`config.py`（新增 `GroupSpec` dataclass + `PARAM_GROUPS` / `GROUP_BY_ID` / `PARAM_TO_GROUP` + 两个开关）；`tuner.py`（新增 ~13 个组函数：`_current_env` / `_group_improved` / `_strategy_*` / `_run_group_strategy` / `select_groups_to_tune` / `are_all_groups_converged` / `_update_state_for_group` / `_record_group_no_change` / `_append_group_log`，`main()` 新增分组分支，旧单参分支保留做兼容回退）。
- **本地集成测试**（已删临时脚本）：13 组全选中、joint 组各成员按有利方向同调、single 组走原逻辑、synergy_search 在 synergy=0 安全跳过、apply 路径真实写 `.env`+state+group 日志、degraded 组反向翻转，全部通过。
- **WSL 部署验证**：flywheel-health-report 29 文件增量部署无残留、引用一致性通过；`main --dry-run` 返回 0 —— 真实历史数据下 11 个可信功能组被选中并生成调优决策（se_recombine 因 synergy=0 未出现、单参组走原逻辑、joint 组耦合成员同调）。

**自然等待项**：下次 daily 健康报告真实跑数后，观察分组并行调优是否如预期工作（各功能组被并行调、耦合参数同调）。
