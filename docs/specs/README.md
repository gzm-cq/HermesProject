# SPEC 索引

> 最后更新: 2026-07-03
> 路线图总览见 [ROADMAP.md](../ROADMAP.md)，任务看板见 [TASKBOARD.md](../TASKBOARD.md)

---

## Active — 正在执行

当前无活跃 SPEC（全部已于 2026-07-02 完成归档）。

## Completed — 已完成

| SPEC | 项目 | 完成内容 |
|------|------|---------|
| [SPEC-router](completed/SPEC-router.md) | knowledge-navigation | LLM Router 替代规则体系，三路智能决策 |
| [context-tagging-plan](completed/context-tagging-plan.md) | knowledge-navigation | XML 语义标签化注入 |
| [cron-self-healing-spec](completed/cron-self-healing-spec.md) | cron-common | 三层 Detection → Auto-heal → Human 闭环 |
| [development-plan](completed/development-plan.md) | knowledge-tree-builder | 知识树管线从 HDBSCAN 迁移到五分类+领域树 |
| [memory-cleanup-optimization-plan](completed/memory-cleanup-optimization-plan.md) | memory-cleanup | merge/compress 质量增强 |
| [phase-a-detailed-design](completed/phase-a-detailed-design.md) | knowledge-navigation / clustering | 记忆标记 + 时态衰减 + 评估基线 |
| [phase-a-optimization-design](completed/phase-a-optimization-design.md) | knowledge-navigation | MMR + 自适应 MIN_SCORE + 上下文压缩 + 因果链置信度 |
| [post-llm-performance-optimization-spec](completed/post-llm-performance-optimization-spec.md) | knowledge-tree-plugin | post_llm_call 性能优化（22s → 8s） |
| [skill-matcher-eval-spec](completed/skill-matcher-eval-spec.md) | knowledge-navigation | Skill Matcher 评估飞轮 Ring 1（评估环） |
| [spec-implementation-plan](completed/spec-implementation-plan.md) | knowledge-tree-builder | 知识树实体多跳 + 数据清理（初版，被 v2 取代） |
| [spec-knowledge-tree-entity-multihop-v2](completed/spec-knowledge-tree-entity-multihop-v2.md) | knowledge-tree-plugin | 实体多跳 v2（kt_entity_links 表 + entity-based 多跳） |
| [spec-knowledge-tree-k-vector-multihop](completed/spec-knowledge-tree-k-vector-multihop.md) | knowledge-tree-builder | k_vector 写入加固 + 多跳关联（被 v2 取代） |
| [task-inventory](completed/task-inventory.md) | self-evolving | 自进化飞轮任务优先级 v2.0（Phase A 已完成） |
| [开发状态与路线图](completed/开发状态与路线图.md) | knowledge-tree | 知识树开发状态（2026-06，已全部完成） |
| [router-alltrue-reduction-spec](completed/router-alltrue-reduction-spec.md) | knowledge-navigation | Router 全查率降低（JSON 三层解析 + prompt 约束） |
| [skillopt-runner-pesc-fix-plan-2026-06-19](completed/skillopt-runner-pesc-fix-plan-2026-06-19.md) | skillopt-runner | PESC 修复（P0-A/B/C + P1-A/B/C/D/E 全部已实施） |
| [flywheel-cron-restructure-spec](completed/flywheel-cron-restructure-spec.md) | cron-wrappers | Cron 重构（巡检修复 + Phase 6 + Skill Eval + periodic-detect 下线） |
| [flywheel-optimization-spec](completed/flywheel-optimization-spec.md) | knowledge-tree-builder / knowledge-navigation | 数据飞轮优化（P0-P3 全部已实施） |
| [cron-env-loader-spec](completed/cron-env-loader-spec.md) | cron-common / knowledge-navigation | Cron 环境变量三层兜底 + Skill Eval timeout 修复 |

## Backlog — 待规划/暂缓

| SPEC | 项目 | 说明 | 启动条件 |
|------|------|------|---------|
| [layer2-data-feedback-design](backlog/layer2-data-feedback-design.md) | 全局 | 数据反馈层 v2.1，Hindsight 主动进化 | 已评估（2026-07-02）：现有隐式反馈机制已覆盖闭环，无需新建 |
| [cron-scheduler-design](backlog/cron-scheduler-design.md) | cron-common | 统一调度框架 hermes-scheduler | 任务 > 8 或出现 DAG 依赖 |
| [deploy-system-redesign](backlog/deploy-system-redesign.md) | deploy | 声明式部署系统 | 当前 deploy.sh 可用 |
| [ecc-v2-control-plane-integration-draft](backlog/ecc-v2-control-plane-integration-draft.md) | 全局 | ECC v2 控制面 + 飞轮整合 | 草案阶段 |

---

## SPEC 编写规范

新建 SPEC 时建议包含以下结构：

```markdown
# SPEC: <标题>

> **状态**: 草案 / 已确认 / 已实施 / 已归档
> **项目**: <所属项目>
> **创建时间**: YYYY-MM-DD

## 一、问题全景
（现状描述 + 数据支撑）

## 二、方案设计
（技术方案 + 修改文件清单）

## 三、实施计划
（分阶段任务 + 验证方式）

## 四、实施状态
（逐项标记 ✅/🔴/⚠️，附实施证据）
```

完成后将 SPEC 移到对应状态目录（active → completed 或 backlog），并更新本索引。
