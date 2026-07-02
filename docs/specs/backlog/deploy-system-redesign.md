# Hermes 部署系统去载设计

> **文档状态：目标态设计 / 未完全实施**  
> 当前生产部署仍使用 `deploy/deploy.sh` + `deploy/projects/*.sh` + `deploy/manifests/*.manifest`。本文描述的是未来声明化部署系统，不代表当前已上线实现。


> 把 `deploy.sh` 从"六合一 shell 脚本"拆成"单入口 CLI + 可插拔步骤"，保持 `deploy <project> --yes` 入口稳定。

## 1. 问题根因

| 当前现象 | 后果 |
|---------|------|
| 单 shell 集成 6 个职责（备份 / 回滚 / 防残留 / 技能同步 / 增量部署 / 插件自动重启 gateway） | 单点复杂度爆炸 |
| 错误处理不一致（部分步骤用 `trap`，部分没有） | 失败状态不明确，难以回滚 |
| 扩展性差（加新项目要改 shell） | 贡献门槛高 |
| 难以测试 | 每次改动靠真机验证 |
| 没有部署审计日志 | 出问题无法回溯 |
| 备份/回滚策略 hardcode | 不同项目无法差异化策略 |

## 2. 设计目标

- **稳定入口**：`hermes-deploy deploy <project> --yes` 命令语法不变
- **步骤插件化**：备份、回滚、技能同步、重启都变成可单独调用的步骤
- **配置声明化**：项目内 `.hermes-deploy.yaml` 描述"我是谁、怎么部署"
- **原子化执行**：成功 → 提交；失败 → 自动回滚到上一版本
- **状态可追溯**：每次部署的"前版本 / 后版本 / 步骤 / 耗时 / 退出码"全记录
- **可独立测试**：每个步骤可单独 dry-run

## 3. 核心架构

```
┌────────────────────────┐
│ hermes-deploy CLI      │  ← Python/Go 单可执行入口
│   (argparse/click)     │
└──────────┬─────────────┘
           │ 加载项目配置
           ▼
┌────────────────────────┐
│ .hermes-deploy.yaml    │  ← 项目级配置（写在每个项目目录里）
└──────────┬─────────────┘
           │ 状态机驱动
           ▼
┌────────────────────────────────────┐
│ Deploy State Machine               │
│   PENDING → BACKING_UP →           │
│   DEPLOYING → POST_CHECKS →        │
│   HEALTH_CHECK → COMMITTED         │
│   ↘ (任何阶段失败) → ROLLING_BACK  │
└──────────┬─────────────────────────┘
           │ 调用
           ▼
┌────────────────────────────────────┐
│ Step Plugins                       │
│   - backup.step                    │
│   - sync_files.step                │
│   - sync_skills.step               │
│   - lint.step                      │
│   - smoke_test.step                │
│   - health_check.step              │
│   - restart_service.step           │
└──────────┬─────────────────────────┘
           │ 写状态 + 审计
           ▼
┌────────────────────────┐
│ shared-postgres        │
│   deploy_runs          │
│   deploy_steps         │
│   deploy_artifacts     │
└────────────────────────┘
```

## 4. 项目级配置 Schema

```yaml
# D:\HermesProject\plugins\knowledge-navigation\.hermes-deploy.yaml
project: knowledge-navigation
type: plugin                            # plugin | script | service
version_file: ./VERSION                 # 部署前/后版本号来源

source:
  root: ./
  excludes:                             # 增量部署排除规则
    - "**/__pycache__/"
    - "**/*.pyc"
    - "tests/"

target:
  type: filesystem                      # filesystem | docker | k8s
  path: /root/.hermes/plugins/knowledge-navigation/

phases:
  pre:                                  # 部署前步骤
    - id: lint
      plugin: "hermes_deploy.steps.lint"
      args: { max_complexity: 10 }
    - id: backup
      plugin: "hermes_deploy.steps.backup"
      args:
        target: ${target.path}
        keep_versions: 5
        compress: gzip

  deploy:                               # 部署主体
    - id: sync_files
      plugin: "hermes_deploy.steps.sync_files"
      args:
        source: ${source.root}
        target: ${target.path}
        excludes: ${source.excludes}
        atomic: true                     # 临时目录 → 原子 rename
    - id: sync_skills
      plugin: "hermes_deploy.steps.sync_skills"
      args:
        source: ./skills/
        target: /root/.hermes/skills/
        filter: "knowledge-navigation.*"

  post:                                 # 部署后步骤
    - id: health_check
      plugin: "hermes_deploy.steps.health_check"
      args:
        endpoint: "http://hermes:8000/plugins/knowledge-navigation/healthz"
        timeout: 30
    - id: smoke_test
      plugin: "hermes_deploy.steps.smoke_test"
      args: { test_suite: "smoke" }
    - id: restart_service
      plugin: "hermes_deploy.steps.restart_service"
      args: { service: hermes-gateway }
      when: "type == 'plugin'"          # 仅插件需要重启

rollback:
  auto: true                            # 失败自动回滚
  preserve_versions: 5                  # 保留最近 5 个版本
  triggers:
    - on_step_failure: any
    - on_health_check_failure: true
    - on_smoke_test_failure: true

notifications:
  on_start: false
  on_success: { channel: feishu-group, mention: none }
  on_failure: { channel: feishu-group, mention: [owner] }

hooks:
  pre_deploy: ".git/hooks/pre-deploy.sh"  # 兼容旧 shell
  post_deploy: ".git/hooks/post-deploy.sh"
```

