# Hermes 调度框架蓝图与当前 Cron Wrapper 标准化

> **文档状态：条件触发型蓝图 / 当前未实施完整统一调度框架**
> 当前生产调度仍使用 Hermes 内置 cron；已落地的是 `/root/.hermes/scripts/` 下 shell wrapper 的统一调用规范、`cron_common.sh` 公共库、日志/通知/flock/错峰执行。本文前半部分描述未来 `hermes-scheduler` 统一调度框架，不代表当前已上线实现。当前事实以 `docs/README.md` 和 `hermes_project/project-profile.md` 为准。

> 简述：**现在统一的是 wrapper 调用模式，不是统一调度框架本体**。完整 scheduler 仅在任务数量 > 8、出现真实 DAG 依赖或状态面板成为刚需时启动。

## 1. 问题根因

| 当前现象 | 后果 |
|---------|------|
| 10 个脚本中 4 个是定时任务型（`daily-learn` / `memory-cleanup` / `system-health-check` / `clustering-analysis-v3`） | 调度逻辑分散，无法统一管理 |
| 每个脚本自带调度代码 + 自定义失败处理 | 失败告警、重试、互斥语义各不一致 |
| 任务之间的**依赖与互斥靠约定**（"我记得 memory-cleanup 和 daily-learn 不能同时跑"） | 偶发撞车，无明确告警 |
| 没有**任务运行状态**统一视图 | 出问题靠人肉翻日志 |
| 没有**幂等保证** | 同一窗口内重跑可能产生脏数据 |

## 2. 设计目标

- **单一调度入口**：`hermes-scheduler` 拉起所有定时任务，脚本不再自带 cron
- **任务声明化**：用 YAML 描述"做什么 / 何时跑 / 依赖谁 / 失败怎么办"
- **依赖与互斥显式声明**：写在配置里，调度器自动 enforce
- **幂等是硬性约束**：所有任务必须能在同一窗口安全重跑
- **状态可观测**：任务状态、执行历史、失败原因存 PG，统一查询
- **可独立测试**：每个任务可被 CLI 手动触发并 mock 输入

## 3. 核心架构

```
┌────────────────────┐
│ hermes-scheduler   │  ← 单进程主调度器（APScheduler + 持久化）
│   (主循环)         │
└─────────┬──────────┘
          │ 读取任务声明
          ▼
┌────────────────────┐
│ tasks/*.yaml       │  ← 任务声明仓库（Git 管理）
│  + registry API    │
└─────────┬──────────┘
          │ 触发时
          ▼
┌────────────────────┐
│ Task Worker Pool   │  ← 独立 worker 进程（隔离 + 并发）
│   (N 个 worker)    │
└─────────┬──────────┘
          │ 写状态
          ▼
┌────────────────────┐
│ shared-postgres    │  ← 任务状态、执行历史、advisory lock
│  - scheduler_jobs  │
│  - scheduler_runs  │
│  - scheduler_locks │
└────────────────────┘
          │ 失败时
          ▼
┌────────────────────┐
│ Alert Sink         │  ← 飞书群机器人 / webhook
└────────────────────┘
```

## 4. 任务声明 Schema

```yaml
# tasks/memory-cleanup.daily.yaml
id: memory-cleanup.daily          # 唯一 ID
description: "Hindsight 去重 / 纠错 / 合并"
owner: "myHermes"

schedule:
  cron: "0 3 * * *"               # 每天凌晨 3 点
  timezone: "Asia/Shanghai"
  jitter_seconds: 60              # 抖动，避免多任务齐点触发

runtime:
  entrypoint: "python -m hermes_tasks.memory_cleanup"  # 可执行入口
  timeout_seconds: 1800           # 30 分钟硬超时
  working_dir: "/root/.hermes/scripts/memory-cleanup/"

dependencies:
  requires:                       # 前置健康检查
    - id: hindsight-index.healthy
      check: "http://hindsight:8080/healthz"
      timeout: 30
  data_ready:                     # 数据就绪
    - type: "pg_count"
      sql: "SELECT COUNT(*) FROM hindsight_facts WHERE ts > now() - interval '1 day'"
      min: 1

mutex:                            # 互斥组（同一时间只跑一个）
  - "nightly-maintenance"
  - "hindsight-write"

retry:
  max_attempts: 2
  backoff: exponential            # 2/4/8 分钟
  on_exhausted: alert_critical    # 重试耗尽 → 关键告警

idempotency:
  strategy: "pg-advisory-lock"    # 用 PG advisory lock 锁住 (job_id, time_bucket)
  bucket: "1d"                    # 同一天重跑视为同一窗口

artifacts:
  on_success: "save_to:pg"        # 结果写回 PG
  on_failure: "save_log:pg"

alerts:
  on_start: false
  on_success: false
  on_failure: alert_warning
  on_timeout: alert_critical
  on_lock_conflict: silent        # 互斥冲突静默
```

