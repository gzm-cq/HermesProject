# Cron Jobs 配置锚点

> 此文件是运行时 cron job 配置的源码锚点。所有 cron job 通过 Hermes 内置 cron
> 调度，配置存储在 `~/.hermes/cron/jobs.json`。修改 cron 配置后请同步更新此文件。
>
> 部署：`deploy/deploy.sh deploy cron-wrappers`（仅脚本，不含调度配置本身）
> 排程时间线：参见 `docs/specs/backlog/cron-scheduler-design.md` 第 3 节

---

## 任务一览

> 真相源：`~/.hermes/cron/jobs.json`。本表与 jobs.json 的 `name` 字段、`schedule.expr` 字段保持一致。共 16 个任务：14 no_agent 脚本 + 2 agent 任务。

| # | name | schedule | 类型 | script | workdir |
|---|------|----------|------|--------|---------|
| 1 | system-health-check | `0 8 * * 1-5` | no_agent | `health-check-cron.sh` | `/root/.hermes/scripts` |
| 2 | flywheel-health-report | `0 8 * * *` | no_agent | `flywheel-health-report.sh` | — |
| 3 | 每日在线学习 | `0 9 * * 1-5` | no_agent | `daily-learn/daily_learn.sh` | `/root/.hermes/scripts/daily-learn` |
| 4 | 知识树k_vector每周兜底维护 | `0 9 * * 6` | no_agent | `knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh` | `/root/.hermes/scripts/knowledge-tree-builder` |
| 5 | 每周深度研究-知识树学习 | `0 9 * * 0` | agent | —（LLM prompt 驱动） | — |
| 6 | clustering-analysis | `0 10 * * 1` | no_agent | `clustering-analysis-v3/scripts/clustering-analysis-cron.sh` | `/root/.hermes/scripts` |
| 7 | 知识树维护每日 | `0 11 * * 1` | no_agent | `knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh` | `/root/.hermes/scripts/knowledge-tree-builder` |
| 8 | 知识导航评估基线 | `0 12 * * *` | no_agent | `knowledge-navigation-baseline.sh` | `/root/.hermes/plugins/knowledge-navigation` |
| 9 | Skill Eval 评估 | `0 12 * * *` | no_agent | `run-skill-eval.sh` | — |
| 10 | memory-cleanup-daily | `0 13 * * *` | no_agent | `memory-cleanup/daily_dryrun.sh` | `/root/.hermes/scripts/memory-cleanup` |
| 11 | 知识导航 Router 健康巡检 | `0 14 * * *` | no_agent | `kn-router-health-check.sh` | — |
| 12 | skillopt-nightly-run | `0 15 * * *` | no_agent | `skillopt-runner/skillopt-nightly-run.sh` | `/root/.hermes/skillopt-runner` |
| 13 | dream-daily | `0 16 * * *` | no_agent | `dream-synth/scripts/dream-daily.sh` | `/root/.hermes/scripts/dream-synth` |
| 14 | cron-periodic-detect | `0 * * * *` | no_agent | `cron-periodic-detect.sh` | — |
| 15 | 论文投稿提醒-改投 | once 2026-08-06 09:00 | agent | —（LLM prompt 驱动） | — |
| 16 | self-evolving-nightly | `30 17 * * *` | no_agent | `self-evolving/self-evolving-nightly.sh` | `/root/.hermes/scripts/self-evolving` |

**说明：**
- `name` 列与 `~/.hermes/cron/jobs.json` 中的 `name` 字段一致，作为 cron job 主键
- `schedule` 列与 jobs.json 中 `schedule.expr`（cron 类型）或 `schedule.run_at`（once 类型）一致
- 执行日：`*` = 每日，`1` = 周一，`1-5` = 工作日，`0` = 周日，`6` = 周六
- 时间字段以各 wrapper 脚本头部 `# 调度建议:` 注释为准（脚本注释是部署契约源真相，jobs.json 是运行时真相，两者必须保持一致）
- 飞轮健康报告在 CN 08:00 运行，确保 UTC 前一天数据已完整（用户前一天晚间改动可见）
- 工作日 08:00 同时间段两个 job：system-health-check（仅 1-5）+ flywheel-health-report（每日），周末仅 flywheel-health-report
- 周一上午链式排班：clustering-analysis(10:00) → 知识树维护(11:00) → 评估基线+Skill Eval(12:00)
- agent 类型任务没有 script，由 Hermes cron 调度 LLM 代理执行 prompt
- cron-periodic-detect 每小时整点运行，独立于飞轮 14 任务，负责失败 job 检测与去重告警
- 论文投稿提醒-改投是一次性任务（2026-08-06 09:00），到期后由 Hermes 自动归档
- self-evolving-nightly(17:30) 在 skillopt(15:00) 之后运行，消费其失败轨迹（failed_tasks）做 Revision→Refinement，并自动写回对应 SKILL.md（F-5 闭环 + B 自动回写）；排在 dream-daily(16:00) 之后避开 LLM 网关高峰。部署目标/工作目录为 `/root/.hermes/scripts/self-evolving`（与 self-evolving.manifest 一致）。运行时需在 `~/.hermes/cron/jobs.json` 同步新增同名 `self-evolving-nightly` 条目，`schedule.expr="30 17 * * *"`，`script="self-evolving/self-evolving-nightly.sh"`，`workdir="/root/.hermes/scripts/self-evolving"`，`no_agent=true`。

---

## 飞轮 state 文件标识对照

`flywheel-health-report.py` 中 `ACTIVE_CRON_JOBS` 用的是 cron state 文件名（部分与 jobs.json 的 `name` 不同），对照如下：

| jobs.json name | ACTIVE_CRON_JOBS state 文件名 |
|----------------|------------------------------|
| memory-cleanup-daily | `memory-cleanup` |
| 知识导航评估基线 | `knowledge-navigation-baseline` |
| Skill Eval 评估 | `run-skill-eval` |
| skillopt-nightly-run | `skillopt-nightly-run` |
| 知识导航 Router 健康巡检 | `kn-router-health-check` |
| 每日在线学习 | `daily-learn` |
| clustering-analysis | `clustering-analysis` |
| 知识树维护每日 | `knowledge-tree-consolidate` |
| 知识树k_vector每周兜底维护 | `knowledge-tree-kvector` |

未在 ACTIVE_CRON_JOBS 中的 job（system-health-check / flywheel-health-report / dream-daily / cron-periodic-detect / 2 个 agent 任务）不写入飞轮 state 文件，由各自 wrapper 直接落盘日志。

---

## 冲突避免规则

1. **LLM 任务间隔 ≥ 1h** — 避免争抢 LiteLLM 网关
2. **skillopt 放最后**（15:00）— 该任务可能运行数小时，不影响其他任务
3. **周一上午密集排班 10:00-12:00** — 链式执行：clustering-analysis(10:00) → 知识树维护(11:00) → 评估基线+Skill Eval(12:00)
4. **08:00 同时间段并行** — system-health-check(1-5) + flywheel-health-report(*) 均为 no_agent 脚本，并行执行无冲突
5. **12:00 同时间段并行** — kn-baseline + skill-eval 均为 no_agent 脚本，并行执行无冲突
6. **周末分布** — 周六 kvector 维护(09:00)；周日 深度研究 agent(09:00)；工作日 daily-learn(09:00) 与周末任务时间相同但执行日不重叠
