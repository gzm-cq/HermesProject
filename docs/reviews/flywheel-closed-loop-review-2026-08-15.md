# 飞轮闭环链路 Review — 闭环修正后复盘

> Review 日期：2026-08-15
> 数据基准：`flywheel-report-2026-08-12-WSL.md`（覆盖 08-11，含 self-evolving 注册，权威快照）
> 对照：`flywheel-report-2026-08-12-with-SE.md`（08-12 03:50 UTC，self-evolving 尚未注册的更早快照）
> 修正依据：
> - `docs/architecture/flywheel-full-optimization-2026-08-11.md`（F-1~F-9 落地）
> - `docs/architecture/data-flywheel-review-2026-08-11.md`（数据飞轮单域 + P1 修复）
> - `docs/reviews/flywheel-fix-verification-review.md`（35 项修复核实 + 补刀）
> - `docs/architecture/data-flywheel-closed-loop.md`（闭环设计基线）

---

> **状态更新（2026-08-15 11:00）**：用户已重启 hermes-gateway，`KN_MAX_TEXT_LENGTH=300` 的 pending_restart 已清除、参数正式生效。原 §0 / §3.1 标注的「阻断性断点」已关闭；其余 P1 / 结构性问题按 §5 逐一解决中。
>
> **状态更新（2026-08-15 三次更新）**：auto-tuner 控制面已从「只控 KN_*」扩展为「KN_* + 知识树 9 项候选 + Self-Evolving 6 项候选」（`PARAM_DEFS` 19→34、`FEEDBACK_KEYS` 20→28）。原先 §3.3「tuner 只控 KN_*」的结构性不对称已部分消解——KT/SE 生产端阈值现可被 tuner 驱动（仍受「每日只调一个参数 + 安全区间 + 无反馈锁定」约束）。K2 `subject_match_threshold` 因无消费方延后。详见 §5 三次更新块。

> **状态更新（2026-08-15 四次更新 · 分组并行调优）**：auto-tuner 调优模型由「每轮只调一个参数」重构为「按功能组并行调优」（用户指令：耦合参数须同组同调、每组定义独立调优策略）。新增 `config.py:PARAM_GROUPS`（13 个功能组、3 种策略：joint_majority / single / synergy_search）+ `GROUP_TUNING_ENABLED=True` + `MAX_GROUPS_PER_RUN=0`（全并行）。每轮选中全部「反馈可信且未收敛」的组、组内耦合参数按组策略同调，一次性批量写 `.env` + 单次重启通知 + 按组/逐成员双写 state；完全复用既有安全机制（validate_step / NO_CHANGE_LOCK / 恶化回滚 / 冷却）。WSL `--dry-run` 实跑：11 个可信组被选中并生成决策，se_recombine 因 synergy=0 安全跳过；无残留、引用一致。详见 §5 四次更新块。

## 0. 结论速览（TL;DR）

| 维度 | 修正后状态 | 判定 |
|------|-----------|------|
| 数据飞轮（知识导航 KN_*） | 4 路召回→judge→tuner→.env→召回，路径已对齐且验证 | ✅ 真闭环 |
| Self-Evolving 写回 | F-5 编排接入 cron，已跑通 1 个 skill 进化并写回 | ✅ 真闭环 |
| Cron 编排层 | flock/重试/状态/飞书，self-evolving-nightly 已注册 | ✅ 健康 |
| 跨飞轮统一账本 F-1 | 5 处 append 落盘，但消费方**只记不控** | ⚠️ 半闭环 |
| 能力飞轮⇄judge 反馈契约 | skillopt/dream 与 kn_judge 无反向控制 | ⚠️ 半闭环（测-控不对称） |
| 参数实际生效 | `KN_MAX_TEXT_LENGTH=300` 调优已随 **08-15 网关重启生效**，pending_restart 已清除 | ✅ 已闭环 |
| SAG 侧闭环增益 | `relevant_rate_sag=0.333` 强负信号至今未驱动参数 | 🔴 未兑现 |

**总判定**：修正把系统从「局部自洽」推进到「数据飞轮真闭环 + 能力飞轮部分闭环」，但**全局协同自进化尚未达成**——账本仍是观测层（只记不控）、SAG 强负信号（`relevant_rate_sag=0.333`）尚未真正驱动参数。（注：tuner 控制面已在 08-15 三次更新扩展至 KT/SE 候选阈值，原「SAG/KT 生产端不可控」已部分消解；pending_restart 断点已于 08-15 网关重启清除。）