## 5. 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 调度器内核 | APScheduler（持久化到 PG） | 轻量、Python 生态、足够支撑单实例 |
| 任务隔离 | 独立 worker 进程 | 防止一个任务 OOM 拖垮全部 |
| 互斥实现 | PG advisory lock | 不需要额外组件、原子性强 |
| 幂等性 | **硬性约束**，所有任务必须声明幂等策略 | 重跑是常态，不能靠人肉判断 |
| 任务发现 | YAML 声明 + 自动注册 | 不需要在代码里 hardcode |
| 失败告警 | 飞书群机器人 webhook | 已有通道 |
| 健康检查 | HTTP 探活 / PG 数据探活 | 简单、可声明 |
| 分布式 | 单实例先跑，预留 leader 选举位 | 当前规模不需要 HA |
| 任务超时 | 调度器 SIGTERM → 升级 SIGKILL | 防止僵尸 worker |

## 6. 与现有系统的边界

| 系统 | 职责 |
|------|------|
| **部署系统** | 管"装"——把代码装到 `/root/.hermes/` |
| **统一调度器（本设计）** | 管"跑"——什么时候跑、跑什么、失败怎么办 |
| **记忆系统** | 管"存"——Hindsight 数据怎么持久化、索引 |
| **知识系统** | 管"用"——召回、注入、生成 |

四者解耦：部署系统装好代码后，统一调度器才能发现并调度它。

## 7. 迁移路径

| Phase | 工作量 | 内容 |
|-------|--------|------|
| **Phase 1** | 1 周 | 写 `hermes-scheduler` 核心（声明解析 + 调度 + worker 池 + 状态表） |
| **Phase 2** | 1 周 | 把 `system-health-check` 包装成第一个任务 |
| **Phase 3** | 2 周 | 迁移 `daily-learn` / `memory-cleanup` / `clustering-analysis-v3` |
| **Phase 4** | 1 周 | 接入告警 + 状态面板（CLI `hermes-scheduler status`） |
| **Phase 5** | 持续 | 废弃脚本内嵌 cron 配置、加新任务只需写 YAML |

## 8. 不做

- ❌ 不做分布式 leader 选举（当前规模不需要）
- ❌ 不做任务 DAG 编排（依赖是线性的，APScheduler 够用）
- ❌ 不做可视化拖拽（YAML + CLI 即可）
- ❌ 不做任务参数版本管理（用 Git 管 YAML 即可）

## 9. 验证标准

- 所有定时任务从脚本迁移到调度器
- 调度器内可看到所有任务状态、运行历史、失败原因
- 同一窗口内重跑任意任务不会产生脏数据
- 任意两个互斥任务不会同时执行
- 单任务失败不影响其他任务
- `hermes-scheduler status` 一行命令即可总览

---

## 10. 当前评估与决策（2026-06）：只落地轻量 Wrapper 标准化

### 现状确认

| 现有定时任务 | 调度方式 | 执行频率 | 已有能力 |
|-------------|----------|----------|----------|
| clustering-analysis-v3 | 独立 cron_wrapper.sh | 每周 | 日志 + 飞书通知 |
| memory-cleanup | memory-cleanup-daily.sh | 每日 | 日志 + 飞书通知 |
| daily-learn | daily_learn.sh | 每日 | 日志 + 飞书通知 |
| system-health-check | health-check-all.sh | 每日 | 日志 + 飞书通知 |

### 评估结论

| 维度 | 得分 | 说明 |
|------|------|------|
| 设计质量 | 8/10 | 架构合理，Schema 完备，前瞻性足够 |
| 当前适配度 | 4/10 | WSL systemctl 权限受限；PG 依赖过重；常驻进程开销不匹配 |
| ROI | 3/10 | 5 周工时解决 4 个任务的管理问题，收益不匹配 |

**决策**：完整 `hermes-scheduler` 归档为**条件触发型蓝图**，当前阶段只采用轻量 cron wrapper 标准化方案。

**触发条件**（满足任一即启动完整框架）：
- 定时任务数量 > 8
- 出现真正的跨任务 DAG 依赖
- 需要任务运行状态面板成为刚需

### 当前已实施范围：轻量 Cron Wrapper 标准化（2026-06）

这部分不是独立调度框架：不新增常驻 scheduler、不引入 YAML 任务注册表、不把运行历史写入 PG、不做 DAG/worker pool。它只统一 Hermes cron 触发后的脚本入口、日志、通知、flock 和错峰运行。

