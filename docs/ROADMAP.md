# HermesProject 路线图

> 最后更新: 2026-07-03

## 项目愿景

构建端到端的 AI 智能体平台，通过 **5 层记忆体系 + 三级混合筛选 + 飞轮闭环优化**，实现：
- 控制 token 开销的同时最大化记忆有效性
- 技能即时可用，知识主动召回
- 数据驱动的持续自进化

## 里程碑总览

| 里程碑 | 状态 | 说明 |
|--------|:----:|------|
| **M1: 记忆体系基础设施** | ✅ 完成 | 5 层记忆体系、双域分离（经验域 + 知识域）、Hindsight RAG、知识树建树管线 |
| **M2: 知识导航三路注入** | ✅ 完成 | LLM Router 智能决策、Hindsight + 知识树 + Skill 三路召回、三级混合筛选 |
| **M3: 飞轮闭环基础设施** | ✅ 完成 | 数据飞轮、能力飞轮、Router 飞轮、Cron 自愈框架（三层保险：L1 开机检测 + L2 每30min周期检测 + L3 系统巡检）、12 个 cron 任务。知识树基线反馈闭环（2026-07-03） |
| **M4: 质量评估体系** | ✅ 完成 | Skill Matcher 评估环、Router 全查率降低、基线数据采集 |
| **M5: 飞轮深化优化** | ✅ 完成 | 数据飞轮优化、SkillOpt PESC 修复、Cron 重构全部已实施 |
| **M6: 扩展飞轮建设** | 🟡 已评估（不新建） | 2026-07-02 审计结论：现有隐式反馈/Baseline/SkillOpt/Router巡检已覆盖反馈闭环，无需新建飞轮 |
| **M7: 基础设施升级** | ⬜ 规划中 | 声明式部署系统、ECC v2 控制面集成。统一调度框架（Dagu v2.9.1）已于 2026-07-03 评估后不采用，详见 [ADR](adr/2026-07-03-evaluate-dagu-rejected.md) |

## 飞轮架构

### 已实现飞轮（M1-M3）

| 飞轮 | 核心闭环 | 状态 |
|------|---------|:----:|
| **数据飞轮** | 知识生产→组织→消费→优化（知识树基线反馈 2026-07-03 已闭环） | ✅ |
| **能力飞轮** | 经验→能力→复用→新能力 | ✅ |
| **Router 飞轮** | 决策→执行→反馈→优化 | ✅ |

### 已评估（M6 — 不新建）

2026-07-02 审计结论：隐式反馈/Baseline/巡检/SkillOpt 已覆盖闭环，无需新建飞轮。详见 [flywheel-blueprint.md](architecture/flywheel-blueprint.md)。

## 当前优先级

### P0-P1 — 全部完成（2026-07-03）

所有 P0/P1 任务已于 2026-07-02 完成归档。2026-07-03 新增 CRON-05 hotfix（Cron 环境变量三层兜底 + Skill Eval timeout 修复），详见 [TASKBOARD.md](TASKBOARD.md)「已完成（最近）」列表及 [specs/completed/](specs/completed/) 目录。

### P2 — 保留规划

| 任务 | 所属方向 | 说明 | 启动条件 |
|------|---------|------|---------|
| 声明式部署系统 | [deploy-system-redesign](specs/backlog/deploy-system-redesign.md) | deploy.sh 从 shell 脚本到声明式 | 当前 deploy.sh 可用 |
| 统一调度框架 | [cron-scheduler-design](specs/backlog/cron-scheduler-design.md) | hermes-scheduler 统一调度入口 | 出现真实 DAG 依赖时重新评估（2026-07-03 已评估 Dagu v2.9.1，不采用） |

### 已评估（不新建）

以下基于飞轮蓝图提议，经 2026-07-02 审计评估后不做：反馈飞轮（现有 SkillOpt+Baseline+巡检已覆盖）、经济飞轮、元认知飞轮、知识飞轮、数据反馈层 L2、ECC v2 控制面。详见 [flywheel-blueprint.md](architecture/flywheel-blueprint.md)。

**2026-07-03 新增：统一调度框架（Dagu v2.9.1）评估后不采用** — 10 个独立 shell 脚本无 DAG 依赖，wrapper 模式已稳定运行数月。Dagu 引入环境鸿沟 + 进程开销 + 自愈盲区，收益与成本不匹配。详见 [ADR](adr/2026-07-03-evaluate-dagu-rejected.md)。保留 `cron_warn` `set -e` 修复（`cron_common.sh` 独立 bug 修复）。

## 技术栈

| 组件 | 技术选型 |
|------|---------|
| 语言 | Python 3.10+ / Shell (bash) |
| 数据库 | PostgreSQL 14+ (pgvector) |
| LLM 网关 | LiteLLM (本地 127.0.0.1:4142) |
| Embedding | BAAI/bge-m3 (SiliconFlow) |
| 部署 | deploy.sh (shell) → 未来声明式 |
| 调度 | Hermes cron + cron_common.sh wrapper |
| 开发环境 | WSL2 (Ubuntu) + Windows |
| 测试 | pytest (覆盖率 80%+) |

## 关键架构决策

1. **不修改 Hermes Gateway 源码** — 所有增强通过插件/脚本/配置实现
2. **不修改 Hindsight 源码** — 记忆维护通过 SQL + 脚本层
3. **Feature Flag 控制** — 每个优化项独立开关，可一键回退
4. **三级混合筛选** — 关键词预筛 → Embedding 精筛 → LLM 精排，平衡召回率与效率
5. **飞轮自治并行** — 各飞轮独立调度，通过状态文件松耦合