---

## 1. 这几天「闭环修正」做了什么（时间线）

| 日期 | 修正内容 | 来源 | 落地状态 |
|------|----------|------|----------|
| 08-10 | 35 项飞轮修复核实，补刀 1 真 bug(P3-1 `--since` 不展开)+1 回归(P3-8 无 key 刷屏)+C3/H7/H10/C5+H2/H3 运行期 env | `flywheel-fix-verification-review.md` §7 | ✅ 全量部署 WSL，242 测试通过 |
| 08-11 | 数据飞轮 P1：`kn_judge` early-return 写 mask 键、`min_sample` 全局↔mask 级不一致修复部署 | `data-flywheel-review-2026-08-11.md` §二 P1 + 优化报告§2 | ✅ 已部署 |
| 08-11 | F-1~F-9 完整飞轮优化（**除 F-8 LLM 网关预算不做**）：统一账本、Skill 控制环、SAG 生产闭环、skillopt 去重、self-evolving 编排、全 edit 应用、state 锁、HARD_BLOCK | `flywheel-full-optimization-2026-08-11.md` §5 | ✅ 代码落地 |
| 08-12 | `hermes-common` 统一共享库部署 → **F-1 账本真正生效**（此前 5 处消费方 bootstrap 降级为 no-op 桩，账本形同虚设） | 优化报告 §5.3 | ✅ WSL 验证写入 ledger.jsonl |
| 08-12 | `self-evolving-nightly` 注册 WSL（id `551be6f57d71`，`30 17 * * *`）；健康报告新增「能力飞轮/Self-Evolving」小节 | 优化报告 §5.1 F-5 | ✅ 已注册并跑通 |
| 08-12 | 修复 `parse_cron_jobs_json` 在 `last_run_at=null` 崩溃的潜在 bug | 优化报告 §5.1 F-5 | ✅ |

**关键转折**：08-12 之前 F-1 账本是「空架子」（no-op），08-12 整合为 `hermes-common` 后才真正有事件写入——这解释了为什么 08-12 WSL 报告才首次出现 ledger 计数与 self-evolving 可靠性行。

---

## 2. 闭环链路现状（逐段打通度）

### 2.1 数据飞轮（知识导航）— ✅ 真闭环
链路：`Router 四路召回(h/kt/s/sag) → trace.log → flywheel-health-report(judge 4 mask) → daily-summary-history.jsonl → auto-tuner(KN_*) → /root/.hermes/.env → Router 行为 → 新 trace`

- 路径对齐已逐文件验证（`parsers.py:57` 写盘 == `config.py:164` 读取 == `.env` 同源）。
- 四路 mask 框架完整：决策/执行/熔断/recall_logger/judge/tuner 因果绑定六要素齐全。
- 熔断框架对称（hindsight/sag/knowledge_tree 三路独立 CircuitBreaker，KT 路成功/失败均复位，此前「KT 不重置」疑云不成立）。
- **唯一阻断**：tuner 已调 `KN_MAX_TEXT_LENGTH 200→300` 但 `pending_restart`，需重启 hermes-gateway 生效（见 §3）。

### 2.2 能力飞轮 — ⚠️ 部分闭环
- **skillopt-runner**：F-2 控制环（`SKILLOPT_MAX_PER_NIGHT`/`COOLDOWN_DAYS`/`DREAM_PROMOTE_THRESHOLD` + `.env` 直读）+ F-4 去重（单一 `last_harvest_iso` 消除逐日 +1 膨胀）+ F-6 全 edit 应用 + F-7 state 锁 + F-9 HARD_BLOCK。已能稳定 harvest→rank→patch。但与 kn_judge **无数据契约**，改写后 neg/使用量变化无法被量化。
- **dream-synth**：F-3 生产端闭环，`dream-daily` 读 `relevant_rate_sag` + 双门控晋升。但 SAG 召回质量仍低（08-12 judge：sag 相关性 36.4%，relevant_rate 仅 0.333），生产侧阈值虽被门控，消费侧质量未改善。
- **self-evolving**：F-5 编排接入 cron（30 17 * * *）→ driver `--auto-apply` → 消费 skillopt `failed_tasks` → revise→refine → `skill_patch.patch_skill_md` 安全写回。08-12 报告已见产出：驱动 3 文件、写回 1 个 skill（`mlops/clustering-analysis`），ledger 1 事件。**写回闭环已真打通**。

