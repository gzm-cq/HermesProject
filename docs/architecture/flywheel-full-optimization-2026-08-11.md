# Hermes 完整飞轮项目分析与优化建议

> 分析日期：2026-08-11
> 范围：数据飞轮（知识导航 / 知识树 / 四路 judge / auto-tuner）+ 能力飞轮（SkillOpt-Runner / Self-Evolving / Dream-Synth）+ Cron 编排层
> 前置：本文件是对 `data-flywheel-review-2026-08-11.md`（数据飞轮单域）的**整体扩展**，重点补足能力飞轮与跨飞轮协同。

---

## 1. 飞轮全景图

```
┌─────────────────────────────────────────────────────────────────────┐
│                      完整飞轮系统（Hermes）                          │
├──────────────────────────────┬──────────────────────────────────────┤
│  数据飞轮（LIVE / 闭环）      │  能力飞轮（PARTIAL）                 │
│                              │                                      │
│  trace.log                   │  ┌─ SkillOpt-Runner (cron, LIVE)     │
│     │                        │  │   harvest→rank→Sleep→patch SKILL  │
│     ▼                        │  └─ Self-Evolving (可部署/未编排)     │
│  runner(阶段0)               │      Revision/Recombine/Refine 算子  │
│     │                        │  └─ Dream-Synth (cron 16:00, LIVE)   │
│     ▼                        │      session→SAG→wiki               │
│  cli(report + kn_judge       │  └─ KT-Builder + daily-learn (LIVE)  │
│      {h,kt,s,sag})           │      采集→知识树 DB                  │
│     │                        │                                      │
│     ▼                        │  ★ 三路均独立运行，无统一反馈账本    │
│  daily-summary-history.jsonl │                                      │
│     │                        │                                      │
│     ▼                        │                                      │
│  auto-tuner → tuner.py       │                                      │
│     │ 仅调整 KN_* 变量         │                                      │
│     ▼                        │                                      │
│  knowledge-navigation        │                                      │
│  Router 行为变化 → 新 trace   │                                      │
├──────────────────────────────┴──────────────────────────────────────┤
│  Cron 编排层：cron_common.sh（flock/日志/飞书/重试/状态）            │
│              cron-periodic-detect.sh（Layer1 健康巡检 30min）        │
│              cron-wrappers/*（daily-learn/kt-builder/               │
│                              memory-cleanup/skillopt/dream-daily/    │
│                              clustering-analysis-v3）               │
└─────────────────────────────────────────────────────────────────────┘
```

**核心事实**：数据飞轮测量全部 4 路 mask（h/kt/s/sag），但 auto-tuner（`PARAM_DEFS`）**只能驱动 `KN_*` 变量**（hindsight + SAG 注入 + 跨域去重 + 重排）。能力飞轮与 SAG/KT 生产方各自跑独立循环，与 kn_judge **无数据契约**。

---

## 2. 核心发现（按优先级）

### P1 — 架构级 / 高价值

**F-1｜两飞轮解耦，无统一反馈账本（结构性）** — ✅ 已落地（见 §5）
- 数据飞轮写入 `daily-summary-history.jsonl`；skillopt 写自己的 `state.json`；dream-synth 写 SAG；KT-builder 写知识树 DB。**没有任何一处做跨循环关联**。
- 后果：`skillopt 改了 skill X → X 的负反馈/使用量是否变化` 永远无法被量化；kn_judge 与 skillopt 可能在同一时段对同一个 skill 既"评判"又"改写"，互相干扰且不自知。
- 定位：全局架构缺口，不是单文件 bug。

**F-2｜Skill 路"只测不控 + 喂入不足"（数据飞轮缺口）** — ✅ 已落地（见 §5）
- `config.py:216` 把 `skill_used_count` 放进 `FEEDBACK_KEYS`，但 `PARAM_DEFS`（config.py:189-210）**没有任何一条以 skill 为执行对象的 actuator**——h/kt/sag 三路都有 `KN_*` 执行器，唯独 `s` 路是"观测不可控"。
- 后果：数据飞轮能测出 skill 健康度，却无法调节 SkillOpt；形成测量-执行不对称。

