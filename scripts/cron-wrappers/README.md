# cron-wrappers

> Hermes no_agent cron 定时任务的统一 shell wrapper 集合。
> 所有脚本通过 `cron_common.sh` 公共库获得 flock 防重入、统一日志、飞书通知能力。
> ⏰ **完整调度表见 [`cron-jobs-config.md`](cron-jobs-config.md)**（此 README 只概要说明）

## 文件结构

```
scripts/cron-wrappers/
├── cron-jobs-config.md                       # 全部 16 个 cron job 配置锚点（真相源）
├── auto-tuner.sh / auto-tuner.py             # 飞轮 Auto-Tuner 参数自动调优
├── backfill-scope.py                         # 召回范围回填
├── cron-boot-detect.sh / .service            # 开机自启检测
├── cron-catchup-repair.sh                    # 错峰补跑修复
├── cron-periodic-detect.sh                   # 每小时失败 job 检测与去重告警
├── deploy-cleanup-health-check.sh            # 部署清理健康检查
├── dream-daily.sh                            # 梦境合成入口
├── health-check-cron.sh                      # 系统健康巡检（system-health-check）
├── kn-router-health-check.sh                 # 知识导航 Router 健康巡检
├── knowledge-navigation-baseline.sh          # 知识导航 recall 基线
├── run-skill-eval.sh                         # Skill 评测
├── daily-learn/daily_learn.sh                # 每日在线学习
├── memory-cleanup/daily_dryrun.sh            # 记忆清理
├── self-evolving/self-evolving-nightly.sh    # 自进化夜间回写
├── skillopt-runner/skillopt-nightly-run.sh   # SkillOpt 增量优化
├── test_context.sh / test_env.sh / test_minimal.sh / test_syntax.sh  # 自检脚本
└── project-name/script-name.sh               # 新增任务请参照此模式
```

> 其余任务（flywheel-health-report、clustering-analysis、knowledge-tree 维护等）的脚本位于各自子项目，调度表统一见 `cron-jobs-config.md`。

## 公共库

- `scripts/cron_common.sh` — flock 防重入、日志、通知、状态
- `scripts/cron_job_template.sh` — 新任务模板

## 新增 cron 任务流程

1. 在 `cron-wrappers/` 下创建 `项目名/脚本名.sh`，`source` `cron_common.sh`
2. 调用 `cron_init "$0"` 获取锁，`cron_end $?` 释放锁
3. Hermes CLI：`cronjob action=create ...` 注册任务
4. 部署：`cd /mnt/d/HermesProject && bash deploy/deploy.sh deploy cron-wrappers --yes`
5. 同步更新 `cron-jobs-config.md` 中的调度表（脚本注释为部署契约源真相）
