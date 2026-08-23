# 数据飞轮系统全景地图（Data Flywheel System Map）

> 本文档沉淀对 HermesProject 数据飞轮的整体理解：四条飞轮闭环、五路召回（mask 四路 + CodeGraph）的不对称设计、飞轮健康巡检四层结构，以及已落地的工程修复与残余弱点。
> 配套代码：`plugins/knowledge-navigation`（消费侧召回编排）、`plugins/knowledge-tree-plugin`（生产侧写入）、`scripts/flywheel-health-report`（巡检与调参）、`scripts/cron-wrappers`（调度）。

---

## 1. 系统总览：四条并行闭环

数据飞轮并非单模块自循环，而是四条**并行、职责分离**的闭环：

| 飞轮 | 闭环路径 | 核心模块 | 状态出口 |
|------|----------|----------|----------|
| **数据飞轮** | 知识生产（post_llm_call 写入）→ 组织（知识树）→ 消费（pre_llm_call 召回）→ 闭环优化 | knowledge-tree-plugin ↔ knowledge-navigation | 消费命中率驱动再生产 |
| **Router 飞轮** | trace.log → 反馈键 → LLM Judge → Auto-Tuner → `.env` 参数 | flywheel-health-report/auto_tuner | `.env` 参数自愈 |
| **能力飞轮（SkillOpt）** | skill 使用反馈 → 文档优化（recombination/refinement） | skillopt-runner | SKILL.md 进化 |
| **能力飞轮（自我进化）** | 对话 session → 梦境反思(dream-synth) → 写入 SAG + 归档 Wiki → SAG 召回 → 回流下一轮对话 | dream-synth ↔ knowledge-navigation(SAG召回) ↔ axiom-wiki | SAG 知识密度驱动再生产 |

**关键解耦**：召回（`pre_llm_call`）与写入（`post_llm_call`）分属两个插件，隔着 LLM 协作成闭环——导航插件只"读"，知识树插件只"写"。

---

## 2. 五路召回架构（Router mask = {h, kt, s, sag} + CodeGraph 符号级）

### 2.1 mask 四路不对称设计

| 路 | 含义 | 传输/存储 | 超时 | 熔断 | 是否走打分链 |
|----|------|-----------|------|------|--------------|
| **h** | Hindsight 经验记忆 RAG | HTTP `:9177` | `timeout_seconds`(30s) | ✅ 3/90s | 是（走完整合并流水线） |
| **kt** | Knowledge Tree 知识树 | PG + pgvector | `kt_timeout_seconds`(默认10s) | ✅ **本次新增** 3/90s | 是 |
| **s** | Skill 本地技能 | 本地 SKILL.md | `skill_timeout_seconds`(默认60s) | ✅ 独立 | **否（拼好文本块直接注入）** |
| **sag** | SAG 结构化文档 | HTTP `:4173` | `sag_search_timeout` | ✅ 3/90s | 是（仅注入指针/全文阈值裁剪） |

> 不对称是刻意的：s 路绕过打分链是因为 skill 是"操作手册"而非"记忆片段"，不应被 MMR/降权裁剪；h/sag 有熔断而 kt 无熔断的历史原因已消除（见 §4 修复）。

**第 5 路（CodeGraph 符号级）**：不在 mask 决策内，由 `_is_code_query()` 代码关键词独立触发（`_do_codegraph_recall`，subprocess 只读查询 `codegraph query`，timeout 5s），返回符号级结果（文件/行号/签名），并入 `kept` 参与去重/度量，注入 `<knowledge source="codegraph">`，绝不阻塞主链路。

### 2.2 编排与合并流水线

`pre_llm_call`（`core/hooks/router.py`）主线：

```
三层门控 → LLM Router 决策 mask → mask 四路并行 submit（共享线程池）
   → 各自绝对截止时间收割（_left(key)）
   → CodeGraph 符号级召回（代码 query 关键词触发，第 5 路，timeout 5s 不阻塞）
   → 合并流水线 → XML 注入
```

合并流水线（顺序，详见 `filtering.py`）：
1. 标记排除 `exclude_marked`
2. rerank 分提取
3. 命中次数 boost
4. 因果链 boost（`_causal_boost`）
5. `[标记:已解决]` 降权 ×0.3
6. 时间衰减（半衰期 30 天，乘法融合）× MMR（λ=0.55，字符集 Jaccard）
7. 分数跨度裁剪（`score_span_*`阈值）
8. 跨域去重（3-gram，降权 ×0.5）
9. kt 候选对齐入列 → SAG 候选合并
10. turn-to-turn 降权 ×0.1 + 文本去重 + token 测量
11. XML 组装（`_assemble_xml_output`）

