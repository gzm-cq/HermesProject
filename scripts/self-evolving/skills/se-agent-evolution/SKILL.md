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

# SE-Agent Evolution Operators Skill

将 SE-Agent 的三大进化算子（Revision / Recombination / Refinement）封装为 Hermes Skill，
使其成为可复用、可组合的进化能力组件。

## 核心概念

| 算子 | 定位 | 触发条件 | 输出 |
|:---:|:---|:---|:---|
| **Revision** | 失败驱动的策略生成器 | 执行失败、需要修正 | 修正内容 + 根本原因 + 3 个替代方案 |
| **Recombination** | 跨轨迹知识合成器 | 多方案融合、并行结果合并 | 重组内容 + 组件来源映射 + 协同评分 |
| **Refinement** | 风险感知的优化器 | 长链路精简、冗余去除 | 精炼内容 + 缩减统计 + 风险评估 |

## 快速开始

### 1. Revision 算子（修正）

```bash
# 基本用法
python scripts/se_revision.py --failed-content "def foo(x): return x + 'hello'" \
    --context "String concatenation with int causes TypeError"

# 指定失败类型
python scripts/se_revision.py --failed-content-file buggy_code.py \
    --context "Fix the bug" \
    --failure-type argument_mismatch \
    --reflection-depth 3

# JSON 输出（用于程序调用）
python scripts/se_revision.py --failed-content "..." --context "..." --output-format json
```

**输入参数**：
- `--failed-content` / `-c`: 失败的内容（代码/轨迹/文档）
- `--failed-content-file` / `-f`: 从文件读取失败内容
- `--context` / `-k`: 任务上下文（必填）
- `--failure-type` / `-t`: 失败类型（auto / invalid_tool_call / argument_mismatch / state_mismatch / recovery_failure / missing_tool_call / response_mismatch）
- `--reflection-depth` / `-d`: 反思深度（1/2/3）
- `--no-alternatives`: 不生成替代方案
- `--alternative-count`: 替代方案数量

**输出**：
- 诊断结果（失败类型、置信度、直接原因、根本原因）
- 修正后的内容
- 3 个替代方案（直接修复/正交方案/保守方案）
- 置信度评分

### 2. Recombination 算子（重组）

```bash
# 从文件合并
python scripts/se_recombine.py --candidates impl_v1.py impl_v2.py impl_v3.py \
    --context "Merge these three implementations into optimal version"

# 从文件列表
python scripts/se_recombine.py --candidates-file candidate_files.txt \
    --context "..." \
    --criteria quality

# 文本输入
python se_recombine.py --candidates "content1" "content2" \
    --context "..." \
    --max-components 3

# JSON 输出
python se_recombine.py --candidates "..." "..." --context "..." --output-format json
```

**输入参数**：
- `--candidates` / `-c`: 候选内容列表（空间分隔）
- `--candidates-file` / `-f`: 候选文件路径列表（每行一个）
- `--context` / `-k`: 任务上下文（必填）
- `--criteria`: 选择标准（quality / coverage / diversity）
- `--max-components`: 最大组件数量
- `--no-conflict-detection`: 禁用冲突检测

**输出**：
- 重组后的内容
- 组件来源映射
- 协同效应评分（>0 表示 1+1>2）
- 冲突日志
- 保留/替换的组件列表

### 3. Refinement 算子（精炼）

```bash
# 基本用法
python scripts/se_refine.py --content-file long_report.md \
    --context "Condense this report to key points"

# 自定义风险阈值
python se_refine.py --content-file code.py \
    --risk-threshold 0.2 \
    --iterations 5

# 使用失败模式库
python se_refine.py --content-file document.md \
    --failure-patterns-file failure_patterns.txt

# JSON 输出
python se_refine.py --content "..." --output-format json
```

**输入参数**：
- `--content` / `-c`: 待精炼内容
- `--content-file` / `-f`: 从文件读取
- `--context` / `-k`: 任务上下文
- `--risk-threshold`: 风险阈值（0-1）
- `--iterations` / `-i`: 优化迭代次数
- `--target-reduction`: 目标缩减比例（0-1）
- `--no-compress`: 禁用输出压缩
- `--failure-patterns-file`: 失败模式库文件

**输出**：
- 精炼后的内容
- 缩减统计（原始长度、精炼后长度、缩减比例）
- 风险评估报告
- 移除的冗余列表
- 替换的高风险部分
- 优化过程日志