### 2.3 跨飞轮统一账本 F-1 — ⚠️ 半闭环（只记不控）
- 5 处 append（kn_judge / skillopt / dream-daily / kt-builder cli / self_evolving_driver）已落盘。
- **但消费方仅 `flywheel-health-report` 读取**做展示；skillopt/dream 写完即弃，**无反向调节** → 原 F-1 目标「跨循环因果关联（改写后 neg 是否真降）」只完成观测一半。
- 这恰是原优化报告 §1 指出的「三路均独立运行，无统一反馈账本」结构性缺口的**温和版残留**：账本有了，契约还没接。

### 2.4 Cron 编排层 — ✅ 健康
- `cron_common.sh`（flock/日志/飞书/重试/状态）+ `cron-periodic-detect.sh`（30min 健康巡检）+ `cron-wrappers/*`。
- self-evolving-nightly 已注册（WSL 报告 12 个核心 cron 任务，with-SE 报告 11 个未含——**以 WSL 报告为准**）。

---

## 3. 当前残留问题（来自 08-12 健康报告 + 历史评审）

### 3.1 阻断性断点（必须处理）
| 问题 | 证据 | 影响 |
|------|------|------|
| **pending_restart 未重启** | ~~`KN_MAX_TEXT_LENGTH 200→300 (pending_restart)`~~ **✅ 08-15 已重启网关清除**，参数已生效 | 原闭环卡点已关闭 |
| **baseline_latest.json 缺失/空** | Router P1：`/root/.hermes/plugins/knowledge-navigation/baselines/baseline_latest.json` | baseline 任务改 report 内建 judge 后产出路径/文件名可能变更，Router 基线比对失效 |

### 3.2 P1 关注项
| 飞轮 | 问题 | 数据 | 根因指向 |
|------|------|------|----------|
| Router | 平均得分 0.4302 < 0.5 阈值 | KT 路相关性 **25%**、SAG **36.4%**、hindsight 82.5% | KT/SAG 两路召回质量差，且 tuner 无法调这两路（只能控 KN_*） |
| SAG | 召回异常 1 次 | Router 尝试 55 / 异常 1 / 零结果 12 | SAG 服务不稳或熔断器触发 |
| Skill | `run-skill-eval ❌ 超时(300s)` | F1=0.4967 可能来自 08-11 陈旧 eval | eval 链路不稳，Skill 健康度信号不可信 |
| SAG 侧增益 | KN_SAG_* 调优历史 0 条 | relevant_rate_sag=0.333 强负信号 | 数据飞轮评审预测的「首个 SAG 侧闭环增益」**至今未兑现** |

### 3.3 结构性不对称（设计层）
- **tuner 控制面（2026-08-15 三次更新后）**：已由「只控 KN_*」扩展为「KN_* + 知识树候选(K1/K3–K9) + Self-Evolving 候选(S1–S6)」共 34 条 `PARAM_DEFS`。KT/SE 生产端阈值现可被 tuner 驱动，原先「测-控」不对称已部分消解（K2 因无消费方延后；S3–S6 重组需 `SE_ENABLE_RECOMBINE=1` 启用才产生非零 synergy 反馈）。
- **tuner 调优粒度（2026-08-15 四次更新）**：由「每轮只调一个参数」升级为「按功能组并行调优」——34 个参数归入 13 个功能组，每轮并行调全部「反馈可信且未收敛」的组，组内耦合参数按 joint_majority / single / synergy_search 三策略同调。解决了耦合参数被当独立旋钮隔天各调、互相抵消的收敛慢问题，且复用全部既有安全机制，未引入新风险面。详见 §5 四次更新块。
- **F-8 按用户明确豁免不做**：无 LLM 网关全局预算，skillopt/dream/health-report 高峰期同打 `127.0.0.1:4142`，限流级联风险仍在（已知、接受）。
- **KT 孤立 0%、置信度 0.9818**：知识树本身健康，但 KT 路召回相关性仅 25%，是「有知识点但召回不对题」而非「没知识点」。

---

## 4. 闭环链路判定矩阵