### 2.3 Router 工程质量亮点

- 6 级 JSON 解析降级 + 置信度 <0.3 兜底
- `FALLBACK_MASK` 硬约束：`h/kt/s` 强制开、`sag` 恒关
- 熔断程序化：失败时 `record_failure` 累计达阈值即开启，冷却后自动恢复

---

## 3. 飞轮健康巡检四层

| 层 | 职责 | 探针/任务 | 频率 |
|----|------|-----------|------|
| **Layer0** 基础设施自愈 | boot 检测、周期检测、自动拉起 | boot-detect / periodic-detect | 启动 + 周期 |
| **Layer1** 系统存活巡检 | 10 项探针（hermes/bifrost/hindsight/sag/postgres/mcp/dashboard/moonbridge/orphan/memory_files） | health-check-all | 08:00 |
| **Layer2** 飞轮质量巡检 | 11 analyzers + LLM Judge 抽样打相关性分 → Auto-Tuner 单变量爬山调参 | flywheel-health-report | 08:00 |
| **Layer3** 修复后追赶 | 修复导致的缺口补跑 | catchup-repair | 事件触发 |

**统一出口**：所有告警经飞书群发出，`no-news-good-news` 原则（无异常不打扰）。

### Auto-Tuner 三态机
`pending_restart → locked`（no_change≥3）→ suspended`（consecutive_degradation≥3）`，含震荡检测、收敛锁定、样本不足哨兵，`suspended` 触发回滚 known-good 参数（见 §4 D3 修复）。

---

## 4. 已落地的工程修复

### 4.1 kt 生产 bug 修复（P0，B-0 前置）
**问题**：`router.py` 在导入期拷贝 `HAS_KNOWLEDGE_TREE`（恒为 `False` 的快照），导致 `_do_kt_recall` 在生产环境**永远 `return []`**——kt 被判定活跃、被提交线程池、记日志，但结果恒空。测试因 `patch.object(..., "HAS_KNOWLEDGE_TREE", True)` 遮蔽了该 bug。
**修复**：以 `_ensure_kt_imported()` 返回值作为唯一真值源，移除 router 内的陈旧快照；测试 patch 同步改为 `_ensure_kt_imported`。

### 4.2 kt 路接入熔断器（B）
复用 `core/circuit_breaker.py` 的 `CircuitBreaker`，新增 `_kt_cb` 实例与 `kt_circuit_*` API，对齐 h/sag 的 3/90s 模式。数据/契约类异常（TypeError/ValueError/KeyError）不计熔断，对齐 SAG 的 4xx 处理。

### 4.3 召回并行与连坐修复（A）
**根因**（非"无超时"，而是）：① skill 用相对超时、在 hs 之后取时，造成端到端延迟翻倍（t0+60s）；② 线程池仅 4 worker，Hindsight 重试导致僵尸线程占满池子，连累 kt/sag 排不上队却消耗自身截止时间；③ 池饱和时 SAG 根本没被调度，却被误记熔断失败。
**修复**：
- 四路统一以提交时刻为锚点的**绝对截止时间**（`_deadline`/`_left(key)`），消除慢路累加；
- 线程池 4 → **16** worker，缓解饥饿；
- Hindsight recall 重试收敛：`hindsight_recall_max_retries`（默认 0，单次超时即放弃）；
- `_was_scheduled()` 区分"服务超时"与"池饱和未调度"，后者**不计入 SAG 熔断**；
- kt 收割超时显式 `kt_circuit_record_failure("timeout")`。

### 4.4 s 路字符上限与 token 消耗观测（C）
s 路此前绕过打分链、不受任何预算约束（仅每 skill 硬截断 4000 字符）。修复：
- 单 skill 上限改为可配置 `skill_max_chars_per_skill`（替代硬编码 4000），仍保留截断以防单 skill 文本过大；
- skill token 计入 `token_usage` 监控日志（此前完全不可见）。
> ⚠️ 原"启用 `enable_token_budget` 按比例截断 s 路"的逻辑已于 **2026-08-10 下午**按用户决策**移除**（见 §4.8）：router 化（内容注入上下文）已发生的 token 成本无法靠截断省下，截断只会牺牲内容完整性。

### 4.5 SAG XML 缺闭合标签修复（P1）
`router.py:838` 的 `<knowledge source="sag" ...` 开标签缺 `>`，产出非法 XML。已补 `>`。