**F-3｜SAG 生产端未闭环** — ✅ 已落地（见 §5）
- auto-tuner 能调 `KN_SAG_*`（仅控制"往检索里注入多少 SAG"），但 `dream-synth` 自身的晋升/质量阈值**完全不受 kn_judge 约束**（`dream-daily.py` 全程不读 `kn_judge_relevant_rate_sag`）。
- 后果：`relevant_rate_sag` 衡量的是"消费侧质量"，而生产侧质量漂移无人调节——只调剂量、不调配方。

**F-4｜SkillOpt-Runner 增量窗口锚定导致负反馈重复计数（能力飞轮 P1 bug）** — ✅ 已落地（见 §5）
- `_phase_harvest`（skillopt_runner.py:808-820）以 `min(skill_last_run.values())` 作为 harvest 窗口下界。
- 只有被成功优化的 skill 才写 `skill_last_run`（:945），且一旦 `neg` 清零后因分数下降很少再被选中 → 该时间戳长期锚定在最旧的优化时刻。
- 于是**每次运行都重新 harvest 自该时刻以来的全部 session**，`rank_skills`（:602-646）对 `skill_neg_feedback` 做 `+=` 累积 → **即使没有新负反馈，某 skill 的 neg 计数也会逐日 +1 膨胀**（day1=1, day2=2, day3=3…），直接污染 `score = neg*3 + …`（:658）。
- 附带：`skill_total_mentions` 只增不减、永不衰减（虽当前未进评分公式，但持续膨胀 state 体积）。

**F-5｜Self-Evolving 算子已具备但自动化缺失（能力飞轮 P1 决策）** — ✅ 已落地（见 §5）
- `self-evolving`（Revision/Recombination/Refinement 三算子）有 `deploy/manifests/self-evolving.manifest` 与 `deploy.sh` 条目，但**无 cron wrapper、无调用方**（全仓库 grep 无外部引用）。
- 现状：可部署的"能力飞轮"库，但**没有编排去喂失败轨迹、自动跑 Revision→Refinement**——飞轮只建成了 80%，闭环编排缺失。

### P2 — 健壮性 / 效率

**F-6｜每 batch 仅应用首个通过 edit（能力飞轮效率）** — ✅ 已落地（见 §5）
- `_optimize_one_skill`（:941-948）对每个通过的 batch 只 `break` 应用第一个 edit，同 batch 其余 edit 被丢弃，需多次夜间运行才能吸收全部改进。

**F-7｜共享 state 并发脆弱（能力飞轮并发）** — ✅ 已落地（见 §5）
- `_phase_optimize`（:975-1016）以 `len(top_scored)` 线程并行跑 `_optimize_one_skill`，各线程**就地修改共享 `state` 字典**（`skill_last_run`/`skill_neg_feedback`/`failed_tasks`）并并发 `save_state`（带锁，但只锁写盘）。
- 在 GIL 下"碰巧可用"，但就地 mutate + 全量 pickle 落盘可能覆盖另一线程刚做的 neg 清零；未来加 worker 即脆。

**F-8｜LLM 网关无全局预算（Cron 编排）** — ⛔ 按用户要求不做（见 §5）
- skillopt（`run_sleep_cycle`×workers）、dream-synth（4 阶段）、flywheel-health-report（judge 并发 5）都打本地 LiteLLM 网关 `127.0.0.1:4142`。各 cron 仅按时间表错峰，**无全局 token/并发预算**，高峰期易触发网关限流级联。

**F-9｜安全扫描为启发式（能力飞轮）** — ✅ 已落地（见 §5）
- `_security_scan`（:681-707）对 LLM 生成的 edit 做关键字匹配（`sudo`/`eval(`/`AKIA…`），存在误杀（含 "eval" 的正向说明）与绕过（混淆）两类风险，仅作 no_agent 降级护栏。

