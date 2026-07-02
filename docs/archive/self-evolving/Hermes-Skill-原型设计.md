# Hermes Skill 原型设计 — SE-Agent 三大进化算子

> **文档版本**: v1.0  
> **创建时间**: 2026-05-29  
> **状态**: 待验证草案（设计阶段，未实施）  
> **参考来源**: SE-Agent (arXiv:2508.02085) · references/se-agent-evolution-analysis.md

---

## 一、设计目标

将 SE-Agent 的三大进化算子（Revision / Recombination / Refinement）封装为 Hermes Skill，使其成为可复用、可组合的进化能力组件，服务于代码审查、方案优化、文档精炼等场景。

**核心原则**：
- 算子独立可调用，也可串联形成进化闭环
- 输入输出标准化，便于 Kanban 任务流转
- 不依赖外部数据库，自包含执行
- 可降级：单算子模式 → 双算子模式 → 三算子闭环

---

## 二、Skill 元数据

```yaml
---
name: se-agent-evolution
description: SE-Agent 三大进化算子 — 轨迹级自进化能力，用于代码审查、方案优化、推理轨迹优化
version: 0.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [evolution, optimization, trajectory, self-improvement, code-review, document-audit]
    related_skills: [code-review, deep-research, kanban-orchestrator, audit-methodology]
    categories: [autonomous-ai-agents, optimization]
---
```

---

## 三、Skill 目录结构

```
se-agent-evolution/
├── SKILL.md                          # Skill 使用说明（本文件）
├── operators/
│   ├── __init__.py
│   ├── revision.py                   # Revision 算子核心实现
│   ├── recombination.py              # Recombination 算子核心实现
│   └── refinement.py                 # Refinement 算子核心实现
├── models/
│   ├── trajectory.py                 # 轨迹数据模型（Trajectory, Step, ToolCall）
│   ├── failure_diagnosis.py          # 失败诊断模型（FailureType, DiagnosisResult）
│   └── risk_assessment.py            # 风险评估模型（RiskLevel, RiskReport）
├── scripts/
│   ├── se_revision.py                # Revision 工具入口（CLI 调用）
│   ├── se_recombine.py               # Recombination 工具入口
│   └── se_refine.py                  # Refinement 工具入口
├── config/
│   └── default.yaml                  # 默认配置（反思深度、风险阈值等）
└── references/
    └── se-agent-paper-summary.md     # SE-Agent 论文核心内容摘要
```

---

## 四、三大算子详细设计

### 4.1 Revision（修正算子）

**定位**：失败驱动的策略生成器

**触发条件**：
- 代码审查中发现 bug 或逻辑错误
- 任务执行失败（工具调用错误、参数不匹配、状态不一致）
- 文档审计中发现事实错误或数据矛盾
- 用户明确指定"修正"某段内容

**输入规范**：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `failed_content` | str | ✅ | — | 失败的内容（代码/轨迹/文档片段） |
| `context` | str | ✅ | — | 任务上下文（问题描述、约束条件） |
| `failure_type` | str | ❌ | auto | 失败类型（6 种，见下方），不填则自动诊断 |
| `reflection_depth` | int | ❌ | 2 | 反思深度（1=直接原因，2=直接+根本，3=深度溯源） |
| `generate_alternatives` | bool | ❌ | true | 是否生成多个替代方案 |
| `alternative_count` | int | ❌ | 2 | 替代方案数量（仅在 generate_alternatives=true 时生效） |

**失败类型（6 种）**：
| 类型 | 说明 | 适用场景 |
|------|------|----------|
| `invalid_tool_call` | 工具调用格式/名称错误 | 工具调用失败 |
| `argument_mismatch` | 参数类型/格式不匹配 | API 调用、函数参数 |
| `state_mismatch` | 状态与预期不一致 | 多步任务状态跟踪 |
| `recovery_failure` | 错误恢复失败 | 异常处理逻辑 |
| `missing_tool_call` | 遗漏必要的工具调用 | 步骤缺失 |
| `response_mismatch` | 输出与预期不符 | 结果验证失败 |

**输出规范**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `revised_content` | str | 修正后的内容 |
| `failure_root_cause` | str | 根本原因分析（2 层反思结果） |
| `alternative_solutions` | List[str] | 正交替代方案（至少 2 个不同路径） |
| `confidence_score` | float | 修正方案置信度（0-1） |
| `direct_fix` | str | 方案 A：直接修复（最小改动） |
| `orthogonal_fix` | str | 方案 B：正交方案（完全不同路径） |
| `conservative_fix` | str | 方案 C：保守方案（回退已知可行路径） |