### 4.6 技术债清理（D1/D2/D3，由后台 agent 执行）
- **D1**：删除与 `cron-wrappers` 100% 重叠的死代码 `flywheel-orchestrator`（先 git 快照再删）。
- **D2**：飞书 `chat_id` 去硬编码，统一从 `FEISHU_CHAT_ID` 环境变量读取，缺失则跳过通知不崩溃。
- **D3**：`auto_tuner` 进入 `suspended` 时**回滚 `.env` 到 known-good（`initial_value`）**，而非仅停调参。

---

## 4.7 上线验证结果（2026-08-10）

全部改动经 `deploy/deploy.sh` 部署至 `/root/.hermes/`，网关重启后实测：

| 验证项 | 方法 | 结果 |
|--------|------|------|
| B-0：kt 召回恒空 | 生产环境直调 `_do_kt_recall("知识树 召回 飞轮 架构")` | **5 条 / 3.33s**（修复前恒为 0） |
| `_ensure_kt_imported()` | 生产环境求值 | `True`（修复前 `HAS_KNOWLEDGE_TREE` 快照恒 `False`） |
| kt / sag 熔断器 | `kt_circuit_is_open()` / `sag_circuit_is_open()` | 均为 `False`（闭合，正常） |
| 召回线程池 | 部署副本 `cache.py` | `max_workers=16`（原 4） |
| Hindsight 召回重试 | 部署副本 `hindsight.py` | 全路径统一 `_max_retries`，默认 0 |
| 回归测试 | `pytest tests`（knowledge-navigation） | **244 passed** |
| 回归测试 | flywheel-health-report / dream-synth | **48 passed / 100 passed** |

### 修复过程中新发现的两个缺陷

**（1）`hindsight.py` 重试守卫不一致（已修）**
循环上界已切到 `hindsight_recall_max_retries`，但内部三处 `attempt < CONFIG.max_retries` 守卫仍用旧字段。
后果：超时/连接失败时会**多睡 1 秒**再落到文案错误的"429 限流"分支。已统一为局部 `_max_retries`，
并补 2 个回归用例（`test_recall_rate_limit_no_retry_by_default` / `test_recall_timeout_no_sleep_by_default`）锁死"默认不 sleep"。

**（2）token 预算总开关未启用，自优化器空转（已决策：移除预算，仅保留观测）**

> 决策原文（2026-08-10 下午）：*"不做预算，router化再多也要花，今天下午去掉token预算控制，只记录实际消耗"*

`.env` 曾配置 `KN_TOKEN_BUDGET_TOTAL=4000`、`KT_RATIO=0.4`、`HINDSIGHT_RATIO=0.4`、`SKILL_RATIO=0.20`，
但 **`KN_ENABLE_TOKEN_BUDGET` 从未设置**（默认 `False`），整套预算机制始终关闭。连带后果：

- `auto_tuner` 白名单含 `KN_TOKEN_BUDGET_{KT,SKILL,HINDSIGHT}_RATIO` 三个参数，`auto-tuner-state.json` 显示它确实在调
  （`token_budget` 于 2026-08-06 二次上调、`token_budget_hindsight_ratio` 于 2026-08-03 上调），**但这些调整对生产零影响**；
- 调参后的质量波动纯属噪声，被记入 `degradation_count` / `direction_history`，**污染了 Router 飞轮方向判据**。

核心判断：router 化（内容注入上下文）的 token 成本已经发生，预算截断**省不下钱、只牺牲内容完整性**。
故最终决策不是"开启预算"，而是**彻底移除预算控制逻辑，只保留实际 token 消耗的记录（观测）**。
落地细节见 §4.8。

---

## 4.8 token 预算控制移除（2026-08-10 下午）

**决策**：移除 token 预算控制逻辑，只保留实际 token 消耗的记录（观测）。理由：router 化（内容注入上下文）的 token 成本已经发生，预算截断省不下钱、只牺牲内容完整性；原总开关 `KN_ENABLE_TOKEN_BUDGET` 从未启用，预算相关调参纯属空转并污染方向判据。

**消费侧 `knowledge-navigation`（主线）**
- `router.py`：`_dedup_and_budget`→`_dedup_and_measure`，删除整个 `if CONFIG.enable_token_budget` 截断分支；`token_usage` 日志改为无条件输出，新增 `total_tokens` 字段，移除 `budget_enabled`
- `config.py`：删除 `enable_token_budget`/`token_budget_total`/`token_budget_{hindsight,kt,skill}_ratio` 5 字段 + env_map + ENV 解析（旧环境变量静默忽略）
- `filtering.py`：删除死代码 `apply_token_budget`/`_trim_to_budget`/`_sort_by_score`/`_result_text`/`_results_total_tokens`（保留 `estimate_tokens`，仍被 router 与测试使用）
- 测试：`test_post_process_recall.py`（`TestDedupAndMeasure`）、`test_filtering.py`（删 `TestApplyTokenBudget` 整类 7 用例）、`test_hooks.py`（`TestTokenUsageEvent`，断言改 `total_tokens`）

