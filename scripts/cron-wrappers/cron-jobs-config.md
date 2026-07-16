# Cron Jobs 配置锚点

> 此文件是运行时 cron job 配置的源码锚点。所有 cron job 通过 Hermes 内置 cron
> 调度，配置存储在 `~/.hermes/cron/jobs.json`。修改 cron 配置后请同步更新此文件。
>
> 部署：`deploy/deploy.sh deploy cron-wrappers`（仅脚本，不含调度配置本身）
> 排程时间线：参见 `docs/specs/backlog/cron-scheduler-design.md` 第 3 节

---

## 任务一览

| # | 名称 | 时间 | 执行日 | 类型 | script | workdir |
|---|------|------|--------|------|--------|---------|
| 1 | system-health-check | 08:00 | 1-5 | no_agent | `health-check-cron.sh` | `/root/.hermes/scripts` |
| 2 | 每日在线学习 | 09:00 | 1-5 | no_agent | `daily-learn/daily_learn.sh` | `/root/.hermes/scripts/daily-learn` |
| 3 | 聚类分析每周跑 | 10:00 | 1 | no_agent | `clustering-analysis-v3/scripts/clustering-analysis-cron.sh` | `/root/.hermes/scripts` |
| 4 | 知识树维护每日 | 11:00 | 1 | no_agent | `knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh` | `/root/.hermes/scripts/knowledge-tree-builder` |
| 5 | 知识导航评估基线 | 12:00 | * | no_agent | `knowledge-navigation-baseline.sh` | `/root/.hermes/plugins/knowledge-navigation` |
| 6 | Skill Eval 评估 | 12:00 | * | no_agent | `run-skill-eval.sh` | — |
| 7 | memory-cleanup-daily | 13:00 | * | no_agent | `memory-cleanup/daily_dryrun.sh` | `/root/.hermes/scripts/memory-cleanup` |
| 8 | 知识导航 Router 健康巡检 | 14:00 | * | no_agent | `kn-router-health-check.sh` | — |
| 9 | skillopt-nightly-run | 15:00 | * | no_agent | `skillopt-runner/skillopt-nightly-run.sh` | `/root/.hermes/skillopt-runner` |
| 10 | 知识树k_vector每周兜底维护 | 09:00 | 6 | no_agent | `knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh` | `/root/.hermes/scripts/knowledge-tree-builder` |
| 11 | 每周深度研究-知识树学习 | 09:00 | 0 | agent | —（LLM prompt 驱动） | — |
| 12 | 论文投稿提醒-改投 | 09:00 | 一次性(2026-08-06) | agent | —（LLM prompt 驱动） | — |
| 13 | 飞轮健康报告 | 08:00 | * | no_agent | `flywheel-health-report.sh` | — |

**说明：**
- 执行日：`*` = 每日，`1` = 周一，`1-5` = 工作日，`0` = 周日，`6` = 周六
- 飞轮健康报告在 CN 08:00 运行，确保 UTC 前一天数据已完整（用户前一天晚间改动可见）
- 所有任务在 08:00-16:00 工作时间内运行，script 路径为相对 `~/.hermes/scripts/` 解析
- agent 类型任务没有 script，由 Hermes cron 调度 LLM 代理执行 prompt
- 当前共 13 个任务：11 个 no_agent 脚本 + 2 个 agent 任务

---

## 冲突避免规则

1. **LLM 任务间隔 ≥ 1h** — 避免争抢 LiteLLM 网关
2. **skillopt 放最后**（15:00）— 该任务可能运行数小时，不影响其他任务
3. **周一上午 08:00-12:00 密集排班** — 每小时一个，链式执行
4. **周六/周日为非工作日** — 仅保留 k_vector / 深度研究等低频任务
