# HermesProject 文档索引

> 最后更新: 2026-06-13

## 阅读顺序

新 contributor 建议按此顺序阅读：

1. `README.md`（项目根）— 项目概览、架构、子项目清单
2. `docs/architecture/记忆体系架构设计-当前.md` — 5 层记忆体系全景
3. `docs/architecture/knowledge-point-definition.md` — 知识点模型 v2.3
4. `docs/engineering-standards.md` — 开发规范
5. `REASONIX.md` — 工作知识速查

---

## 文档分类

### Architecture — 架构设计

| 文档 | 说明 | 状态 |
|------|------|:----:|
| `记忆体系架构设计-当前.md` | 5层记忆体系 + 双域 + 自进化飞轮（入口文档） | ✅ 当前 |
| `knowledge-point-definition.md` | 知识点模型 v2.3（五分类/准入/去重/树定位） | ✅ 当前 |
| `hindsight-memory-maintenance-framework.md` | Hindsight 记忆维护框架（不碰源码） | ✅ 当前 |
| `layer2-data-feedback-design.md` | 数据反馈层 v2.1 | 待实施 |
| `phase-a-detailed-design.md` | Phase A v1.1（记忆标记/时态衰减/基线） | ✅ 已实施 |
| `phase-a-optimization-design.md` | Phase A 优化 v3.0 | ✅ 当前 |
| `project-profile.md` | 完整项目元信息（技术栈/依赖/cron） | ✅ 当前 |
| `cron-scheduler-design.md` | Cron wrapper 标准化设计 | 部分实施 |
| `deploy-system-redesign.md` | 声明式部署系统目标设计 | 目标状态 |
| `flywheel-blueprint.md` | 8 个可构建的飞轮蓝图 | 规划参考 |
| `ecc-v2-control-plane-integration-draft.md` | ECC v2 控制面集成分析 | 草案 |
| `skillopt-hermes-integration-analysis.md` | SkillOpt-Hermes 集成方案 | ✅ 当前 |

### Plans — 活跃计划

| 文档 | 说明 |
|------|------|
| `开发状态与路线图.md` | 知识树开发状态和待办 |
| `development-plan.md` | 知识树管线重构开发计划 |
| `task-inventory.md` | 自进化飞轮任务优先级 v2.0 |
| `skillopt-runner-pesc-fix-plan-2026-06-19.md` | SkillOpt-Runner PESC 修复计划 |

### Research — 行业调研

| 文档 | 说明 |
|------|------|
| `前沿与最佳实践*.md` | AI Agent 记忆存储技术行业分析 |
| `当前正式环境的记忆存储技术全面分析.md` | 生产环境记忆系统快照 |
| `三算子作用发挥评估.md` | SE-Agent 三大算子评估 |

### Reviews — 审查报告

`docs/reviews/` 目录保留，内容不动。

### Archive — 已归档

| 文档 | 原位置 | 说明 |
|------|--------|------|
| `01-fix-plan.md` | `docs/reviews/` | 记忆/知识系统修复计划（已执行） |
| `2026-06-06-knowledge-tree-repair.md` | `docs/superpowers/plans/` | 知识树修复计划（已整合进 consolidate） |
| `2026-06-08-domain-merge.md` | `docs/superpowers/plans/` | domain 合并计划（已整合） |

### 标准规范

| 文档 | 位置 | 说明 |
|------|------|------|
| 工程标准 | `docs/engineering-standards.md` | Python 开发规范 |
| 架构规范 | `.qoder/rules/architecture-spec.md` | 5 层记忆体系约束 |
| 命名规范 | `.qoder/rules/naming-conventions.md` | snake_case / PascalCase |
| Git 工作流 | `.qoder/rules/git-workflow-spec.md` | commit 格式、分支策略 |
| 部署规范 | `.qoder/rules/deployment-spec.md` | deploy.sh 使用规范 |
| 测试规范 | `.qoder/rules/testing-spec.md` | pytest 覆盖率目标 |
| 开发需求 | `.qoder/rules/requirements-spec.md` | 生成完整可运行代码 |