## 算子串联模式

### 模式 B: Revision → Refinement

```bash
# 先修正，再精简
python scripts/se_revision.py --failed-content "..." --context "..." -o json > revision_result.json

# 提取修正内容并精炼
python scripts/se_refine.py --content "$(jq -r '.revised_content' revision_result.json)" \
    --context "Refine the corrected content"
```

### 模式 D: 完整闭环

```bash
# Revision → Recombination → Refinement
# 1. Revision
python scripts/se_revision.py --failed-content "..." --context "..." -o json > revision.json

# 2. Recombination (合并修正方案与其他候选)
jq -r '.alternatives[].content' revision.json > alternatives.txt
echo "..." >> alternatives.txt  # 添加其他候选
python scripts/se_recombine.py --candidates-file alternatives.txt --context "..." -o json > recombine.json

# 3. Refinement
jq -r '.recombined_content' recombine.json | \
    python scripts/se_refine.py --content-file /dev/stdin --context "Final refinement"
```

## 配置说明

配置文件位于 `config/default.yaml`：

```yaml
# Revision 配置
revision:
  reflection_depth: 2
  generate_alternatives: true
  alternative_count: 2
  confidence_threshold: 0.6

# Recombination 配置
recombination:
  selection_criteria: quality
  max_components: 5
  detect_conflicts: true
  conflict_severity_threshold: 0.5

# Refinement 配置
refinement:
  risk_threshold: 0.3
  optimization_budget: 3
  compress_output: true
  target_reduction_ratio: 0.5

# 通用配置
common:
  llm_model: sensenova-6.7-flash-lite
  embedding_model: bge-large-zh-v1.5
  output_format: markdown
```

## 与现有 Skill 协作

| 现有 Skill | 协作方式 |
|:---|:---|
| `code-review` | Revision 作为修正引擎 |
| `audit-methodology` | Refinement 作为精简引擎 |
| `deep-research` | Recombination 作为多源融合引擎 |
| `kanban-orchestrator` | 三大算子作为 worker 进化能力 |

## 目录结构

```
se-agent-evolution/
├── SKILL.md                    # 本文件
├── operators/
│   ├── __init__.py
│   ├── revision.py             # Revision 算子核心实现
│   ├── recombination.py        # Recombination 算子核心实现
│   └── refinement.py           # Refinement 算子核心实现
├── models/
│   ├── __init__.py
│   ├── trajectory.py           # 轨迹数据模型
│   ├── failure_diagnosis.py    # 失败诊断模型
│   └── risk_assessment.py      # 风险评估模型
├── scripts/
│   ├── se_revision.py          # Revision CLI 入口
│   ├── se_recombine.py         # Recombination CLI 入口
│   └── se_refine.py            # Refinement CLI 入口
├── config/
│   └── default.yaml            # 默认配置
└── references/
    └── (研究文献)
```

## 失败类型定义

| 类型 | 说明 | 适用场景 |
|:---|:---|:---|
| `invalid_tool_call` | 工具调用格式/名称错误 | 工具调用失败 |
| `argument_mismatch` | 参数类型/格式不匹配 | API 调用、函数参数 |
| `state_mismatch` | 状态与预期不一致 | 多步任务状态跟踪 |
| `recovery_failure` | 错误恢复失败 | 异常处理逻辑 |
| `missing_tool_call` | 遗漏必要的工具调用 | 步骤缺失 |
| `response_mismatch` | 输出与预期不符 | 结果验证失败 |

## 注意事项

1. **LLM 集成**：当前实现为框架骨架，实际 LLM 调用部分需要接入 sensenova 或其他 LLM API
2. **Embedding 模型**：Recombination 的语义匹配依赖 embedding 模型，需配置 SiliconFlow API
3. **失败模式库**：Refinement 的失败模式库需要手动维护或通过进化过程自动积累
4. **置信度阈值**：低于 `confidence_threshold` 时建议用户确认

## 开发计划

- [ ] Revision: 接入 LLM 生成实际修正内容
- [ ] Recombination: 接入 embedding 模型进行语义匹配
- [ ] Refinement: 完善风险扫描规则库
- [ ] 串联模式: 实现自动化的算子串联工作流
- [ ] Kanban 集成: 作为 worker 的进化能力组件