## 5. CLI 入口

```bash
# 核心命令（语法与原 deploy.sh 兼容）
hermes-deploy plan <project>          # 预览将做什么
hermes-deploy deploy <project> --yes  # 实际部署
hermes-deploy rollback <project>      # 回滚到上一版本
hermes-deploy status <project>        # 当前部署状态
hermes-deploy history <project>       # 历史部署记录
hermes-deploy diff <project>          # 对比当前 vs 目标

# 调试
hermes-deploy --dry-run <project>     # 模拟执行
hermes-deploy steps list              # 列出所有可用步骤插件
hermes-deploy steps run <step> <args> # 单独运行某步骤
```

## 6. 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 实现语言 | **Python**（用 Click/Typer 做 CLI） | 与现有项目语言一致，插件易写 |
| 步骤插件机制 | Python `entry_points`（包级插件） | 标准、可发现、无需手动注册 |
| 配置文件格式 | YAML | 注释友好、与 Python 生态对齐 |
| 备份策略 | 版本化 tar（带时间戳）+ 软链接 latest | 简单、可保留多个历史版本 |
| 原子化部署 | 写入临时目录 → 原子 rename | 失败时旧版本不受影响 |
| 状态机 | 显式状态机（不是简单的 if-else） | 失败回滚路径明确 |
| 回滚触发 | 任何步骤失败 + 健康检查失败 + smoke 失败 | 三道保险 |
| 与 git 集成 | 部署前打 tag `deploy-<timestamp>` | 与代码版本一一对应 |
| 审计日志 | PG 表（pg_admin 写）+ 飞书群消息 | 持久化 + 即时通知 |
| 并发控制 | 同项目串行、不同项目可并发 | 避免文件冲突 |
| 兼容性 | 保留 `deploy.sh` 入口，shim 到 Python CLI | 老的命令继续可用 |
| 错误处理 | 统一异常类型 + exit code | 脚本可被 cron 准确判断成功/失败 |

## 7. 步骤插件清单（v1 必做）

| 步骤 | 职责 |
|------|------|
| `lint` | Python 语法 + 复杂度检查 |
| `backup` | 备份目标目录到带时间戳的 tar |
| `sync_files` | 增量同步 + 排除规则 + 原子写入 |
| `sync_skills` | 同步技能到 `/root/.hermes/skills/` |
| `health_check` | HTTP 探活 / PG 探活 |
| `smoke_test` | 跑项目自带 smoke test |
| `restart_service` | 重启 systemd / Docker / k8s service |
| `run_migrations` | 跑数据库迁移（PG schema 版本管理） |
| `invalidate_cache` | 失效相关缓存 |

## 8. 迁移路径

| Phase | 工作量 | 内容 |
|-------|--------|------|
| **Phase 1** | 1 周 | 写 `hermes-deploy` 核心（CLI + 配置解析 + 状态机） |
| **Phase 2** | 1 周 | 迁移 4 个最常用步骤（sync_files / backup / health_check / restart_service） |
| **Phase 3** | 1 周 | 给所有 12 个项目生成 `.hermes-deploy.yaml` |
| **Phase 4** | 1 周 | 包装 deploy.sh 为 shim，调用新 CLI |
| **Phase 5** | 1 周 | 跑双轨期（deploy.sh 和 hermes-deploy 并行跑 1 周对比） |
| **Phase 6** | 0.5 周 | 废弃 deploy.sh |

## 9. 不做

- ❌ 不做远程部署（所有项目都在本机 `/root/.hermes/`）
- ❌ 不做金丝雀发布（self-evolving 才有这个需求，普通部署全量）
- ❌ 不做依赖图分析（项目之间没有运行期依赖）
- ❌ 不做可视化（CLI + 飞书通知足够）

## 10. 验证标准

- `hermes-deploy deploy <project> --yes` 行为与原 `deploy.sh` 完全一致
- 任何步骤失败自动回滚，目标目录保持部署前状态
- 每个项目一次部署耗时比原 shell 短 20% 以上
- 部署历史可在 PG 查、可在飞书群看
- 新增一个项目部署只需要写一份 `.hermes-deploy.yaml`，不改核心代码

---

## 附：与统一调度器的协作

```
部署系统（hermes-deploy）   调度系统（hermes-scheduler）
      │                              │
      │ 装好代码                     │
      ├─────────────────────────►   │
      │ 写入部署完成事件             │
      │                              │ 触发 first-run 校验
      │                              │ 注册到调度器
      │                              │ 启动定时
      ▼                              ▼
```

部署系统只管"装好"，调度器才能"跑起来"。两个系统**解耦**但**顺序依赖**。
