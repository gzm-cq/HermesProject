# HermesProject 任务看板

| 最后更新: 2026-07-03
| 当前状态: 全部 P0-P1 任务已完成（CRON-05 为 P1 hotfix）
> 路线图总览见 [ROADMAP.md](ROADMAP.md)

---

## ✅ 全部完成（2026-07-02）

当前所有 P0-P1 任务已完成。详见下方「已完成（最近）」列表。

## P2 — 保留待办

### INFRA-01: 声明式部署系统
- **SPEC**: [deploy-system-redesign](specs/backlog/deploy-system-redesign.md)
- **任务**: deploy.sh 从 shell 脚本演进为声明式 CLI + 可插拔步骤
- **状态**: ⬜ 规划中（当前 deploy.sh 可用）

### INFRA-02: 统一调度框架
- **SPEC**: [cron-scheduler-design](specs/backlog/cron-scheduler-design.md)
- **任务**: hermes-scheduler 统一调度入口，DAG 依赖管理
- **启动条件**: 出现真实 DAG 依赖时重新评估（2026-07-03 已评估 Dagu v2.9.1，不采用）
- **状态**: 🟡 已评估（不新建）

## 已评估（不新建）

以下经 2026-07-02 审计评估后不做：反馈飞轮（现有 SkillOpt+Baseline+巡检已覆盖）、经济飞轮、元认知飞轮、知识飞轮、数据反馈层 L2、ECC v2 控制面。详见 [flywheel-blueprint.md](architecture/flywheel-blueprint.md)。

2026-07-03 新增：统一调度框架（Dagu v2.9.1）评估后不采用。详见 [ADR](adr/2026-07-03-evaluate-dagu-rejected.md)。

---

## 已完成（最近）

| ID | 任务 | 完成时间 | SPEC |
|----|------|---------|------|
| FW-03 | 数据飞轮 P3-8 domain 缓存 key | 2026-07-02 | [flywheel-optimization-spec](specs/completed/flywheel-optimization-spec.md) |
| FW-01 | 数据飞轮 P3-5 embedding 复用 | 2026-07-02 | [flywheel-optimization-spec](specs/completed/flywheel-optimization-spec.md) |
| CRON-02 | 聚类 Phase 6 基线反馈闭环 | 2026-07-02 | [flywheel-cron-restructure-spec](specs/completed/flywheel-cron-restructure-spec.md) |
| SO-01 | SkillOpt resolve_edit_skill_name | 2026-07-02 | [skillopt-runner-pesc-fix-plan](specs/completed/skillopt-runner-pesc-fix-plan-2026-06-19.md) |
| KN-02 | Router prompt 自愿全开降低 | 2026-07-02 | [router-alltrue-reduction-spec](specs/completed/router-alltrue-reduction-spec.md) |
| CRON-03 | Skill 评估 cron 日常化 | 2026-07-02 | [flywheel-cron-restructure-spec](specs/completed/flywheel-cron-restructure-spec.md) |
| CRON-01 | Router 巡检 ERROR 修复 | 2026-07-02 | [flywheel-cron-restructure-spec](specs/completed/flywheel-cron-restructure-spec.md) |
| SO-02 | SkillOpt P1-D/E 状态确认 | 2026-07-02 | [skillopt-runner-pesc-fix-plan](specs/completed/skillopt-runner-pesc-fix-plan-2026-06-19.md) |
| KN-01 | Router JSON 解析修复（三层兜底） | 2026-07-02 | [router-alltrue-reduction-spec](specs/completed/router-alltrue-reduction-spec.md) |
| FW-02 | 矛盾检测条件提取增强（12种模式） | 2026-07-02 | [flywheel-optimization-spec](specs/completed/flywheel-optimization-spec.md) |
| KN-03 | Skill Matcher 三级混合筛选 | 2026-07-02 | [skill-matcher-eval-spec](specs/completed/skill-matcher-eval-spec.md) |
| KN-04 | 知识树实体多跳 v2 | 2026-07-01 | [spec-knowledge-tree-entity-multihop-v2](specs/completed/spec-knowledge-tree-entity-multihop-v2.md) |
| KN-05 | baseline 采集逻辑修复 | 2026-07-02 | — |
| KN-06 | Embedding 熔断 + 线程安全 | 2026-07-02 | — |
| KN-07 | post_llm_call 增量放置修复 | 2026-06-28 | [post-llm-performance-optimization-spec](specs/completed/post-llm-performance-optimization-spec.md) |
| CRON-04 | Cron 自愈框架 Phase 1-3 | 2026-07-01 | [cron-self-healing-spec](specs/completed/cron-self-healing-spec.md) |
| KT-01 | 知识树建树管线重构 | 2026-06-09 | [development-plan](specs/completed/development-plan.md) |
| KT-02 | k_vector 写入加固 + 多跳 | 2026-06-28 | [spec-knowledge-tree-k-vector-multihop](specs/completed/spec-knowledge-tree-k-vector-multihop.md) |
| KT-03 | Router LLM 决策实施 | 2026-06-27 | [SPEC-router](specs/completed/SPEC-router.md) |
| KT-04 | 上下文语义标签化 | 2026-06-27 | [context-tagging-plan](specs/completed/context-tagging-plan.md) |
| KT-05 | Phase A 记忆标记+时态衰减+基线 | 2026-06-10 | [phase-a-detailed-design](specs/completed/phase-a-detailed-design.md) |
| KT-06 | Phase A 进阶优化（MMR+自适应+压缩+因果链） | 2026-06-10 | [phase-a-optimization-design](specs/completed/phase-a-optimization-design.md) |
| KT-07 | 知识树质量基线反馈闭环 | 2026-07-03 | [kt-baseline-feedback-spec](specs/completed/kt-baseline-feedback-spec.md) |
| MC-01 | memory-cleanup merge/compress 质量增强 | 2026-06-15 | [memory-cleanup-optimization-plan](specs/completed/memory-cleanup-optimization-plan.md) |
| SE-01 | 自进化飞轮 Phase A | 2026-05-30 | [task-inventory](specs/completed/task-inventory.md) |
| CRON-05 | Cron 环境变量兜底 + Skill Eval timeout 修复 | 2026-07-03 | [cron-env-loader-spec](specs/completed/cron-env-loader-spec.md) |
| CRON-06 | cron-periodic-detect 恢复上线（L2 静默检测） | 2026-07-03 | [cron-self-healing-spec](specs/completed/cron-self-healing-spec.md) |
| KN-08 | eval_queries_auto.json dimension 补全 + 生成器修复 | 2026-07-04 | — |

> 完整已完成列表见 [specs/completed/](specs/completed/) 目录
