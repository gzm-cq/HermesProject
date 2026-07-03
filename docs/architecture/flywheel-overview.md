# Hermes 数据飞轮概览

> 本文档描述 Hermes 当前实际运转的数据飞轮与能力飞轮组成。
> 规划蓝图见 [flywheel-blueprint.md](flywheel-blueprint.md)，记忆体系设计见 [记忆体系架构设计-当前.md](../记忆体系架构设计-当前.md)。

## 数据飞轮（知识飞轮）

核心环节：知识生产 → 知识组织 → 知识消费 → 闭环优化。

| 环节 | 项目 | 路径 | 作用 |
|------|------|------|------|
| **知识生产** | 知识树构建器 | `scripts/knowledge-tree-builder/` | 从文档批量建树、周期 consolidate 维护 |
| | 知识树在线插件 | `plugins/knowledge-tree-plugin/` | `post_llm_call` 增量提取知识点 |
| | Hindsight retain | （外部服务） | 对话经验自动沉淀为记忆单元 |
| **知识组织** | 聚类分析 | `scripts/clustering-analysis-v3/` | HDBSCAN 聚类 + 因果链检测，优化 RAG 索引 |
| | 记忆清理 | `scripts/memory-cleanup/` | LLM 分类 retain/remove/merge/compress，控制 L2 token |
| **知识消费** | 知识导航插件 | `plugins/knowledge-navigation/` | LLM Router 三路召回（H + KT + S），注入上下文 |

### 飞轮闭环

```
对话/任务 ──→ 知识导航插件（三路召回） ──→ LLM 输出
   ↑                                            |
   |                                            v
   |                              新经验 -> Hindsight retain
   |                              新知识 -> 知识树 post_llm_call
   |                                            |
   |                                            v
   |                         聚类优化 <- 记忆清理 <- 周期维护
   |                                            |
   +---------- 下一轮召回更精准 -----------------+
```

---

## 能力飞轮（独立闭环）

| 项目 | 路径 | 作用 |
|------|------|------|
| SkillOpt Runner | `scripts/skillopt-runner/` | 基于负反馈自动优化 skill 文档（调度入口） |
| SkillOpt Sleep | `scripts/skillopt-sleep/` | 训练引擎本体（rollout -> reflect -> revise 循环） |
| 自我进化研究 | `scripts/self-evolving/` | SE-Agent 三层进化算子（Revision / Recombination / Refinement） |

---

## Router 飞轮（独立闭环）

| 项目 | 路径 | 作用 |
|------|------|------|
| Router 决策 | `plugins/knowledge-navigation/core/router.py` | LLM Router 决策 `{h, kt, s}` mask，含三层 JSON 解析兜底 |
| Router 健康巡检 | `scripts/cron-wrappers/kn-router-health-check.sh` | 每日检查 Router 解析失败率、recall 成功率、模型稳定性 |
| 知识导航基线 | `scripts/cron-wrappers/knowledge-navigation-baseline.sh` | 每周采集 baseline，LLM judge 评估，delta 检测告警 |
| Skill 评估 | `scripts/cron-wrappers/run-skill-eval.sh` | 每日评估 Skill 匹配质量，退化告警 |

**Router 飞轮闭环**：

```
用户消息 → LLM Router 决策 → 按 mask 条件执行三路召回 → LLM 输出
   ↑                                                     |
   |                                                     v
   |                                   Router 健康巡检 + 基线采集
   |                                                     |
   |                                                     v
   |                                   发现问题 → 优化 prompt / 调整阈值
   |                                                     |
   +---------- 下一轮决策更精准 ---------------------------+
```

---

## 运维支撑

| 项目 | 路径 | 作用 |
|------|------|------|
| 系统健康巡检 | `scripts/system-health-check/` | 3-tier 架构巡检 + 飞书告警 |
| Cron 公共层 | `deploy/cron-common/` + `scripts/cron-wrappers/` | 定时任务基础设施（flock 互斥、日志包装、补偿修复） |
| AI 报告生成 | `scripts/ai-report-system/` | 多数据源分析报告 |