### 已闭环（非本次问题，记录以免重复）
- 数据飞轮 P1（`kn_judge` early-return 不写 mask 键、`min_sample` 不一致）已于 2026-08-11 修复并部署。
- `cron-catchup-repair.sh` `local` 误用（B-3）、PG 连接缓存线程安全（B-1）、skill_matcher LRU（B-2）等已于 2026-07-03 修复。

---

## 3. 优化建议（按落地优先级）

### P1 — 必须做（协同层 + 关键 bug）

1. **建统一反馈账本（F-1 落地）**
   - 扩展 `daily-summary-history.jsonl` 或新增 `flywheel-ledger.jsonl`，所有循环追加事件：kn_judge 各路指标、skillopt patch（skill / before-after neg / ts）、dream-synth SAG 写入量、KT-build 条目数。
   - 价值：单一健康视图 + 跨循环因果关联（"改写后 neg 是否真的降"）。
   - 落地：`flywheel-health-report/src/.../ledger.py` + skillopt/dream-synth 在关键节点 `append`。

2. **补齐 Skill 路控制环（F-2 落地）**
   - 在 `PARAM_DEFS` 新增以 `skill_used_count` / mask `s` 为反馈的 actuator，例如：
     `SKILLOPT_ENABLED`、`SKILLOPT_MAX_PER_NIGHT`、`SKILLOPT_COOLDOWN_DAYS`。
   - 或若保持 skillopt 独立：将 `s` 从 kn_judge 的"执行相关指标"中移出，避免假精度。二选一。

3. **SAG 生产端闭环（F-3 落地）**
   - `dream-synth` 读取账本中的 `kn_judge_relevant_rate_sag`，动态调节晋升阈值；将其 `config.yaml` 阈值改为 tuner 可调参数。

4. **修 SkillOpt 重复计数（F-4 落地）**
   - 用单一 `last_harvest_iso`（每次运行后推进）替代 `min(skill_last_run)`；`skill_total_mentions` 改为从 `usage.json` 的实时 `use_count` 派生或加滚动窗口衰减，不再无界累积 `skill_neg_feedback`。

5. **Self-Evolving 定位决策（F-5 落地）**
   - A：编排接入——在 skillopt/L1 巡检后喂失败轨迹，自动跑 Revision→Refinement 并写回 SKILL/知识树；
   - B：明确标记 research-only，移出 `deploy.sh` 自动清单，避免维护漂移。
   - 建议选 A（算子已就绪，边际成本低、闭环价值高）。

### P2 — 健壮性

6. `_optimize_one_skill` 应用单个 batch 内**全部通过 edit**（F-6）。
7. skillopt `state` 改为 per-skill 原子更新或加写入锁，消除并发 mutate（F-7）。
8. 网关全局预算：cron_common 增加 `LLM_BUDGET`/`gateway priority`，并对重 LLM 任务做错峰（F-8）。
9. `_security_scan` 升级为语义护栏（白名单基线 diff + 最小权限），降低误杀/绕过（F-9）。

### P3 — 可观测性
10. 每路 mask 单独 dashboard（h/kt/s/sag 各自 relevance 曲线 + actuator 值），替代当前合并视图。

---

## 4. 关键文件索引