**巡检侧 `flywheel-health-report`（后台 agent）**
- `parsers.py`：事件名 `token_budget`→`token_usage`
- `analyzers/token_budget.py`→`token_usage.py`：纯消耗观测（删预算耗尽语义、补 sag 路、加占比）；`total_tokens` 缺失时按四路求和兜底（兼容新旧插件）
- `config.py`：删 `TH.token_budget_exhaust_pct`、`REC_TH.token_exhaust_ratio`、4 个 `KN_TOKEN_BUDGET_*`（PARAM_DEFS 19→15）
- `tuner.py`：白名单移出 3 个 budget ratio；连带删除 `_RATIO_TRIO_PARAMS` 及 `normalize_ratio_trio`/`_get_ratio_trio_bounds`/`_rebalance_ratio_trio_after_rollback`/`main()` 10.5 段死代码（约 200 行）
- `recommendations.py`/`report.py`/`runner.py`：同步更新
- `report.py`：7 天趋势表「Token耗尽%」列→「Token消耗avg | Skill占比%」两列；`daily-summary` 用 `token_total_avg` + `token_skill_share_pct`
- `token_exhaust_pct` 从 `FEEDBACK_KEYS` 及 `KN_MAX_TEXT_LENGTH`/`KN_SAG_POINTER_THRESHOLD` 反馈键彻底摘除（永为 0 的同向票=污染，已消除）
- 测试：`test_parsers`/`test_recommendations`/`test_suspended_rollback` 更新；新增 `test_analyzers/test_token_usage.py`（8 用例）；`test_suspended_rollback` 替代被测参数 `KN_SAG_SEARCH_TOP_K`

## 4.9 移除后上线验证（2026-08-10 下午）

| 验证项 | 方法 | 结果 |
|--------|------|------|
| 消费侧回归 | `pytest plugins/knowledge-navigation/tests` | **237 passed**（原 244 − 7 删减预算用例） |
| 巡检侧回归 | `pytest scripts/flywheel-health-report/tests`（排除生产 live 测试） | **52 passed**（基线 48 + 4 新增 token_usage 用例） |
| 生产 trace 实测（只读） | 37 条 `token_usage` 事件 | avg：hs 807 / sag 157 / kt 0 / skill 3111 / total 4075；占比 skill 76.3% / hs 19.8% / sag 3.9% / kt 0%；累计 150,781 tokens；issues=[] |
| 契约兼容 | analyzer 对旧事件（无 `total_tokens`）四路求和兜底 | 插件改动上线前后均正常出数（新事件带 `total_tokens`，旧事件自动求和） |

> 注：部署前生产 trace 事件仍带 `budget_enabled` 且无 `total_tokens`（旧插件未上线）；新插件上线后事件将带 `total_tokens`、不再带 `budget_enabled`，analyzer 两种格式均可解析。

---

## 5. 残余弱点（待后续迭代）

| 优先级 | 弱点 | 说明 |
|--------|------|------|
| P2 | s 路按"整体字符串"截断 | `skill_max_chars_per_skill` 仍是按单 skill 整体文本硬截断；彻底方案需把 skill 拆为 per-skill 候选块再裁剪，但**不再走 token 预算**——仅做长度上限保护 |
| P2 | kt 无跨域去重兜底 | kt 与 hindsight 跨域去重用 embedding 时依赖 PG 可用性 |
| P3 | `_jaccard` 字符集中文不可靠 | 与跨域去重 3-gram 标准不一致，中文相似度判据有偏 |
| P3 | 串行分支（active_count<2）无超时 | 单路场景可无限阻塞 |
| P3 | alert 仍部分单点 | D2 已去硬编码，但通知渠道仍依赖单一飞书群 |
| P3 | 梦境反思 LLM 消耗未观测 | dream-synth 批处理不走 `token_usage` 事件，自我进化成本未纳入统一消耗统计（可选补，详见 §6.4） |

> **已闭环项（2026-08-10 下午）**：原 P0「`KN_ENABLE_TOKEN_BUDGET` 未启用 + 自优化器空转污染方向判据」与原两条 P2「`filtering.apply_token_budget` 死代码 / s 路 token 预算截断」已随 §4.8 的预算移除决策一并消除——预算控制逻辑整体下线与死代码清理。