**执行流程**：
```
Step 1: [诊断] 分析失败内容，提取失败模式
  └── 若 failure_type 未指定，自动匹配 6 种失败类型

Step 2: [反思] 深度自我反思（reflection_depth 层）
  ├── 第 1 层：直接原因（如：参数格式错误）
  ├── 第 2 层：根本原因（如：对工具 schema 理解偏差）
  └── 第 3 层：溯源（如：文档描述模糊导致理解偏差）

Step 3: [生成] 生成三方案
  ├── 方案 A：直接修复（最小改动，保留原结构）
  ├── 方案 B：正交方案（完全不同实现路径）
  └── 方案 C：保守方案（回退到已知可行模式）

Step 4: [评估] 置信度评分
  └── 基于历史成功率、方案复杂度、风险等级
```

**Hermes 场景示例**：
```
用户：审查这份代码，找出 bug 并修正
→ Revision 算子被触发
→ 诊断：argument_mismatch（函数参数类型错误）
→ 反思：直接原因=类型不匹配；根本原因=函数签名理解偏差
→ 生成：A=修正类型声明；B=重构为泛型函数；C=回退到旧版本 API
→ 输出修正后的代码 + 三种方案供用户选择
```

---

### 4.2 Recombination（重组算子）

**定位**：跨轨迹/跨方案的知识合成器

**触发条件**：
- 多个 worker 并行完成了同一任务的不同路径
- 用户提供了多个候选方案需要融合
- 代码审查中发现不同文件/模块有可复用的模式
- 文档审计中发现多篇文档对同一主题有不同表述，需要整合

**输入规范**：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `candidate_contents` | List[str] | ✅ | — | 候选内容列表（代码/轨迹/文档片段） |
| `task_context` | str | ✅ | — | 任务上下文（目标、约束） |
| `selection_criteria` | str | ❌ | quality | 选择标准（quality / coverage / diversity） |
| `max_components` | int | ❌ | 5 | 最大组件数量 |
| `detect_conflicts` | bool | ❌ | true | 是否检测组件冲突 |

**输出规范**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `recombined_content` | str | 重组后的内容 |
| `component_map` | Dict[str, str] | 组件来源映射（哪个部分来自哪个候选） |
| `synergy_score` | float | 协同效应评分（0-1，>0 表示 1+1>2） |
| `conflict_log` | List[str] | 冲突检测日志（如有） |
| `preserved_components` | List[str] | 保留的高置信度组件 |
| `replaced_components` | List[str] | 被替换的低效组件 |

**执行流程**：
```
Step 1: [提取] 从候选内容中提取可复用组件
  ├── 成功内容：提取有效步骤、正确模式、最优参数
  └── 失败内容：提取"避坑"知识（什么不应该做）

Step 2: [匹配] 基于语义相似度匹配组件
  └── 识别可替换/可合并的相似组件

Step 3: [合成] 生成重组内容
  ├── 保留：高置信度组件
  ├── 替换：用更优方案替换低效组件
  └── 融合：合并多个候选的互补部分

Step 4: [冲突检测] 检查组件间一致性
  └── 语义一致性、依赖关系、格式兼容性

Step 5: [评分] 计算协同效应
  └── synergy = (重组质量 - 平均候选质量) / 平均候选质量
```

**Hermes 场景示例**：
```
用户：Kanban 中 3 个 worker 分别完成了同一模块的代码，帮我合成最优版本
→ Recombination 算子被触发
→ 提取：worker1 的异常处理 + worker2 的性能优化 + worker3 的接口设计
→ 匹配：识别三者的重叠部分和互补部分
→ 合成：保留各自最优部分，消除冗余
→ 输出：重组后的代码 + 组件来源映射 + 协同评分
```

---

### 4.3 Refinement（精炼算子）

**定位**：风险感知的轨迹/内容优化器

**触发条件**：
- 完成一个长链路任务后需要精简执行路径
- 代码审查后需要去除冗余代码
- 文档审计后需要精简表述、去除冗余
- 用户要求"精简"某段内容

**输入规范**：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `candidate_content` | str | ✅ | — | 待精炼的内容 |
| `failure_patterns` | List[str] | ❌ | [] | 失败模式库（可选） |
| `risk_threshold` | float | ❌ | 0.3 | 风险阈值（0-1，低于此值视为安全） |
| `optimization_budget` | int | ❌ | 3 | 优化迭代次数 |
| `compress_output` | bool | ❌ | true | 是否压缩输出 |

**输出规范**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `refined_content` | str | 精炼后的内容 |
| `reduction_stats` | Dict | 缩减统计（原始长度、精炼后长度、缩减比例） |
| `risk_assessment` | RiskReport | 风险评估报告 |
| `optimization_log` | List[OptStep] | 优化过程日志 |
| `removed_redundancies` | List[str] | 被移除的冗余部分 |
| `replaced_risky_parts` | List[str] | 被替换的高风险部分 |

