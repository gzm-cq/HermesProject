# Hermes-Kit — 知识飞轮增强包 架构方案

> 方案1：最小产品版，作为 Hermes 插件/增强包，单命令安装
> 版本: 1.2 | 更新: 2026-07-25 | 核验: 实际系统状态（jobs.json + hermes cron API）

## 一、产品定位

Hermes-Kit 是 Hermes Agent 的**知识飞轮增强包**，把四路召回、聚类分析、Skill 优化、飞轮监控、知识树维护、记忆清理、梦境合成、自进化研究打包成一个可一键安装的模块。不修改 Hermes 核心源码，不改 Hermes 的 plugin/hook 体系，只做**配置化组装**。

```
安装前：Hermes（基础 agent）
安装后：Hermes + Hermes-Kit（完整知识飞轮 + 能力飞轮系统）
```

## 二、架构总览

```
┌────────────────────────────────────────────────────────────────┐
│                    hermes-kit CLI                              │
│  hermes-kit install   hermes-kit status   hermes-kit update    │
└────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────────┐
│  kit-config    │   │  kit-deploy   │   │  kit-cron         │
│  统一配置管理   │   │  编排部署     │   │  统一 cron 调度    │
│  ~/.hermes-kit/│   │  插件/脚本/  │   │  14个任务统一管理   │
│  config.yaml   │   │  systemd     │   │  状态/告警/日志    │
└───────────────┘   └───────┬───────┘   └───────────────────┘
                            │
   ┌────────┬──────┬────────┼────────┬────────┬────────┬───────┐
   ▼        ▼      ▼        ▼        ▼        ▼        ▼       ▼
┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐
│四路  ││知识树││记忆  ││聚类  ││Skill ││梦境  ││自进化││在线  │
│召回  ││维护  ││清理  ││分析  ││优化  ││合成  ││研究  ││学习  │
│(插件)││(cron)││(cron)││(cron)││(cron)││(cron)││(脚本)││(cron)│
└──┬───┘└──────┘└──────┘└──────┘└──────┘└──────┘└──────┘└──────┘
   │
   ▼
┌───────────────────────────────────────────────────────────────┐
│                   飞轮健康报告（每日 08:00）                    │
│  12 cron 状态 + 三路召回统计 + 基线趋势 + P0/P1 告警          │
└───────────────────────────────────────────────────────────────┘
```

## 三、组件清单

### 3.1 四路召回（知识导航插件）

| 组件 | 来源 | 改动 |
|------|------|------|
| cron-common 公共库 | `scripts/cron_common.sh` + `scripts/cron_job_template.sh` | 无改动，所有 wrapper 依赖 |
| knowledge-navigation 插件 | `plugins/knowledge-navigation/` | 无改动，原样部署 |
| knowledge-tree-plugin | `plugins/knowledge-tree-plugin/` | 无改动，原样部署 |
| SAG MCP 服务 | Docker 容器 | 无改动 |
| Hindsight daemon | systemd 服务 | 无改动 |
| Router 配置 | 知识导航插件内置 | 无改动 |

**安装动作**：`deploy/deploy.sh deploy cron-common --yes` → `deploy/deploy.sh deploy knowledge-navigation --yes` → `deploy/deploy.sh deploy knowledge-tree-plugin --yes` → 重启 gateway → 确认 SAG/Hindsight 运行