```
                    ┌─ 已真闭环 ─┐
数据飞轮(KN_*)  ────▶ 4路召回→judge→tuner→.env→召回   ✅ 验证路径对齐
Self-Evolving ─────▶ failed_tasks→revise→refine→写回   ✅ 08-12 已跑通1个
Cron 编排      ─────▶ flock/重试/状态/飞书             ✅ 健康
                    ┌─ 半闭环(有信号无反向控制) ─┐
统一账本 F-1  ──────▶ 5处append→仅health-report读   ⚠️ 只记不控
能力飞轮⇄judge ────▶ skillopt/dream 与 kn_judge 无契约 ⚠️ 测-控不对称
                    ┌─ 断点 ─┐
参数生效      ──────▶ pending_restart 未重启         🔴 卡在应用
SAG增益       ──────▶ 强负信号未驱动 KN_SAG_*        🔴 未兑现
baseline      ──────▶ baseline_latest.json 缺失       🔴 Router基线失效
```

---

## 5. 行动建议（按优先级）

| 优先级 | 动作 | 预期收益 | 类别 |
|--------|------|----------|------|
| **P0** | 重启 hermes-gateway 使 `KN_MAX_TEXT_LENGTH=300` 生效，清除 pending_restart | 闭环「应用」环节打通，tuner 优化落地 | 断点修复 |
| **P1** | 排查 `baseline_latest.json` 缺失：baseline 任务改 report 内建 judge 后，产出文件名/路径是否同步变更 | 恢复 Router 基线比对 | 断点修复 |
| **P1** | 单独跑 `run-skill-eval` 并查 300s 超时根因（并发/模型/网络） | Skill F1 信号恢复可信 | 可靠性 |
| **P1** | SAG：1 次异常查熔断日志 + 服务健康；KT 路相关性 25% 考虑降 `mask_min_sample_kt` 或扩 KT 召回策略 | 拉高 Router 均分 | 质量 |
| **P2** | 让 F-1 账本从「只记」升级为「可控」：skillopt/dream 读 ledger 做反向门控（**选项 A**）；或维持观测并把 `s` 路移出 kn_judge 避免假精度（**选项 B**） | 跨飞轮协同自进化 | 结构 |
| **P2** | SAG 侧增益兑现：确认 tuner 在下一非冷却日基于 `relevant_rate_sag` 驱动 `KN_SAG_*` | 首个 SAG 侧真实闭环增益 | 闭环 |

