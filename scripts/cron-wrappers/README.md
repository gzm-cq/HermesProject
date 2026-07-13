# cron-wrappers

> Hermes no_agent cron 定时任务的统一 shell wrapper 集合。
> 所有脚本通过 `cron_common.sh` 公共库获得 flock 防重入、统一日志、飞书通知能力。
> ⏰ **完整调度表见 [`cron-jobs-config.md`](cron-jobs-config.md)**（此 README 只概要说明）

## 文件结构

```
scripts/cron-wrappers/
├── cron-jobs-config.md                               # 全部 13 个 cron job 配置锚点
├── flywheel-health-report.py                         # 飞轮健康报告生成器
├── flywheel-health-report.sh                         # 飞轮健康报告 cron wrapper
├── kn-router-health-check.sh                         # Router 健康巡检
├── health-check-cron.sh                              # 系统健康巡检
├── knowledge-navigation-baseline.sh                  # 知识导航 recall 基线
├── run-skill-eval.sh                                 # Skill 评测
├── memory-cleanup/daily_dryrun.sh                    # 记忆清理干跑
├── daily-learn/daily_learn.sh                        # 每日在线学习
├── skillopt-runner/skillopt-nightly-run.sh           # SkillOpt 增量优化
├── clustering-analysis-v3/scripts/
│   └── clustering-analysis-cron.sh                   # 聚类分析
├── knowledge-tree-builder/scripts/
│   ├── knowledge-tree-consolidate.sh                 # 知识树合并
│   └── knowledge-tree-kvector-maintenance.sh         # k_vector 兜底回填
└── project-name/script-name.sh                       # 新增任务请参照此模式
```

## 公共库

- `scripts/cron_common.sh` — flock 防重入、日志、通知、状态
- `scripts/cron_job_template.sh` — 新任务模板

## 新增 cron 任务流程

1. 在 `cron-wrappers/` 下创建 `项目名/脚本名.sh`，`source` `cron_common.sh`
2. 调用 `cron_init "$0"` 获取锁，`cron_end $?` 释放锁
3. Hermes CLI：`cronjob action=create ...` 注册任务
4. 部署：`deploy/deploy.sh deploy <项目名> --yes`