> cron-common 必须最先部署，所有 cron wrapper 脚本（[health-check-cron.sh](file:///d:/HermesProject/scripts/cron-wrappers/health-check-cron.sh)、[daily_learn.sh](file:///d:/HermesProject/scripts/cron-wrappers/daily-learn/daily_learn.sh) 等）都依赖 `/root/.hermes/lib/cron_common.sh`。

### 3.2 聚类分析

| 组件 | 来源 | 改动 |
|------|------|------|
| clustering-analysis-v3 脚本 | `scripts/clustering-analysis-v3/` | 无改动 |
| cron wrapper | `scripts/cron-wrappers/clustering-analysis-v3/scripts/clustering-analysis-cron.sh` | 无改动 |

**安装动作**：`deploy/deploy.sh deploy clustering-analysis-v3 --yes` → 创建 cron（周一 10:00）

### 3.3 Skill 优化

| 组件 | 来源 | 改动 |
|------|------|------|
| skillopt-runner 脚本 | `scripts/skillopt-runner/` | 无改动 |
| skillopt-sleep 库 | `scripts/skillopt-sleep/` | 无改动 |
| run-skill-eval 脚本 | `scripts/cron-wrappers/run-skill-eval.sh` | 无改动 |

**安装动作**：`deploy/deploy.sh deploy skillopt-runner --yes` → `deploy/deploy.sh deploy skillopt-sleep --yes` → 创建 cron（每天 15:00）

### 3.4 飞轮健康报告

| 组件 | 来源 | 改动 |
|------|------|------|
| 飞轮健康报告 wrapper | `scripts/cron-wrappers/flywheel-health-report.sh` | 无改动，内部调用 flywheel-health-report.py |
| Router 健康巡检 wrapper | `scripts/cron-wrappers/kn-router-health-check.sh` | 无改动 |
| 系统巡检 health-check-all.py | `scripts/system-health-check/health-check-all.py` | 无改动 |
| 巡检入口 health-check-cron.sh | cron-wrappers 统一部署（`scripts/cron-wrappers/`） | 无改动 |
| 知识导航基线 wrapper | `scripts/cron-wrappers/knowledge-navigation-baseline.sh` | 无改动 |

**安装动作**：`deploy/deploy.sh deploy system-health-check --yes` → cron-wrappers 统一部署各 wrapper → 创建 3 个 cron：
- 系统巡检 → 工作日 08:00（`health-check-cron.sh`）
- 知识导航基线 → 每天 12:00（`knowledge-navigation-baseline.sh`）
- Router 巡检 → 每天 14:00（`kn-router-health-check.sh`）

### 3.5 知识树维护

| 组件 | 来源 | 改动 |
|------|------|------|
| knowledge-tree-builder 脚本 | `scripts/knowledge-tree-builder/` | 无改动 |
| consolidate 维护脚本 | `scripts/cron-wrappers/knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh` | 无改动 |
| k-vector 维护脚本 | `scripts/cron-wrappers/knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh` | 无改动 |

**安装动作**：`deploy/deploy.sh deploy knowledge-tree-builder --yes` → 创建 cron（consolidate 周一 11:00, kvector 周六 09:00）

### 3.6 记忆清理

| 组件 | 来源 | 改动 |
|------|------|------|
| memory-cleanup 脚本 | `scripts/memory-cleanup/` | 无改动 |
| daily_dryrun.sh wrapper | `scripts/cron-wrappers/memory-cleanup/daily_dryrun.sh` | 无改动 |

**安装动作**：`deploy/deploy.sh deploy memory-cleanup --yes` → 创建 cron（每天 13:00）

### 3.7 梦境合成

| 组件 | 来源 | 改动 |
|------|------|------|
| dream-synth 脚本 | `scripts/dream-synth/` | 无改动 |
| dream-daily.sh wrapper | `scripts/dream-synth/scripts/dream-daily.sh` | 无改动 |

**安装动作**：`deploy/deploy.sh deploy dream-synth --yes` → 创建 cron（每天 16:00）

### 3.8 自进化研究

| 组件 | 来源 | 改动 |
|------|------|------|
| self-evolving 脚本 | `scripts/self-evolving/` | 无改动 |
| SE-Agent skill | self-evolving 内置 skills/ | 无改动 |

**安装动作**：`deploy/deploy.sh deploy self-evolving --yes`（无 cron，按需手动运行或由 agent 调度）

### 3.9 每日在线学习

| 组件 | 来源 | 改动 |
|------|------|------|
| daily-learn 脚本 | `scripts/cron-wrappers/daily-learn/` | 无改动 |
| daily_learn.sh wrapper | `scripts/cron-wrappers/daily-learn/daily_learn.sh` | 无改动 |

**安装动作**：`deploy/deploy.sh deploy daily-learn --yes` → 创建 cron（工作日 09:00）

### 3.10 评估工具（可选）

| 组件 | 来源 | 改动 |
|------|------|------|
| recall-eval | `scripts/recall-eval/` | 无改动 |
| p0-benchmark | `scripts/p0-benchmark/` | 无改动 |

**安装动作**：`deploy/deploy.sh deploy recall-eval --yes`（无 cron，手动触发评估）

## 四、统一配置

```yaml
# ~/.hermes-kit/config.yaml
kit:
  version: "1.1.0"

  # 四路召回
  recall:
    hindsight: true
    knowledge_tree: true
    sag: true
    skill_matcher: true
    router:
      enabled: true
      model: s-deepseek-v4-flash
      timeout: 15
      min_score: 0.6

  # 知识树维护
  knowledge_tree:
    enabled: true
    consolidate_schedule: "0 11 * * 1"       # 每周一 11:00
    kvector_schedule: "0 9 * * 6"             # 每周六 09:00
    consolidate:
      min_sub_nodes: 5
      max_split_size: 50

  # 记忆清理
  memory_cleanup:
    enabled: true
    schedule: "0 13 * * *"                    # 每天 13:00
    dry_run: true
    apply: false

  # 聚类分析
  clustering:
    enabled: true
    schedule: "0 10 * * 1"                    # 每周一 10:00
    min_cluster_size: 5
    silhouette_threshold: 0.05

  # Skill 优化
  skill_optimization:
    enabled: true
    schedule: "0 15 * * *"                    # 每天 15:00
    top_k: 5
    skill_eval_schedule: "0 12 * * *"         # 每天 12:00

  # 梦境合成
  dream_synth:
    enabled: true
    schedule: "0 16 * * *"                    # 每天 16:00

  # 自进化研究
  self_evolving:
    enabled: true
    # 无 cron，按需手动运行或由 agent 调度

  # 每日在线学习
  daily_learn:
    enabled: true
    schedule: "0 9 * * 1-5"                   # 工作日 09:00

  # 飞轮报告
  health_report:
    enabled: true
    schedule: "0 8 * * *"                     # 每天 08:00
    push_to: feishu

  # Router 飞轮
  router_health:
    enabled: true
    kn_baseline_schedule: "0 12 * * *"        # 每天 12:00
    router_check_schedule: "0 14 * * *"      # 每天 14:00

  # 系统巡检
  system_health:
    enabled: true
    schedule: "0 8 * * 1-5"                   # 工作日 08:00

  # 通知
  notification:
    channel: feishu
    home_chat_id: "${HERMES_KIT_HOME_CHAT_ID:-}"   # 从 .env 注入，不硬编码到模板
```

> **chat_id 来源**：默认模板留空占位，由 `templates/.env.append` 中的 `HERMES_KIT_HOME_CHAT_ID` 注入。install.sh 检测到为空时提示用户填写，不阻断安装（仅跳过飞书通知步骤）。

## 五、生命周期管理

### 5.1 三命令模式

```
hermes-kit install     # 首次安装：部署 + 创建配置 + 建 cron
hermes-kit upgrade     # 升级：更新代码 + 合并配置 + 重建 cron
hermes-kit config      # 仅修改配置，不动代码
```

### 5.2 配置归拢

当前配置散落在三处，归拢后：

| 配置内容 | 归拢前 | 归拢后 |
|---------|--------|--------|
| 组件开关/阈值 | `config/default.yaml` | **不变**，已有 |
| cron 调度时间 | `install.sh` CRON_JOBS 数组硬编码 | **移入** `config/default.yaml` |
| 非密钥环境变量 | `templates/.env.append` | **移入** `config/default.yaml` |
| API 密钥/密码 | `.env` | **保持**，12-factor 原则 |
| 子项目内部阈值 | 各子项目自己的 config | **保持**，子项目自治 |

### 5.3 配置合并逻辑（upgrade 核心）

```
旧 config.yaml         新模板 default.yaml          合并结果
  clustering:               clustering:               clustering:
    min_cluster_size: 5       min_cluster_size: 5       min_cluster_size: 5  ← 保留用户值
    schedule: "0 10 * * 1"    schedule: "0 10 * * 1"   schedule: "0 10 * * 1"
                               cron:                    ← 新增段
                                 health: "0 8 * * *"   cron:
                                                         health: "0 8 * * *"  ← 新增 key 追加
```

实现方式：`python3 -c "yaml.merge(旧, 新)"` 或 `yq eval-all`，不做整文件覆盖。

### 5.4 升级流程

```
hermes-kit upgrade
  Step 1: 备份旧配置 → config.yaml.bak.<timestamp>
  Step 2: 部署组件更新（deploy/deploy.sh deploy <每个组件> --yes）
  Step 3: 合并配置（保留用户值，追加新 key）
  Step 4: 重建 cron（根据新配置的 schedule 字段）
  Step 5: 验证
  Step 6: 通知
```

### 5.5 配置即 cron 来源

install.sh 和 upgrade.sh 中的 cron 创建逻辑不再硬编码调度时间，而是从 `~/.hermes-kit/config.yaml` 读取：

```yaml
# config/default.yaml（新增 cron 段）
cron:
  system_health: "0 8 * * 1-5"
  health_report: "0 8 * * *"
  daily_learn: "0 9 * * 1-5"
  kvector_maintenance: "0 9 * * 6"
  deep_research: "0 9 * * 0"       # agent 任务，无 script
  clustering: "0 10 * * 1"
  knowledge_tree_consolidate: "0 11 * * 1"
  kn_baseline: "0 12 * * *"
  skill_eval: "0 12 * * *"
  memory_cleanup: "0 13 * * *"
  router_health: "0 14 * * *"
  skill_optimization: "0 15 * * *"
  dream_daily: "0 16 * * *"
  cron_detect: "0 * * * *"
```

install.sh 改为：
1. 读取 `~/.hermes-kit/config.yaml` 的 `cron:` 段
2. 按配置创建 cron
3. 用户改配置后，运行 `hermes-kit upgrade` 自动重建 cron

## 七、一键安装流程

```bash
hermes-kit install
```

**执行步骤：**

```
Step 1: 环境检查
  ├─ Hermes 版本 ≥ 0.19.0?     → hermes --version
  ├─ Docker 运行中?             → systemctl is-active docker
  ├─ PostgreSQL 可连?           → psql -h 127.0.0.1 -p 5434 -U postgres -c "SELECT 1"
  ├─ LiteLLM 可连?              → curl :4142/health/liveliness
  └─ Python 3.11+?              → python3 --version

Step 2: 部署组件（严格按此顺序）
  ├─ [1] cron-common 公共库     → deploy/deploy.sh deploy cron-common --yes
  ├─ [2] 知识导航插件           → deploy/deploy.sh deploy knowledge-navigation --yes
  ├─ [3] 知识树插件             → deploy/deploy.sh deploy knowledge-tree-plugin --yes
  ├─ [4] 知识树构建器           → deploy/deploy.sh deploy knowledge-tree-builder --yes
  ├─ [5] 聚类分析               → deploy/deploy.sh deploy clustering-analysis-v3 --yes
  ├─ [6] 记忆清理               → deploy/deploy.sh deploy memory-cleanup --yes
  ├─ [7] Skill 优化             → deploy/deploy.sh deploy skillopt-runner --yes
  ├─ [8] SkillOpt 库            → deploy/deploy.sh deploy skillopt-sleep --yes
  ├─ [9] 系统巡检               → deploy/deploy.sh deploy system-health-check --yes
  ├─ [10] 每日在线学习          → deploy/deploy.sh deploy daily-learn --yes
  ├─ [11] 梦境合成              → deploy/deploy.sh deploy dream-synth --yes
  ├─ [12] 自进化研究            → deploy/deploy.sh deploy self-evolving --yes
  ├─ [13] cron wrappers         → deploy/deploy.sh deploy cron-wrappers --yes
  └─ [14] 评估工具（可选）      → deploy/deploy.sh deploy recall-eval --yes

Step 3: 配置
  ├─ 创建 ~/.hermes-kit/config.yaml
  ├─ 追加环境变量到 .env
  ├─ 启用插件（config.yaml plugins.enabled）
  └─ 验证配置

Step 4: 创建 cron 任务（通过 `hermes cron create` API，共 15 个常驻 + 1 个一次性）
  ├─ 系统巡检              → 0 8 * * 1-5       health-check-cron.sh                 --no-agent
  ├─ 飞轮健康报告          → 0 8 * * *         flywheel-health-report.sh             --no-agent
  ├─ 每日在线学习          → 0 9 * * 1-5       daily-learn/daily_learn.sh            --no-agent
  ├─ k-vector 维护         → 0 9 * * 6         knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh  --no-agent
  ├─ 每周深度研究          → 0 9 * * 0         （agent 任务，无 script）
  ├─ 聚类分析              → 0 10 * * 1        clustering-analysis-v3/scripts/clustering-analysis-cron.sh            --no-agent
  ├─ 知识树 consolidate    → 0 11 * * 1        knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh           --no-agent
  ├─ 知识导航基线          → 0 12 * * *        knowledge-navigation-baseline.sh      --no-agent
  ├─ Skill Eval            → 0 12 * * *        run-skill-eval.sh                     --no-agent
  ├─ 记忆清理              → 0 13 * * *        memory-cleanup/daily_dryrun.sh        --no-agent
  ├─ Router 巡检           → 0 14 * * *        kn-router-health-check.sh             --no-agent
  ├─ Skill 优化            → 0 15 * * *        skillopt-runner/skillopt-nightly-run.sh --no-agent
  ├─ 梦境合成              → 0 16 * * *        dream-synth/scripts/dream-daily.sh    --no-agent
  ├─ cron 异常检测         → 0 * * * *         cron-periodic-detect.sh               --no-agent
  └─ 论文投稿提醒（一次性）→ once 2026-08-06 09:00  （agent 任务，可选，由用户手动创建）

> **API 验证**：`hermes cron --help` 已确认支持 `create / list / pause / resume / remove / edit` 子命令，`hermes cron create` 接受 `--name / --script / --no-agent / --workdir / --deliver / --skill / --repeat` 等参数，可覆盖本步骤全部字段。
>
> **幂等策略**：install.sh 创建每个 cron 前先 `hermes cron list` 检查同名 job 是否存在：
> - 已存在且 schedule 一致 → 跳过（INFO 日志）
> - 已存在但 schedule 不一致 → 警告并跳过，提示用户手动 `hermes cron edit` 决策（**不覆盖**已有调度，避免破坏用户自定义）
> - 不存在 → `hermes cron create` 创建
>
> 这保证在现有环境（已有 15 个 cron 在跑）重新运行 install.sh 不会破坏既有调度。

Step 5: 验证
  ├─ 插件已加载?            → grep knowledge-navigation ~/.hermes/config.yaml
  ├─ cron 已创建?           → hermes cron list
  ├─ 手动触发一次巡检       → python3 ~/.hermes/scripts/health-check-all.py
  └─ 飞书通知可达?          → lark-cli im +messages-send --chat-id ... --text "Hermes-Kit 安装完成"
```

## 八、目录结构

```
/mnt/d/HermesProject/hermes-kit/
├── SPEC.md                       # 本架构文档
├── README.md                     # 安装说明
├── install.sh                    # 一键安装脚本（核心！）
├── uninstall.sh                  # 卸载脚本
├── config/
│   └── default.yaml              # 默认配置模板
├── templates/
│   └── .env.append               # 需要追加到 .env 的配置项
├── scripts/
│   ├── kit-status.sh             # 组件状态检查
│   ├── kit-update.sh             # 组件更新
│   └── kit-verify.sh             # 安装验证
└── manifests/
    └── kit.manifest              # 文件清单
```

## 九、安装脚本核心逻辑

```bash
# install.sh 核心逻辑（伪代码）
install() {
    check_env                         # 检查依赖（hermes/docker/pg/litellm/python）
    deploy_cron_common                # [1] cron-common（其他 wrapper 依赖此公共库）
    deploy_plugins                    # [2-3] knowledge-navigation + knowledge-tree-plugin
    deploy_scripts                    # [4-12] 知识树/聚类/记忆/skillopt/skillopt-sleep
                                      #         系统巡检/每日学习/梦境合成/自进化
    deploy_cron_wrappers              # [13] cron-wrappers（依赖 cron-common + 各项目 Python）
    deploy_optional                   # [14] recall-eval（可选）
    write_config                      # 写入 ~/.hermes-kit/config.yaml
    append_env                        # 追加 .env 配置
    enable_plugins                    # 修改 config.yaml 启用插件
    create_cron_jobs                  # 创建 14 个 cron 任务（12 no_agent + 2 agent）
    verify_installation               # 验证安装
    notify_complete                   # 飞书通知
}
```

## 十、与现有系统的关系

| 现有组件 | Hermes-Kit 关系 | 说明 |
|---------|:---------------:|------|
| Hermes Gateway | 宿主 | 依赖，不修改 |
| Hindsight daemon | 依赖 | 四路召回需要 |
| LiteLLM | 依赖 | 模型路由需要 |
| PostgreSQL | 依赖 | 存储需要 |
| SAG | 依赖 | 知识检索需要 |
| 现有 cron | 接管 | 统一管理，不重复创建 |
| 现有 deploy.sh | 复用 | 所有组件走 deploy 部署（含 manifest 清单） |

### 配置文件关系

系统中有两个 `config.yaml`，职责分离，互不覆盖：

| 文件 | 所有者 | install.sh 操作 | 内容 |
|------|--------|----------------|------|
| `~/.hermes/config.yaml` | Hermes 本身 | **仅追加** `plugins.enabled` 列表项（knowledge-navigation / knowledge-tree-plugin），不动其他字段 | Hermes 全局配置（model、provider、plugins、tools 等） |
| `~/.hermes-kit/config.yaml` | Hermes-Kit | **全量写入**（首次）/ **合并更新**（升级） | Kit 自身的飞轮开关、调度、通知配置 |

**边界约束**：
- install.sh 对 `~/.hermes/config.yaml` 的修改仅限 `plugins.enabled` 字段，使用 yq 或 Python yaml 库做字段级 upsert，不做整文件覆盖
- 修改前自动备份 `~/.hermes/config.yaml` 到 `~/.hermes/config.yaml.bak.<timestamp>`
- 若检测到 `plugins.enabled` 已包含目标插件，跳过不重复追加

### 配置注入策略

> **核心矛盾**：现有 wrapper 脚本（如 [daily_dryrun.sh](file:///d:/HermesProject/scripts/cron-wrappers/memory-cleanup/daily_dryrun.sh)）的参数（`--vote 1 --apply`）是**硬编码**在脚本里，不读 `~/.hermes-kit/config.yaml`。SPEC 第三节承诺"无改动，原样部署"。kit 需要在不改 wrapper 的前提下让 kit-config 生效。

**策略：env 注入 + 子项目 config 软链**

1. **环境变量注入**（主路径）：install.sh 根据 `~/.hermes-kit/config.yaml` 渲染 `templates/.env.append`，追加到 `~/.hermes/.env`。各 wrapper 通过 `cron_common.sh` 加载 .env，间接获得配置。
   - 例：`memory_cleanup.dry_run: true` → `HERMES_KIT_MEM_DRY_RUN=1` → wrapper 中 `DRY_RUN=${HERMES_KIT_MEM_DRY_RUN:-0}`

2. **子项目 config 软链**（fallback）：对有自己 `config/default.yaml` 的子项目（memory-cleanup、clustering-analysis-v3、knowledge-tree-builder），install.sh 不改子项目 config，仅在 `~/.hermes-kit/config.yaml` 中暴露**子项目不支持的字段**为只读元数据（标注 `# read-only: 子项目 config/default.yaml`），让用户知道改这些字段要去子项目 config。

3. **不支持的字段**：kit-config 中以下字段属于"声明性"配置，仅用于 `hermes-kit status` 展示和文档化，**不影响实际行为**：
   - `clustering.min_cluster_size` / `silhouette_threshold`（由 [clustering-analysis-v3/config/default.yaml](file:///d:/HermesProject/scripts/clustering-analysis-v3/config/default.yaml) 控制）
   - `knowledge_tree.consolidate.min_sub_nodes` / `max_split_size`（由 [knowledge-tree-builder/config/default.yaml](file:///d:/HermesProject/scripts/knowledge-tree-builder/config/default.yaml) 控制）
   - `skill_optimization.top_k`（由 [skillopt-runner/config.yaml](file:///d:/HermesProject/scripts/skillopt-runner/config.yaml) 控制）

**实施约束**：
- install.sh 渲染 .env.append 前，先 diff 旧版本，仅追加变更项，不覆盖用户已有的 .env 配置
- .env.append 中所有 kit 注入的变量以 `HERMES_KIT_` 前缀命名，避免与 Hermes 本身的变量冲突
- wrapper 脚本若需读 kit-config，必须使用 `${HERMES_KIT_XXX:-default}` 模式，保证无 .env 时也能用默认值运行

### 部署目标路径映射

| 组件 | 源目录 | 部署目标 | manifest |
|------|--------|----------|----------|
| knowledge-navigation | `plugins/knowledge-navigation/` | `/root/.hermes/plugins/knowledge-navigation/` | `knowledge-navigation.manifest` |
| knowledge-tree-plugin | `plugins/knowledge-tree-plugin/` | `/root/.hermes/plugins/knowledge-tree-plugin/` | `knowledge-tree-plugin.manifest` |
| knowledge-tree-builder | `scripts/knowledge-tree-builder/` | `/root/.hermes/scripts/knowledge-tree-builder/` | `knowledge-tree-builder.manifest` |
| clustering-analysis-v3 | `scripts/clustering-analysis-v3/` | `/root/.hermes/scripts/clustering-analysis-v3/` | `clustering-analysis-v3.manifest` |
| memory-cleanup | `scripts/memory-cleanup/` | `/root/.hermes/scripts/memory-cleanup/` | `memory-cleanup.manifest` |
| skillopt-runner | `scripts/skillopt-runner/` | `/root/.hermes/skillopt-runner/` | `skillopt-runner.manifest` |
| skillopt-sleep | `scripts/skillopt-sleep/` | `/root/.hermes/skillopt-sleep/` | `skillopt-sleep.manifest` |
| cron-common | `scripts/cron_common.sh` + `scripts/cron_job_template.sh` | `/root/.hermes/lib/` | `cron-common.manifest` |
| cron-wrappers | `scripts/cron-wrappers/` | `/root/.hermes/scripts/` | `cron-wrappers.manifest` |
| system-health-check | `scripts/system-health-check/` | `/root/.hermes/scripts/` | `system-health-check.manifest` |
| daily-learn | `scripts/cron-wrappers/daily-learn/` | `/root/.hermes/scripts/daily-learn/` | `daily-learn.manifest` |
| dream-synth | `scripts/dream-synth/` | `/root/.hermes/scripts/dream-synth/` | `dream-synth.manifest` |
| self-evolving | `scripts/self-evolving/` | `/root/.hermes/scripts/self-evolving/` | `self-evolving.manifest` |
| recall-eval（可选） | `scripts/recall-eval/` | `/root/.hermes/scripts/recall-eval/` | `recall-eval.manifest` |
| p0-benchmark（可选） | `scripts/p0-benchmark/` | `/root/.hermes/scripts/p0-benchmark/` | `p0-benchmark.manifest` |

## 十一、实施计划

| 阶段 | 内容 | 预估工时 |
|------|------|:--------:|
| **Phase 1** | 创建目录结构 + install.sh 框架 + 环境检查 | 1h |
| **Phase 2** | 实现组件部署（14 个组件串行部署） | 1.5h |
| **Phase 3** | 实现配置写入 + 14 个 cron 创建 | 1h |
| **Phase 4** | 验证脚本 + 飞书通知 | 1h |
| **Phase 5** | 测试：全新环境安装 + 现有环境安装 | 2h |

**总计：约 6.5 小时**

## 十二、关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 配置格式 | YAML | 与 Hermes config.yaml 一致，不引入新格式 |
| 安装方式 | bash 脚本 | 零依赖，Hermes 用户都有 bash |
| 组件版本 | 跟踪 HermesProject 源码 | 不走独立版本管理，减少维护负担 |
| 失败处理 | 幂等重试，每步有状态标记 | 安装中断后可以重新运行 |
| 卸载 | 逆向操作，保留数据（见下方卸载语义） | 可回滚，不丢记忆 |
| cron 管理 | 通过 `hermes cron` CLI | 统一状态管理，不另起炉灶 |
| 部署方式 | 全部走 `deploy/deploy.sh` | 复用现有 manifest 机制 |
| 配置注入 | env 注入（`HERMES_KIT_` 前缀） | 不改 wrapper 脚本，遵守"无改动"承诺 |
| chat_id | 从 .env 注入，不硬编码 | 通用模板不应包含用户特定 ID |

### 卸载语义

`uninstall.sh` 按"代码卸载、保留数据"原则执行逆向操作，与 `deploy.sh cleanup --uninstall` 的"删部署文件 + 删所有备份"行为不同。

**卸载步骤**：

| 步骤 | 操作 | 保留/删除 |
|------|------|-----------|
| 1 | `hermes cron remove <job>` 逐个移除 15 个 cron | 删除 cron 调度 |
| 2 | `deploy.sh cleanup --uninstall` 移除 14 个组件部署文件 | 删除代码 |
| 3 | 从 `~/.hermes/config.yaml` 的 `plugins.enabled` 移除 knowledge-navigation / knowledge-tree-plugin | 仅移除指定字段，不删整个 config |
| 4 | 删除 `~/.hermes-kit/` 目录 | 删除 kit 自身 |

**保留项（不删除）**：

| 数据类型 | 路径 | 理由 |
|---------|------|------|
| Hermes 全局配置 | `~/.hermes/config.yaml` | 用户可能有其他插件配置 |
| Hermes 数据库 | PostgreSQL（hindsight、knowledge_tree 等） | 知识树/记忆是用户资产 |
| Hermes cron 调度状态 | `~/.hermes/cron/jobs.json`、`executions.db` | 历史执行记录 |
| Hermes 日志 | `~/.hermes/logs/` | 审计需要 |
| 部署备份 | `~/.hermes/backups/<project>/` | 允许用户回滚 |
| 子项目生成的数据 | `~/.hermes/scripts/daily-learn/weekly-reports/` 等 | 用户研究产物 |

> **强制确认**：uninstall.sh 在执行步骤 1-4 前必须 `read -p` 确认，且默认 `--dry-run` 模式只展示将删除的文件清单，需显式 `--apply` 才真正执行。