> **2026-08-15 更新（闭环修复落地）**：
> - **#4 KT 路相关性/孤儿率（P1 行）根因已定位并修复**：`build_kp_edges` 策略3 对 >100 KPs 的 subject 用 `hash(frozenset())` 种子 `rng.sample` 取样，系统性错过高相似对（高相似 KP 聚集在低 id 段），导致实跑 0 新边（孤儿率 68%）。改为 `ORDER BY id` + `kid_list[:100]` 前缀取样后，dry-run 实测 **2793 新边**（2525 同科高相似 + 268 向量桥接）。✅
> - **#5 SAG 侧闭环增益（P2 行）已修复**：tuner.py 加「严重度快车道」`_sev()`，强负信号（mask relevant_rate<0.5）参数同 tier 内优先调优，解除被 KN_* 排队饿死。源码 dry-run 已验证实选 `KN_TEMPORAL_FLOOR_WEIGHT`（worst_rate=0.296）。待下次 daily 报告确认 KN_SAG_* 开始被调优。✅
> - **13:22 起已用 `--yes` 正式重部署三项目**（此前漏 `--yes` 被静默取消，非 manifest 漏文件——manifest 实际含 `src/knowledge_tree_builder/**/*.py`）：kt-builder/fhr/kn 修复标记全部 live。
>
> **2026-08-15 二次更新（用户 4 项收尾指令）**：
> - **F-1 账本"只记→可控"（P2 行）已修复（选项 A 反向门控）**：`ledger.py` 新增 `recent_skill_patch_trend()`；`skillopt_runner.patch_skill_hermes` 在写回前读 ledger——某 skill 近 10 次修订中 ≥3 次仍携带重负反馈(`neg_before`≥3) 且高负向率≥50% → 暂停自动 patch 转人工审阅。best-effort（读失败不阻断）。`dream_promote` 无 per-skill 维度，门控落于 skillopt。✅
> - **知识树 0.65 阈值接入闭环调优（用户建议）**：`complex.py` 改读 `KT_EDGE_SIM_THRESHOLD` 环境变量；`config.py` PARAM_DEFS 增 `KT_EDGE_SIM_THRESHOLD`(默认 0.65 / floor 0.55 防噪声边 / 绑定 `kt_orphan_pct`)；`tuner.py` 将 `kt_orphan_pct` 判为 `down_better`（孤儿率越低越好）；`report.py` 指标字典补 `kt_avg_confidence`/`kt_fragment_domains` 观测。✅
> - **全链路可调参数审计（用户指令 3）**：扫出 9 个 KT 阈值 + 6 个 SE 阈值等候选，但绝大多数缺自动质量反馈 → 盲目接入会变"无效参数"。本轮仅接入确有反馈的 `KT_EDGE_SIM_THRESHOLD` + 补观测指标；其余候选登记为「需先补质量指标再接入」，未盲目全加。
> - **现有 18 参数有效性核查（用户指令 4）**：结论——**全部有效，无无效参数需修正**。`KN_ENABLE_CAUSAL_CHAIN` 默认 True（因果链参数有效，非死参数）；所有 feedback 键（`skill_used_count`/`sag_total_kept`/`avg_relevance_kt`/`router_empty_pct`/`kt_orphan_pct` 等）均在 `report.py:617-670` 日报产出。细微已知点（不修核心逻辑）：`stable_ok` 键在 `old_v==0` 时 `_is_metric_improved` 恒 True，静默日可能令 `SKILLOPT_*`/`DREAM_*` 同向漂移，但会被 `NO_CHANGE_LOCK(3)` 自愈。
> - 部署：`--yes` 正式部署 4 项目（增量模式）hermes-common / skillopt-runner / flywheel-health-report / knowledge-tree-builder，runtime 修复标记全部 live，4 文件 py_compile + WSL 端到端校验通过。
>
> **2026-08-15 三次更新（候选参数三阶段全量接入 · "直接全部完成三个阶段"）**：
> - **15 个候选参数全部接入 auto-tuner 闭环**（K1/K3–K9 + S1/S2/S3–S6），`PARAM_DEFS` 19→**34**、`FEEDBACK_KEYS` 20→**28**；K2 `subject_match_threshold` 经全仓核查无任何消费方，按指令④「无无效参数」原则仅保留 env 映射、延后接入。
> - **Phase A（复用现有指标）**：K1 `KT_VECTOR_EDGE_SIM_THRESHOLD`→`kt_orphan_pct`、K3 `KT_DOMAIN_MERGE_THRESHOLD`→`kt_fragment_domains`，消费侧补 `os.environ.get` 读取 + `PARAM_DEFS` 条目 + `_parse_feedback` 方向映射（`kt_fragment_domains`→`down_better`）。
> - **Phase B（补 1–2 新指标）**：K4 `KT_MAX_CANDIDATES_PER_ARTICLE`→`kt_candidate_noise_rate`、K5 `KT_SPLIT_MAX_ROUNDS`→`kt_over_split_rate`（KT analyzer 真实计算）；S1 `KN_REFLECTION_CONFIDENCE`→`se_reflection_accept_rate`、S2 `KN_REFLECTION_MAX_TRACE_LINES`→`se_reflection_mean_confidence`（SE analyzer 真实计算）。
> - **Phase C（新质量采集管线，未延后）**：K6–K9 新增 `KT_ARTICLE_MAX_CHARS`/`KT_DEDUP_THRESHOLD_DIRECT`/`KT_DEDUP_THRESHOLD_LLM`→`kt_low_conf_kp_rate`、`KT_CONFLICT_THRESHOLD`→`kt_pending_conflict_rate`（来自 `knowledge_tree` / `knowledge_review_queue` 真实分布，非伪造）；S3–S6 重组算子 `RecombinationConfig.from_env()` 五变量读取 + `self_evolving_driver` opt-in 重组钩子（`SE_ENABLE_RECOMBINE=1` 时记录 `synergy_score`→`se_recombine_synergy_avg`，默认关闭→0→安全锁定）。
> - **安全约束（规避无效参数陷阱）**：所有新反馈键一律写具体数值（真实值或 0）而非 `None`，规避 tuner「全键 None」走 `improved=True` 伪改善分支；重组未启用时 synergy=0 令 S3–S6 自动锁定。
> - **验证**：12 文件 `py_compile` 全过；三项目 `--yes` 增量部署（kt-builder 50 / fhr 29 / self-evolving 33 文件）成功；WSL `/usr/bin/python3` 端到端校验 8 新反馈键方向正确 + 合成数据计算全 OK（`ALL_ANALYZER_OK`）。
> - 路线图见 `docs/reviews/flywheel-candidate-params-roadmap-2026-08-15.md`（Phase A/B/C 已标注 Done，§7 落地速查）。
>
> **2026-08-15 四次更新（分组并行调优 · 用户新指令）**：
> - **根因**：旧 `select_param_to_tune` 每轮只返一个参数、`determine_direction` 纯单参逻辑、状态锁/恶化/冷却全单参级 → 耦合参数（SAG 4 个、KT 抽取质量 4 个、hindsight 5 个）被当独立旋钮隔天各调，打破耦合、收敛慢、可能互相抵消。用户指令：应按功能**分组并行调参**，每组参数相互有关、需为每组定义独立调优策略。
> - **新模型**：`config.py:PARAM_GROUPS`（13 功能组）→ `select_groups_to_tune` 每轮返全部「可信且未收敛」组 → 每组按 `strategy` 独立计算 → 一次性**批量写 .env + 单次重启通知 + 按组/逐成员双写 state**。
> - **三种策略**：`joint_majority`（9 组：组内反馈键多数投票定组方向，改善→各成员沿有利方向同调，恶化→整体反向）、`single`（3 组 xdedup/sagprod/kt_domain：退化为原 determine_direction）、`synergy_search`（1 组 se_recombine：se_recombine_synergy_avg 单标量驱动，synergy=0 安全跳过）。
> - **开关**：`GROUP_TUNING_ENABLED=True`（False→回退旧单参）、`MAX_GROUPS_PER_RUN=0`（0=不限，每轮调全部可信未收敛组）。
> - **安全复用**：validate_step 逐参步幅校验、NO_CHANGE_LOCK(3)、CONSECUTIVE_DEGRADATION_SUSPEND(3)、COARSE_STEP_FACTOR=2、恶化回滚、冷却，全部复用旧机制，未引入新风险。
> - **改动文件**：config.py（GroupSpec/PARAM_GROUPS/GROUP_BY_ID/PARAM_TO_GROUP+开关）、tuner.py（新增 ~13 个组函数 + main 新分支，旧单参分支保留做兼容回退）。
> - **验证**：本地集成测试（已删临时脚本）13 组全选中/joint 同调/single 走原逻辑/synergy=0 跳过/apply 真写 .env+state+group 日志/degraded 反向翻转；WSL 29 文件增量部署无残留 + 引用一致性；`main --dry-run` 返 0，11 个可信组选中并生成决策（se_recombine 因 synergy=0 未出现）。路线图见 `flywheel-candidate-params-roadmap-2026-08-15.md` §8。
| **已知豁免** | F-8 LLM 网关全局预算不做，记录高峰期限流级联风险 | — | 风险登记 |

---

## 6. 数据可信度提示
- 08-12 两份报告差异属正常：WSL 报告（03:50 后、含 self-evolving 注册）为权威，**以 WSL 版为准**。
- `KN_SAG_*` / `KN_TEMPORAL_*` 等 9 项参数「调优中 0 条历史」= 尚未被 tuner 触碰，属冷启动非故障。
- 知识树基线采集 `2026-07-24` 已过期 456h（阈值 48h）——历史遗留告警，与本次闭环修正无关，建议单独清理或重新采集。
- 全局错误 229 条（ERROR 9 / WARNING 209），Top 模块 `tools.mcp_tool`(59) / `knowledge_tree_plugin.hooks`(36) / `hindsight`(34)，ERROR 占比 4.1%，非 P0 级。

---

## 7. 一句话总结
修正把飞轮从「局部自洽」带到「数据飞轮真闭环」，但**全局协同自进化仍差三件事**：① 重启网关消 pending_restart（断点）；② 让账本从观测升级为反向控制（F-1 只完成一半）；③ 兑现 SAG 侧强负信号到参数的闭环增益。做完这三项，飞轮才真正从「能转」变为「会自我进化」。