---

## 6. 能力飞轮之二：SAG召回 ↔ 梦境反思 ↔ Wiki 自我进化闭环

> 与 §1「能力飞轮（SkillOpt）」并列的第二条能力飞轮：它是**知识蒸馏 / 自我反思闭环**，把原始对话沉淀为结构化知识（SAG）与公理（Wiki），再经 SAG 召回回流到下一轮对话。2026-08-10 用户拍板的「去 token 预算、只观测」决策覆盖了本飞轮的 SAG 召回消耗（见 §6.3）。

### 6.1 闭环路径

```
对话 session（state.db 新增）
    │
    ▼
梦境反思 dream-synth（每日 16:00 串行四阶段）
    ├─ Phase1 synthesize：session → LLM 过滤(score≥3) → LLM 提炼 → 写入 SAG（tag: dream-synth）
    ├─ Phase2 patterns  ：近期反思笔记 → LLM 跨 session 主题发现 → 写入 SAG（tag: dream-pattern）
    ├─ Phase3 promote   ：未归档反思 → LLM 判归档价值 → 写入 axiom-wiki（按 category 落盘 .md）
    └─ Phase4 feishu     ：top-5 未归档反思 → 推送飞书
    │
    ▼
SAG 召回（knowledge-navigation sag mask，共用 SAG_SOURCE_ID）
    │   召回 dream-synth / dream-pattern 沉淀的反思笔记、方案、协议、报告
    ▼
LLM 输出（注入 SAG 结构化知识）→ 下一轮对话更精准
    ▲                                              │
    └────────  Wiki 高价值公理也作为 SAG 召回的"方案/协议/报告"来源 ┘
```

### 6.2 三个关键组件

| 组件 | 项目/路径 | 角色 |
|------|-----------|------|
| **SAG 召回** | `plugins/knowledge-navigation/.../hooks/router.py` `_do_sag_recall` | 四路召回之一（mask=`sag`）；REST `/search` 命中后按 `sag_min_score`/`sag_pointer_threshold`/`sag_max_inject` 裁剪注入 |
| **梦境反思** | `scripts/dream-synth/` | 每日批处理：session → 反思笔记 → SAG + Wiki；`SAG_SOURCE_ID="89a9a04d..."` 与知识导航插件共用同一 SAG 源 |
| **Wiki** | `axiom-wiki`（`CFG["wiki"]["base_path"]`，物理路径 + MCP） | 高价值反思的归档终点，按 category 落盘 `.md` |

### 6.3 与 token 预算移除决策的关系（2026-08-10）

- **SAG 召回 token 消耗：已被覆盖**。本飞轮 SAG 路走消费侧 `_dedup_and_measure`，四路 `sag_tokens` 已写入 `token_usage` 事件（生产实测 sag=157 avg，占比 3.9%）。无需额外改造。
- **梦境反思 / Wiki：无 token 预算逻辑**。grep 确认 `scripts/dream-synth/` 不含 `token_budget`/`enable_token_budget`；其 LLM 调用仅用 `max_tokens` 生成上限（非注入预算）。本次「去预算」决策对它无改动。
- **SAG 召回的内容 cap ≠ token 预算**：`sag_max_inject`（注入条数上限）、`sag_pointer_threshold`（超长转指针）、`sag_min_score`（分数门槛）是**结构/质量裁剪**，与「token 预算截断」性质不同，保留不动。

### 6.4 观测缺口（待补）

- **梦境反思自身 LLM 消耗未计入 token_usage**：dream-synth 是批处理流水线，其 LLM token 开销不走 per-query 召回记账，目前不写 `token_usage` 事件。若需把「自我进化成本」纳入统一消耗观测，可在 `call_llm` 包装层补 token 统计（独立于本次去预算改造，可选）。

---

## 7. 部署与回滚

唯一入口 `deploy/deploy.sh`（WSL 内运行，仓库挂载 `/mnt/d/HermesProject`，目标 `/root/.hermes/`）：
```
wsl -e bash -c "cd /mnt/d/HermesProject && bash deploy/deploy.sh deploy knowledge-navigation"
```
- 仅 `knowledge-navigation` 与 `knowledge-tree-plugin` 持有 `PROJECT_SVC="hermes-gateway.service"`，部署后自动重启网关。
- 回滚：保留 `deploy/deploy.sh` 的版本化；`auto_tuner` 的 `.env` 回滚依赖 `suspended` 状态触发（D3）。
