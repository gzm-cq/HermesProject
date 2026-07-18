# Cron Jobs 配置锚点

> 此文件是运行时 cron job 配置的源码锚点。所有 cron job 通过 Hermes 内置 cron
> 调度，配置存储在 `~/.hermes/cron/jobs.json`。修改 cron 配置后请同步更新此文件。
>
> 部署：`deploy/deploy.sh deploy cron-wrappers`（仅脚本，不含调度配置本身）
> 排程时间线：参见 `docs/specs/backlog/cron-scheduler-design.md` 第 3 节

---

## 任务一览

| # | job_id | 名称 | 时间 | 执行日 | 类型 | script | workdir |
|---|--------|------|------|--------|------|--------|---------|
| 1 | system-health-check | 系统健康巡检 | 02:30 | * | no_agent | `health-check-cron.sh` | `/root/.hermes/scripts` |
| 2 | daily-learn | 每日在线学习 | 03:00 | * | no_agent | `daily-learn/daily_learn.sh` | `/root/.hermes/scripts/daily-learn` |
| 3 | clustering-analysis | 聚类分析每周跑 | 04:00 | 0 | no_agent | `clustering-analysis-v3/scripts/clustering-analysis-cron.sh` | `/root/.hermes/scripts` |
| 4 | knowledge-tree-consolidate | 知识树维护每周 | 10:30 | 1 | no_agent | `knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh` | `/root/.hermes/scripts/knowledge-tree-builder` |
| 5 | kn-baseline | 知识导航评估基线 | 12:00 | * | no_agent | `knowledge-navigation-baseline.sh` | `/root/.hermes/plugins/knowledge-navigation` |
| 6 | skill-eval | Skill Eval 评估 | 12:00 | * | no_agent | `run-skill-eval.sh` | — |
| 7 | memory-cleanup-daily | memory-cleanup-daily | 13:00 | * | no_agent | `memory-cleanup/daily_dryrun.sh` | `/root/.hermes/scripts/memory-cleanup` |
| 8 | kn-router-health | 知识导航 Router 健康巡检 | 14:00 | * | no_agent | `kn-router-health-check.sh` | — |
| 9 | skillopt-nightly | skillopt-nightly-run | 15:00 | * | no_agent | `skillopt-runner/skillopt-nightly-run.sh` | `/root/.hermes/skillopt-runner` |
| 10 | kvector-maintenance | 知识树k_vector每周兜底维护 | 11:00 | 1 | no_agent | `knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh` | `/root/.hermes/scripts/knowledge-tree-builder` |
| 11 | weekly-deep-research | 每周深度研究-知识树学习 | 09:00 | 0 | agent | —（LLM prompt 驱动） | — |
| 12 | paper-submit-reminder | 论文投稿提醒-改投 | 09:00 | 一次性(2026-08-06) | agent | —（LLM prompt 驱动） | — |
| 13 | flywheel-health-report | 飞轮健康报告 | 08:00 | * | no_agent | `flywheel-health-report.sh` | — |

**说明：**
- 执行日：`*` = 每日，`1` = 周一，`1-5` = 工作日，`0` = 周日，`6` = 周六
- 时间字段以各 wrapper 脚本头部 `# 调度建议:` 注释为准（脚本注释是部署契约的源真相）
- `job_id` 列与 `flywheel-health-report.py` 中 `ACTIVE_CRON_JOBS` 标识一致，作为主键使用
- 飞轮健康报告在 CN 08:00 运行，确保 UTC 前一天数据已完整（用户前一天晚间改动可见）
- system-health-check 在 02:30 运行，daily-learn 在 03:00 运行，均避开工作时段 LLM 任务
- 周一上午排班：知识树维护(10:30) → k_vector 兜底(11:00，consolidate 之后) → 评估基线(12:00)
- agent 类型任务没有 script，由 Hermes cron 调度 LLM 代理执行 prompt
- 当前共 13 个任务：11 个 no_agent 脚本 + 2 个 agent 任务

---

## 冲突避免规则

1. **LLM 任务间隔 ≥ 1h** — 避免争抢 LiteLLM 网关
2. **skillopt 放最后**（15:00）— 该任务可能运行数小时，不影响其他任务
3. **周一上午 10:30-12:00 知识树密集排班** — 链式执行：consolidate(10:30) → k_vector 兜底(11:00) → 评估基线(12:00)
4. **凌晨批处理 02:30-04:00** — system-health-check(02:30) → daily-learn(03:00) → 聚类分析(周日04:00)
5. **周日** — 聚类分析(04:00) + 深度研究(09:00)；**周六** — 无定时任务
