# 自进化智能体研究项目

> **创建时间**：2026-05-29  
> **研究方法**：Deep Research + Kanban 多智能体并行研究  
> **状态**：Phase A 部署完成（标记排除 + 时态衰减 + 评估基线 + MMR + CE压缩 + 因果链）

---

## 项目概述

本项目系统研究了自进化智能体（Self-Evolving Agents）领域的三个代表性工作：

| 框架 | 机构 | 核心贡献 |
|------|------|---------|
| **SEAL** | 西湖大学 + 蚂蚁集团 | Agent-Environment 协同进化，提出"智能体-环境失配"问题 |
| **SE-Agent** | QuantaAlpha | 轨迹级自进化，三大算子（Revision/Recombination/Refinement） |
| **CoEvolve** | AMAP-ML（高德地图） | Agent-Data 协同进化，失败信号→任务合成→分布更新 |

---

## 目录结构

```
scripts/self-evolving/
├── src/self_evolving/            # 主源码（自进化算子实现）
│   ├── __init__.py
│   ├── adapters/                 # LLM 客户端适配
│   │   └── llm_client.py
│   ├── operators/                # 三大算子核心实现
│   │   ├── __init__.py
│   │   ├── revision.py           # Revision 算子
│   │   ├── recombination.py      # Recombination 算子
│   │   └── refinement.py         # Refinement 算子
│   ├── models/                   # 数据模型
│   │   ├── __init__.py
│   │   ├── trajectory.py         # 轨迹数据模型
│   │   └── risk_assessment.py    # 风险评估模型
│   ├── scripts/                  # CLI 入口
│   │   ├── se_revision.py        # Revision CLI
│   │   ├── se_recombine.py       # Recombination CLI
│   │   └── se_refine.py          # Refinement CLI
│   └── prompt_loader.py          # 提示词加载
├── src/kanban_reflection/        # Kanban 反思回路子包
│   ├── __init__.py
│   ├── cli.py                    # kanban-reflect CLI
│   ├── config.py
│   ├── adapters/llm_client.py
│   └── core/reflector.py         # 失败任务反思分析
├── scripts/                      # 运维/验证脚本
│   ├── self_evolving_driver.py   # 夜间自进化驱动
│   ├── kanban_reflect_hook.py    # Kanban 反思钩子
│   ├── skill_patch.py            # SKILL.md 自动回写
│   └── _make_trace.py / _scan_quality.py / _verify_kn.py
├── config/                       # 默认配置
│   ├── default.yaml
│   ├── eval_queries.yaml
│   └── prompts.yaml
├── tests/                        # 测试
│   └── test_operators.py
├── docs/                         # 项目文档
├── references/                   # 只读研究文献
├── skills/                       # Skill 文件
│   └── se-agent-evolution/
│       └── SKILL.md
├── pyproject.toml                # 构建配置
└── README.md                     # 本文件
```

---

## 核心发现

### 1. 自进化 Agent 三大分类

| 分类 | 核心思想 | 代表工作 |
|------|---------|---------|
| Type 1: Self-Improving | 模型自身能力迭代 | RefineLM, Self-Instruct |
| Type 2: Self-Organizing | 多智能体自组织协同 | CAMEL, AutoGen |
| Type 3: Self-Evolving | 环境与 Agent 共同进化 | SEAL, SE-Agent, CoEvolve |

### 2. 三层协同进化互补

| 框架 | 进化界面 | 互补价值 |
|------|---------|---------|
| SEAL | 训练环境（粗粒度） | 提供"舞台" |
| CoEvolve | 数据分布（细粒度） | 提供"内容" |
| SE-Agent | 推理轨迹 | 提供"策略" |

### 3. 四层进化闭环（融合框架）

```
第 4 层：环境进化（SEAL）→ 优化任务界面
第 3 层：分布进化（CoEvolve）→ 优化训练数据
第 2 层：轨迹进化（SE-Agent）→ 优化推理策略
第 1 层：策略进化（RL/GRPO）→ 优化模型参数
```

---

## CLI 用法

安装后可用三个 CLI 命令：

```bash
# 安装
pip install -e .

# Revision: 失败驱动的策略生成
se-revision --failed-content "..." --context "..." [--failure-type argument_mismatch]

# Recombination: 跨轨迹知识合成
se-recombine --candidates "content1" "content2" --context "..."

# Refinement: 风险感知的内容优化
se-refine --content-file document.md --risk-threshold 0.3
```

也可通过模块方式运行：
```bash
python -m self_evolving.scripts.se_revision --help
python -m self_evolving.scripts.se_recombine --help
python -m self_evolving.scripts.se_refine --help
```

### Kanban 反思回路（kanban_reflection）

分析 Kanban 失败任务 trace 并输出结构化反思结果（夜间 `self-evolving-nightly.sh` 调用）：

```bash
# 分析失败原因
kanban-reflect analyze --task-id <id> --trace <path> [--goal <goal>] [-o result.json]

# 仅预览 trace 内容，不调用 LLM
kanban-reflect analyze --task-id <id> --trace <path> --dry-run

# 列出支持的失败类型（SEAL 6 类）
kanban-reflect list-types
```

## 算子串联模式

### 模式 B: Revision → Refinement
```bash
se-revision --failed-content "..." --context "..." -o json > revision.json
# 提取修正内容并精炼
se-refine --content "$(jq -r '.revised_content' revision.json)" --context "Refine the corrected content"
```

### 模式 D: 完整闭环
```bash
# Revision → Recombination → Refinement
se-revision --failed-content "..." --context "..." -o json > revision.json
se-recombine --candidates-file alternatives.txt --context "..." -o json > recombine.json
se-refine --content-file /dev/stdin --context "Final refinement"
```

## 相关资源

- SEAL 论文：https://arxiv.org/abs/2605.24426
- SE-Agent 论文：https://arxiv.org/abs/2508.02085
- CoEvolve 论文：https://arxiv.org/abs/2604.15840
- Awesome-Self-Evolving-Agents 综述：https://github.com/XMUDeepLIT/Awesome-Self-Evolving-Agents
- SEAL GitHub：https://github.com/yihaohu0118/SEAL
- SE-Agent GitHub：https://github.com/JARVIS-Xs/SE-Agent
- CoEvolve GitHub：https://github.com/AMAP-ML/CoEvolve

---

*项目由 Hermes Agent 使用 Kanban 多智能体并行研究 + Deep Research 方法创建。*