**执行流程**：
```
Step 1: [冗余检测] 识别冗余内容
  ├── 重复表述/重复调用
  ├── 可合并的连续步骤
  └── 不必要的中间状态

Step 2: [风险扫描] 对照失败模式库
  └── 检查是否触发已知失败模式
  └── 评估每一步/每段的风险等级

Step 3: [迭代优化] 多轮精炼（optimization_budget 轮）
  for i in range(optimization_budget):
      ├── 移除冗余
      ├── 替换高风险部分
      └── 验证内容完整性

Step 4: [压缩存储] 输出压缩后的内容
  └── 目标：50-80% 体积缩减
```

**Hermes 场景示例**：
```
用户：这份审计报告太长了，帮我精简到核心要点
→ Refinement 算子被触发
→ 冗余检测：发现重复的风险描述、冗余的引用
→ 风险扫描：对照审计失败模式库（遗漏关键风险、术语不一致等）
→ 迭代优化：3 轮精简，移除冗余，替换模糊表述
→ 输出：精简后的报告 + 缩减统计（原始 5000 字 → 精炼 2000 字，缩减 60%）
```

---

## 五、算子串联模式

### 模式 A：单算子（独立调用）

```
用户指令 → 单一算子 → 输出
```
适用：明确知道需要哪个算子的场景

### 模式 B：Revision → Refinement（修正后精简）

```
失败内容 → Revision → 修正内容 → Refinement → 精炼修正内容 → 输出
```
适用：代码修复、文档修正后需要精简表述

### 模式 C：Recombination → Refinement（合成后精简）

```
多个候选 → Recombination → 重组内容 → Refinement → 精炼重组内容 → 输出
```
适用：多方案融合后需要去除冗余

### 模式 D：完整闭环（Revision → Recombination → Refinement）

```
失败内容 → Revision → 修正方案
                    ↓
多个候选（含修正方案）→ Recombination → 重组内容
                                        ↓
                              Refinement → 精炼内容 → 输出
```
适用：复杂任务的完整进化流程

---

## 六、配置规范

### config/default.yaml

```yaml
# Revision 算子配置
revision:
  reflection_depth: 2              # 默认反思深度（1-3）
  generate_alternatives: true      # 是否生成替代方案
  alternative_count: 2             # 替代方案数量
  confidence_threshold: 0.6        # 低于此置信度时提示用户确认

# Recombination 算子配置
recombination:
  selection_criteria: quality      # 默认选择标准（quality / coverage / diversity）
  max_components: 5                # 最大组件数量
  detect_conflicts: true           # 是否检测冲突
  conflict_severity_threshold: 0.5 # 冲突严重程度阈值

# Refinement 算子配置
refinement:
  risk_threshold: 0.3              # 风险阈值（0-1）
  optimization_budget: 3           # 优化迭代次数
  compress_output: true            # 是否压缩输出
  target_reduction_ratio: 0.5      # 目标缩减比例（0.5 = 50%）

# 通用配置
common:
  llm_model: sensenova-6.7-flash-lite  # 算子执行使用的模型
  embedding_model: bge-large-zh-v1.5   # 语义匹配使用的 embedding 模型
  max_input_length: 8000               # 最大输入长度（token）
  output_format: markdown              # 输出格式（markdown / plain）
```

---

## 七、与现有 Skill 的协作关系

| 现有 Skill | 协作方式 | 场景 |
|------------|----------|------|
| `code-review` | Revision 作为 code-review 的修正引擎 | 代码审查发现 bug → 自动调用 Revision 生成修复方案 |
| `audit-methodology` | Refinement 作为审计报告的精简引擎 | 审计完成后 → 调用 Refinement 精简报告 |
| `deep-research` | Recombination 作为多源信息的融合引擎 | 多源研究结果 → 调用 Recombination 合成综合报告 |
| `kanban-orchestrator` | 三大算子作为 worker 的进化能力 | worker 执行失败 → 自动触发进化算子重试 |

---

## 八、实施优先级

| 优先级 | 算子 | 理由 | 预计工时 |
|--------|------|------|----------|
| P0 | Revision | 最核心、最常用，代码审查/文档修正的基础能力 | 2-3 小时 |
| P1 | Refinement | 与 Revision 天然配合，精简场景高频 | 1-2 小时 |
| P2 | Recombination | 依赖 embedding 模型，复杂度较高 | 2-3 小时 |

---

## 九、待验证问题

1. **算子间的上下文传递** — Revision 的输出如何作为 Recombination 的输入？需要定义统一的中间格式。
2. **置信度阈值如何设定** — 不同场景对置信度的要求不同，是否需要场景自适应阈值？
3. **失败模式库的维护** — Refinement 依赖失败模式库，这个库如何构建和更新？
4. **多语言支持** — 当前设计以中文为主，是否需要支持英文/多语言场景？
5. **性能基准** — 每个算子的平均执行时间和 token 消耗是多少？需要建立基准测试。

---

*本文档为设计阶段草案，未经实施验证。所有设计决策需在实际测试中确认。*