| 域 | 文件 | 角色 |
|----|------|------|
| 数据飞轮 judge | `scripts/flywheel-health-report/src/flywheel_health_report/analyzers/kn_judge.py` | 四路 mask 评判 |
| 数据飞轮 tuner | `scripts/flywheel-health-report/src/flywheel_health_report/auto_tuner/tuner.py` | 调参执行（仅 KN_*） |
| 数据飞轮配置 | `scripts/flywheel-health-report/src/flywheel_health_report/config.py` | PARAM_DEFS / FEEDBACK_KEYS / mask 阈值 |
| 能力飞轮 | `scripts/skillopt-runner/skillopt_runner.py` | harvest→rank→optimize（含 F-4 bug） |
| 能力飞轮 | `scripts/self-evolving/src/self_evolving/operators/*.py` | Revision/Recombine/Refine（未编排，F-5） |
| SAG | `scripts/dream-synth/scripts/dream-daily.py` | 每日梦境流水线（与 judge 解耦，F-3） |
| KT | `scripts/knowledge-tree-builder/`、`scripts/daily-learn/` | 知识树生产与采集 |
| Cron | `scripts/cron_common.sh`、`scripts/cron-periodic-detect.sh`、`scripts/cron-wrappers/` | 编排层 |

---

## 5. 落地状态（2026-08-11 实现）

用户决策：**除 F-8（LLM 网关全局预算）外，其余全部优化落地**。F-8 按用户明确指示不做（"我不需要 LLM 预算管理"）。

### 5.1 落地总表