**1. 公共库 `scripts/cron_common.sh`**（已创建）：
- flock 防重入（`/tmp/hermes-cron-locks/<job>.lock`）
- 统一日志落盘（`/root/.hermes/logs/cron/<job>-YYYYMMDD.log`）
- 飞书通知双通道（lark-cli 优先 → webhook 降级）
- 彩色输出 + 步骤状态跟踪 + `cron_finish` 汇总

**2. 模板 `scripts/cron_job_template.sh`**（已创建）：
- 新任务复制模板，修改 `CRON_JOB_NAME` 和业务逻辑即可
- 支持 `cron_run_step`（自动跟踪）和手动 `cron_section` 两种模式

**3. 白天错峰执行（当前通过 Hermes 内置 cron 配置）**：

当前不使用系统 `crontab`；`crontab -l` 为空是正常状态，避免双重触发。Hermes no_agent `script` 统一解析到 `/root/.hermes/scripts/`。

所有任务在 08:00-16:00 工作时间内执行，LLM 密集型任务之间至少间隔 1 小时，避免争抢 LiteLLM 网关。

**周一（高峰期）：**

```
 08:00  系统巡检              ← LLM 轻
 09:00  每日在线学习           ← LLM 重（ArXiv+GitHub+知识树入库，~1.5min）
 10:00  聚类分析               ← LLM 重（~20min）
 11:00  知识树 consolidate    ← LLM 重（~20-30min）
 12:00  知识导航评估基线       ← LLM 轻
 13:00  memory-cleanup        ← LLM 重（~20-30min）
 15:00  skillopt              ← LLM 重（可能更长），放最后
```

**周二~周五：**

```
 08:00  系统巡检
 09:00  每日在线学习
 13:00  memory-cleanup
 15:00  skillopt
```

**周六：**

```
 09:00  知识树 k_vector 兜底维护
 13:00  memory-cleanup
 15:00  skillopt
```

**周日：**

```
 09:00  每周深度研究
 13:00  memory-cleanup
 15:00  skillopt
```

| 任务 | 时间（北京时间） | 执行日 | Hermes script | 状态 |
|------|------------------|--------|---------------|------|
| system-health-check | 08:00 | 工作日 1-5 | `health-check-cron.sh` | 已接入 `cron_common.sh` |
| daily-learn | 09:00 | 工作日 1-5 | `daily-learn/daily_learn.sh` | 已接入 `cron_common.sh` |
| clustering-analysis | 10:00 | 周一 | `clustering-analysis-v3/scripts/clustering-analysis-cron.sh` | 已接入 `cron_common.sh` |
| knowledge-tree consolidate | 11:00 | 周一 | `knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh` | 已接入 `cron_common.sh` |
| knowledge-navigation baseline | 12:00 | 周一 | `knowledge-navigation-baseline.sh` | 已接入 `cron_common.sh` |
| memory-cleanup | 13:00 | 每日 | `memory-cleanup/daily_dryrun.sh` | 已接入 `cron_common.sh` |
| knowledge-tree kvector | 09:00 | 周六 | `knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh` | 已接入 `cron_common.sh` |
| 每周深度研究-知识树学习 | 09:00 | 周日 | LLM agent mode（无脚本，由 Hermes cron 调度） | agent-mode，无 cron_common |
| skillopt-nightly-run | 15:00 | 每日 | `skillopt-runner/skillopt-nightly-run.sh` | 已接入 `cron_common.sh` |
| 论文投稿提醒-改投 | 09:00 | 一次性（2026-08-06） | 无脚本（LLM agent mode） | agent-mode，一次性 |

**4. 迁移状态**：
- 8/8 no_agent shell 脚本已接入 `cron_common.sh`。
- clustering-analysis 采用外层 thin wrapper 调用原完整 `cron_wrapper.sh`，避免重写旧业务逻辑。
- knowledge-navigation baseline 与 skillopt runner 的业务脚本部署在项目目录，同时在 `/root/.hermes/scripts/` 保留 Hermes cron 可执行入口。

**5. 部署**：
- `cron_common.sh` 部署到 `/root/.hermes/lib/cron_common.sh`（新增 manifest 或归入现有项目）
- 各项目 wrapper 部署到各自项目目录；Hermes cron 入口必须位于 `/root/.hermes/scripts/` 下

---

## 附：典型迁移对比

**Before**（脚本内嵌 cron）：
```python
# daily-learn/main.py
from apscheduler.schedulers.blocking import BlockingScheduler
sched = BlockingScheduler()
sched.add_job(run_daily_learn, 'cron', hour=3)
sched.start()
```

**未来完整框架 After**（任务声明，当前未实施）：
```yaml
# tasks/daily-learn.daily.yaml
id: daily-learn.daily
schedule: { cron: "0 3 * * *", timezone: "Asia/Shanghai" }
runtime: { entrypoint: "python -m hermes_tasks.daily_learn" }
```

调度器自动发现、自动跑、出问题自动告警。
