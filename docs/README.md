# HermesProject 文档索引

> 最后更新: 2026-07-03

## 快速入口

| 你想... | 去哪里 |
|---------|--------|
| 了解项目整体目标和路线图 | [ROADMAP.md](ROADMAP.md) |
| 查看当前待办任务 | [TASKBOARD.md](TASKBOARD.md) |
| 搭建开发环境 | [setup-guide.md](setup-guide.md) |
| 了解架构设计 | [architecture/](architecture/) |
| 查看 SPEC 计划文档 | [specs/](specs/) |
| 查看审查报告 | [reviews/](reviews/) |
| 查看行业调研 | [research/](research/) |
| 查看归档文档 | [archive/](archive/) |

## 新人阅读顺序

1. **[README.md](../README.md)**（项目根）— 项目概览、架构图、子项目清单
2. **[ROADMAP.md](ROADMAP.md)** — 路线图总览，了解项目目标与里程碑
3. **[TASKBOARD.md](TASKBOARD.md)** — 当前任务看板，了解正在做什么
4. **[setup-guide.md](setup-guide.md)** — 环境搭建完整指南
5. **[architecture/记忆体系架构设计-当前.md](architecture/记忆体系架构设计-当前.md)** — 5 层记忆体系全景
6. **[architecture/knowledge-point-definition.md](architecture/knowledge-point-definition.md)** — 知识点模型 v2.3
7. **[engineering-standards.md](engineering-standards.md)** — 开发规范

---

## 目录结构

```
docs/
├── ROADMAP.md               # 项目路线图（目标 + 里程碑 + 优先级）
├── TASKBOARD.md             # 任务看板（P0/P1/P2 + 状态）
├── README.md                # 本文件（文档索引）
├── setup-guide.md           # 环境搭建指南
├── engineering-standards.md # 工程标准
├── architecture/            # 架构设计文档（当前生效的）
├── specs/                   # SPEC 计划文档（按状态分类）
│   ├── README.md            # SPEC 索引
│   ├── active/              # 正在执行的 SPEC
│   ├── completed/           # 已完成的 SPEC
│   └── backlog/             # 待规划/暂缓的 SPEC
├── reviews/                 # 审查报告
├── research/                # 行业调研
└── archive/                 # 归档文档（废弃/历史）
```

## Architecture — 架构设计

| 文档 | 说明 | 状态 |
|------|------|:----:|
| [记忆体系架构设计-当前.md](architecture/记忆体系架构设计-当前.md) | 5层记忆体系 + 双域 + 自进化飞轮（入口文档） | ✅ 当前 |
| [记忆体系架构设计-v2.md](architecture/记忆体系架构设计-v2.md) | 设计总纲（最简架构） | ✅ 参考 |
| [knowledge-point-definition.md](architecture/knowledge-point-definition.md) | 知识点模型 v2.3（五分类/准入/去重/树定位） | ✅ 当前 |
| [hindsight-memory-maintenance-framework.md](architecture/hindsight-memory-maintenance-framework.md) | Hindsight 记忆维护框架 | ✅ 当前 |
| [project-profile.md](architecture/project-profile.md) | 完整项目元信息（技术栈/依赖/cron） | ✅ 当前 |
| [flywheel-overview.md](architecture/flywheel-overview.md) | 数据飞轮与能力飞轮组成 | ✅ 当前 |
| [flywheel-blueprint.md](architecture/flywheel-blueprint.md) | 8 个可建飞轮蓝图 | 规划参考 |
| [flywheel-optimization-report.md](architecture/flywheel-optimization-report.md) | 数据飞轮优化审计报告 | ✅ 当前 |
| [skillopt-hermes-integration-analysis.md](architecture/skillopt-hermes-integration-analysis.md) | SkillOpt-Hermes 集成方案 | ✅ 当前 |

## SPEC — 计划文档

详见 [specs/README.md](specs/README.md)。按状态分类：

- **[active/](specs/active/)** — 正在执行的 SPEC（0 个）
- **[completed/](specs/completed/)** — 已完成的 SPEC（19 个）
- **[backlog/](specs/backlog/)** — 待规划/暂缓的 SPEC（4 个）

## Research — 行业调研

| 文档 | 说明 |
|------|------|
| [前沿与最佳实践-AI-Agent记忆存储技术分析报告.md](research/前沿与最佳实践-AI-Agent记忆存储技术分析报告.md) | AI Agent 记忆存储技术行业分析 |
| [当前正式环境的记忆存储技术全面分析.md](research/当前正式环境的记忆存储技术全面分析.md) | 生产环境记忆系统快照 |
| [三大算子作用发挥评估.md](research/三大算子作用发挥评估.md) | SE-Agent 三大算子评估 |

## Reviews — 审查报告

`docs/reviews/` 目录保留，内容不动。最近一次完整审查：2026-06-15。

## Archive — 归档文档

| 文档 | 原位置 | 说明 |
|------|--------|------|
| `01-fix-plan.md` | `docs/reviews/` | 记忆/知识系统修复计划（已执行） |
| `2026-06-06-knowledge-tree-repair.md` | `docs/plans/` | 知识树修复计划（已整合进 consolidate） |
| `2026-06-08-domain-merge.md` | `docs/plans/` | domain 合并计划（已整合） |
| `review-plan-memory-knowledge-system-2026-06-15.md` | `docs/` | 多 Agent 审查执行计划（已完成） |
| `self-evolving/` | `scripts/self-evolving/docs/backup/` | 自进化项目历史文档 |
| `knowledge-navigation/` | `plugins/knowledge-navigation/docs/archive/` | 知识导航历史文档 |
| `ai-report-system/` | `scripts/ai-report-system/docs/plans/` | AI 报告系统历史计划 |

## 标准规范

| 文档 | 位置 | 说明 |
|------|------|------|
| 工程标准 | `docs/engineering-standards.md` | Python 开发规范 |
| 架构规范 | `.qoder/rules/architecture-spec.md` | 5 层记忆体系约束 |
| 命名规范 | `.qoder/rules/naming-conventions.md` | snake_case / PascalCase |
| Git 工作流 | `.qoder/rules/git-workflow-spec.md` | commit 格式、分支策略 |
| 部署规范 | `.qoder/rules/deployment-spec.md` | deploy.sh 使用规范 |
| 测试规范 | `.qoder/rules/testing-spec.md` | pytest 覆盖率目标 |
| 开发需求 | `.qoder/rules/requirements-spec.md` | 生成完整可运行代码 |