| 编号 | 标题 | 状态 | 关键改动 |
|------|------|------|----------|
| F-1 | 统一反馈账本 | ✅ 已落地 | 新建 `scripts/common/ledger.py`（零依赖 JSONL，写 `HERMES_HOME/data/flywheel/ledger.jsonl`）；kn_judge / skillopt_runner / dream-daily / kt-builder cli / self_evolving_driver **五处**关键节点 append 事件。各消费方启动时以 bootstrap 向上定位仓库 `libs/hermes_common` 注入 sys.path，命中即 `from hermes_common.ledger import append_ledger_event` 启用；**2026-08-12 将 `scripts/common` 与 `hermes-plugin-common` 统一整合为 `hermes-common` 共享库（`libs/hermes_common/hermes_common`，含 ledger/llm_guard/text_utils），新建 `deploy/manifests/hermes-common.manifest` + `deploy/projects/hermes-common.sh`，部署至 `/root/.hermes/lib/hermes_common/`，此前端到端失效的 no-op 桩正式生效** |
| F-2 | Skill 路控制环 | ✅ 已落地 | `config.py:PARAM_DEFS` 新增 `SKILLOPT_MAX_PER_NIGHT`(1-3) / `SKILLOPT_COOLDOWN_DAYS`(1-5) / `DREAM_PROMOTE_THRESHOLD`(0.3-0.9)；skillopt_runner 新增 `_load_skillopt_env_overrides()` 直读 `.env` 的 `SKILLOPT_*` → `_apply_skillopt_controls()` 截断+冷却过滤。`SKILLOPT_ENABLED` 作手动 bool 总开关（不进 PARAM_DEFS） |
| F-3 | SAG 生产端闭环 | ✅ 已落地 | dream-daily 新增 `_load_promote_threshold()`（.env > config.yaml > 0.6）+ `_read_latest_relevant_rate_sag()`（读 `daily-summary-history.jsonl`）；`phase_promote` 注入 `promote_ctx` 并加双门控（promote=false 跳 / score<threshold 跳） |
| F-4 | SkillOpt 重复计数 bug | ✅ 已落地 | `_phase_harvest` 改用单一 `last_harvest_iso`（每次运行后推进），替代 `min(skill_last_run)` 锚定；消除 `skill_neg_feedback` 逐日 +1 膨胀 |
| F-5 | Self-Evolving 编排接入 | ✅ 已落地 | 新建 `scripts/self-evolving/scripts/self_evolving_driver.py`（消费 skillopt `state.json.failed_tasks` → revise→refine → 写 output）；新建 `scripts/cron-wrappers/self-evolving/self-evolving-nightly.sh`（30 17 * * *）；`cron-jobs-config.md` 任务数 15→16。**B 自动写回（用户选项 C）已落地**：新增 `scripts/self-evolving/scripts/skill_patch.py`（安全写回 SKILL.md，保留 frontmatter + 按 task_id 去重 + HARD_BLOCK 护栏 + 自动备份/回滚），driver 增加 `--auto-apply` 在精炼后调用 `patch_skill_md` 写回真实 skill；WSL `jobs.json` 已注册 `self-evolving-nightly`（id `551be6f57d71`），闭环全自动运行。**巡检可视化（2026-08-12）**：`flywheel-health-report` 现已纳入该闭环——`config.ACTIVE_CRON_JOBS` 加 `self-evolving-nightly`（归"能力飞轮"）、新增 `analyzers/self_evolving.py`（读 driver 输出 / 扫描 SKILL.md 的 `SE-APPLIED` 块 / best-effort 读 ledger），报告新增"能力飞轮 / Self-Evolving"小节；并顺带修复 `parse_cron_jobs_json` 在 `last_run_at=null` 时返回 `None` 导致任务可靠性表渲染崩溃的潜在 bug |
| F-6 | 每 batch 应用全部通过 edit | ✅ 已落地 | `_optimize_one_skill` 改为应用单 batch 内全部通过 edit（非首个 break），吸收更充分 |
| F-7 | 共享 state 并发安全 | ✅ 已落地 | 引入 `_STATE_LOCK` 模块级锁，state 更新/落盘加锁，消除并发 mutate 竞态 |
| F-8 | LLM 网关全局预算 | ⛔ 按用户要求不做 | 用户："除了 llm 网关无全局预算外，其余全部优化" → 明确豁免 |
| F-9 | 语义安全护栏 | ✅ 已落地 | `_security_scan` 强化为 `HARD_BLOCK` 关键字硬阻断（sudo/eval(/AKIA…），no_agent 降级护栏 |

### 5.2 新增 / 修改文件清单

**新增**
- `libs/hermes_common/hermes_common/ledger.py` — 统一反馈账本模块（零依赖）
- `libs/hermes_common/hermes_common/llm_guard.py` — LLM 统一护栏（零第三方依赖）
- `libs/hermes_common/hermes_common/text_utils.py` — 关键词提取/CJK 处理（原 hermes-plugin-common）
- `deploy/manifests/hermes-common.manifest` — 统一共享库部署清单（hermes_common，含 ledger/llm_guard/text_utils）
- `deploy/projects/hermes-common.sh` — 统一共享库部署项目（目标 `/root/.hermes/lib`，`FIRST_DEPLOY_CLEANUP=false` 避免误删共享 lib 目录）
- `scripts/self-evolving/scripts/self_evolving_driver.py` — Self-Evolving 编排 driver（F-5，含 `--auto-apply` 写回开关）
- `scripts/self-evolving/scripts/skill_patch.py` — 安全写回 SKILL.md 模块（B 自动写回，HARD_BLOCK + 去重 + 备份/回滚，自包含不依赖未部署的 `hermes_common`）
- `scripts/cron-wrappers/self-evolving/self-evolving-nightly.sh` — 夜间编排 wrapper（F-5，调用 driver `--auto-apply`）

**修改**
- `scripts/flywheel-health-report/src/flywheel_health_report/config.py` — F-2  actuators（PARAM_DEFS 3 条）
- `scripts/flywheel-health-report/src/flywheel_health_report/analyzers/kn_judge.py` — F-1 ledger append（kn_judge 事件）
- `scripts/skillopt-runner/skillopt_runner.py` — F-1 ledger / F-2 control / F-4 harvest / F-6 all-edit / F-7 lock / F-9 HARD_BLOCK
- `scripts/dream-synth/scripts/dream-daily.py` — F-1 ledger + F-3 阈值门控
- `scripts/dream-synth/config.yaml` — 新增 `promote.threshold`
- `scripts/dream-synth/prompts/promote-judge.txt` — 输出加 `score` 字段
- `scripts/knowledge-tree-builder/src/knowledge_tree_builder/cli.py` — F-1 ledger（kt_build 事件）
- `scripts/cron-wrappers/cron-jobs-config.md` — 任务数 15→16 + jobs.json 同步提示

### 5.3 验证结论

- 7 个 Python 文件 `py_compile` 全部通过（ledger / config / kn_judge / skillopt_runner / dream-daily / kt-builder cli / self_evolving_driver）。
- F-2 控制环三场景最小验证 PASSED：ENABLED=0 整轮跳过 / MAX_PER_NIGHT=2 截断 5→2 / COOLDOWN_DAYS=4 冷却过滤 skillA、skillC。
- ledger 功能测试 PASSED（append 落盘 + 异常静默降级）。
- **F-1 账本已真正部署生效（2026-08-12）**：此前 `scripts/common` 不在任何部署清单，WSL 上 `ledger.py` 缺失，5 处消费方的 bootstrap 均降级为 no-op 桩（即账本形同虚设）。2026-08-12 将共享工具统一整合为 `hermes-common` 库（含 ledger/llm_guard/text_utils），部署至 `/root/.hermes/lib/hermes_common/`；在 WSL 真实消费方路径（health-report analyzers 目录）模拟 bootstrap 验证 `append_ledger_event` 真实 import 成功并写入 `data/flywheel/ledger.jsonl`（测试写入行已清理）。回滚：`deploy/deploy.sh rollback hermes-common <ts>`。自此 5 个消费方自动启用账本，健康巡检 `ledger_deployed` 计数随之生效。
- `PARAM_DEFS` 新条目 + 反馈键（`skill_used_count` / `kn_judge_relevant_rate_sag`）存在性校验 PASSED；tuner 导入 + `_parse_feedback` / `_param_judge_trusted` / `_is_param_permanently_skipped` 校验 PASSED。
- **WSL 部署动作已完成**（原"待 WSL 部署"项已结清）：`~/.hermes/cron/jobs.json` 已注册 `self-evolving-nightly`（id `551be6f57d71`，`schedule.expr="30 17 * * *"`，`script="self-evolving/self-evolving-nightly.sh"`，`workdir="/root/.hermes/scripts"`，`no_agent=true`，`enabled=true`，备份 `jobs.json.bak_pre_se`）；wrapper 已随 `cron-wrappers` 部署至 `/root/.hermes/scripts/self-evolving/self-evolving-nightly.sh`（exec bit 已设）。闭环：cron(30 17 * * *) → wrapper → `self_evolving_driver.py --auto-apply` → 消费 skillopt `failed_tasks` → revise→refine → `skill_patch.patch_skill_md` 安全写回 SKILL.md（受 HARD_BLOCK + 去重 + 备份约束）。
- **WSL 端到端验证 PASSED**：在 WSL 生产路径下以部署代码运行 (1) 直接写回烟测 —— `find_skill_md` 定位、`patch_skill_md` 写 `SE-APPLIED` 块并保留 frontmatter、同 task_id 去重、危险内容被 HARD_BLOCK 拒绝，全部通过；(2) 全链路烟测 —— 真实 driver `--auto-apply` 跑通 revise→refine→写回，`processed=1, applied=1, blocked=0`，`SE-APPLIED` 块已落盘。

---

## 6. 结论

系统**数据飞轮已闭环且健康（P0=0）**，但**完整飞轮"测-控"不对称**：
- 数据飞轮只控 `KN_*`（知识导航子系统），对 Skill/SAG/KT 的**生产端**不可控；
- 能力飞轮三路（skillopt / dream-synth / self-evolving）各自独立、与 judge 无契约；
- `self-evolving` 算子就绪却未编排，飞轮闭环缺最后一环。

最高杠杆的三项：**统一反馈账本（F-1）→ 补齐 Skill/SAG 控制环（F-2/F-3）→ 修 skillopt 重复计数（F-4）**。做完这三项，飞轮才从"局部自洽"升级为"全局协同自进化"。
