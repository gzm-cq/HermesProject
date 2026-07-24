# HermesProject Code Wiki

> 版本: 1.0 | 最后更新: 2026-07-23
> 本文档为 HermesProject 的结构化代码百科，涵盖项目整体架构、模块职责、关键类与函数、依赖关系及运行方式。

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [五层记忆体系](#3-五层记忆体系)
4. [飞轮架构](#4-飞轮架构)
5. [插件系统](#5-插件系统)
6. [模块详解](#6-模块详解)
   - 6.1 [知识导航插件](#61-知识导航插件-knowledge-navigation)
   - 6.2 [知识树在线插件](#62-知识树在线插件-knowledge-tree-plugin)
   - 6.3 [知识树构建器](#63-知识树构建器-knowledge-tree-builder)
   - 6.4 [记忆聚类分析](#64-记忆聚类分析-clustering-analysis-v3)
   - 6.5 [记忆清理](#65-记忆清理-memory-cleanup)
   - 6.6 [自我进化研究](#66-自我进化研究-self-evolving)
   - 6.7 [AI 报告生成系统](#67-ai-报告生成系统-ai-report-system)
   - 6.8 [Draw.io 矢量图生成](#68-drawio-矢量图生成-drawio-generator)
   - 6.9 [Dream Synth](#69-dream-synth)
   - 6.10 [SkillOpt Runner](#610-skillopt-runner)
   - 6.11 [SkillOpt Sleep](#611-skillopt-sleep)
   - 6.12 [P0 Benchmark](#612-p0-benchmark)
   - 6.13 [Recall Eval](#613-recall-eval)
   - 6.14 [每日在线学习](#614-每日在线学习-daily-learn)
7. [Cron 定时任务系统](#7-cron-定时任务系统)
8. [部署系统](#8-部署系统)
9. [依赖关系图](#9-依赖关系图)
10. [项目运行方式](#10-项目运行方式)
11. [工程规范](#11-工程规范)
12. [外部服务依赖](#12-外部服务依赖)

---

## 1. 项目概述

| 属性 | 值 |
|------|------|
| **项目名称** | HermesProject |
| **定位** | Hermes 智能体平台的开发主仓库，包含 10+ 独立可部署子项目 |
| **核心目标** | 端到端 AI 助手系统：5 层记忆体系 + 技能强制注入 + 自进化飞轮 |
| **语言** | Python 3.10+ |
| **运行环境** | WSL2 (Ubuntu)，部署目标 `/root/.hermes/` |
| **源码位置** | `D:\HermesProject\`（Windows）/ `/mnt/d/HermesProject/`（WSL） |
| **许可证** | MIT |

**核心能力**：
- **知识导航**：LLM Router 三路注入（Hindsight 经验 + 知识树 + Skill），确保有效记忆主动召回
- **技能强制注入**：三级混合筛选（关键词预筛 → Embedding 精筛 → LLM 精排）
- **聚类分析**：优化 RAG 库结构，提升召回率
- **记忆清理**：精简核心记忆，控制 token 开销
- **自进化**：SE-Agent 三层进化算子 + 反思回路

---

## 2. 整体架构

```
═══════════════════ Session 初始化（仅一次） ═══════════════════

┌─────────────────────────────┐       ┌─────────────────────────────────┐
│ MEMORY.md / USER.md        │ 一次性 │ System Prompt 静态上下文        │
│ (经记忆清理精简后的核心记忆)│─注入─→│ 本次 session 全程不再变化       │
│ 只保留必要内容，控制 token  │       │ ─────────────────────────── │
└─────────────────────────────┘       │ 作为每条消息的固定基底上下文  │
                                        └────────────────┬────────────────┘
                                                         │
══════════════════ 每条消息处理（循环） ═════════════════════
                                                         │
┌──────────────────────────────────────────────────────────────────────────┐
│ Hermes Gateway                                                               │
│                                                                              │
│ 用户消息 ──→ 知识导航 (Hook pre_llm_call) ──→ LLM Router 决策 ──→ 按 mask 执行 ──→ 融合组装 ──→ LLM 调用 ──→ 响应 │
│                            │                         │             ↑               │
│                    ┌───────┴───────┐                  │             │               │
│                    ▼               ▼                  │             │               │
│           Hindsight recall   知识树 recall              │             │               │
│           （经验域 L4）     （知识域 L5）              │             │               │
│                    │               │                  │             │               │
│                    └───────┬───────┘                  │             │               │
│                            ▼                          │             │               │
│                    ┌──────────────┐                   │             │               │
│                    │  Skill 匹配   │────────────────────             │               │
│                    │  (第三路)     │ 自动注入 <auto_loaded_skills>   │               │
│                    │  三级混合筛选  │ 到用户消息                        │               │
│                    └──────────────┘                   │             │               │
└──────────────────────────┬───────────────────────────────┬──────────────────────────┘
                           │ 语义检索                       │
                           ▼                               │
┌────────────────────────────────────────────────────────┐│
│       Hindsight RAG 记忆库                             ││
│ 全量历史记忆向量化存储，支持语义检索                 ││
└─────────┬────────────────────┬────────────────────────┘│
          │                    │                          │
 ↑ 优化聚类结构          ↑ 降级写入                    │
          │                    │                          │
┌─────────▼─────────┐  ┌─────▼──────────────────────┐   │
│ 记忆聚类分析       │  │ 记忆清理                      │   │
│ HDBSCAN 向量聚类 │  │ LLM 分类 retain/remove/merge │   │
│ + 因果链检测      │  │ 必要保留→MEMORY/USER          │   │
│ → 提升 recall 率 │  │ → 减少 token 开销            │   │
└───────────────────┘  └──────────────────────────────┘   │
```

### 目录结构总览

```
HermesProject/
├── config/                  # 全局配置（common.yaml、gateway.yaml.example）
├── plugins/                 # Hermes 插件（需重启 gateway）
│   ├── knowledge-navigation/   # 知识导航插件（pre_llm_call 召回）
│   ├── knowledge-tree-plugin/  # 知识树在线插件（post_llm_call 增量学习）
│   └── hermes-plugin-template/ # 插件开发模板
├── scripts/                 # 独立脚本项目
│   ├── ai-report-system/       # AI 报告生成系统
│   ├── clustering-analysis-v3/ # 记忆聚类分析
│   ├── cron-wrappers/          # Cron shell wrapper 集合
│   ├── daily-learn/            # 每日在线学习
│   ├── drawio-generator/       # Draw.io 矢量图生成
│   ├── dream-synth/            # Dream Synth 梦境合成
│   ├── knowledge-tree-builder/ # 知识分域建树管线
│   ├── memory-cleanup/         # 记忆分类清理
│   ├── p0-benchmark/           # 性能基准测试
│   ├── recall-eval/            # 召回评估
│   ├── self-evolving/          # 自我进化研究
│   ├── skillopt-runner/        # SkillOpt 增量优化 runner
│   └── skillopt-sleep/         # SkillOpt-Sleep 优化引擎
├── deploy/                  # 一键部署系统
│   ├── deploy.sh            # 分发入口
│   ├── lib/common.sh        # 共享函数库
│   ├── projects/            # 各项目独立配置脚本
│   └── manifests/           # 文件级部署清单
├── docs/                    # 文档
└── .qoder/rules/            # 开发规范体系
```

---

## 3. 五层记忆体系

| 层级 | 名称 | 数据存储 | 生命周期 | 管理方式 |
|------|------|----------|----------|----------|
| L1 | Session DB | SQLite sessions | 会话级 | 自动，Hermes 内置 |
| L2 | MEMORY.md | `~/.hermes/memories/MEMORY.md` | 跨会话 | Agent 手动写 + 定期清理脚本 |
| L3 | USER.md | `~/.hermes/memories/USER.md` | 持久 | Agent 手动写 |
| L4 | **经验域** (Hindsight) | PG `memory_units` (pgvector) | 长期 | 自动 retain + 聚类增强 |
| L5 | **知识域** (知识树) | PG `knowledge_tree` (pgvector) | 长期 | 离线 builder + 在线 plugin |

### 双域对比

| 维度 | 经验域 (L4) | 知识域 (L5) |
|------|-------------|-------------|
| 数据源 | 对话过程中自动 retain | 精读笔记/审计报告/技术文章 |
| 数据结构 | 平铺语义向量 | 结构化二叉树（领域→科目→知识） |
| 知识分类 | 无分类，语义检索 | 五分类（原理/公式/要点/结论/方法） |
| 检索方式 | 三路 RRF（语义+BM25+实体图+因果链） | 关键词匹配 + cosine 语义 |
| 增强管道 | 聚类分析 + 因果链 + 实体挂靠 | 纠错回路（consolidate） |
| 写入入口 | Hindsight retain API | `knowledge-tree-builder run --merged` |

---

## 4. 飞轮架构

### 4.1 数据飞轮（知识飞轮）

核心闭环：**知识生产 → 知识组织 → 知识消费 → 闭环优化**

```
对话/任务 ──→ 知识导航插件（四路召回） ──→ LLM 输出
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

| 环节 | 项目 | 作用 |
|------|------|------|
| **知识生产** | 知识树构建器 + 知识树在线插件 + Hindsight retain | 从文档建树、增量提取、对话经验沉淀 |
| **知识组织** | 聚类分析 + 记忆清理 | HDBSCAN 聚类、因果链、LLM 分类 |
| **知识消费** | 知识导航插件 | LLM Router 四路召回注入上下文 |

### 4.2 能力飞轮

| 项目 | 作用 |
|------|------|
| SkillOpt Runner | 基于负反馈自动优化 skill 文档（调度入口） |
| SkillOpt Sleep | 训练引擎本体（rollout → reflect → revise 循环） |
| 自我进化研究 | SE-Agent 三层进化算子（Revision / Recombination / Refinement） |

### 4.3 Router 飞轮

```
用户消息 → LLM Router 决策 → 按 mask 条件执行四路召回 → LLM 输出
   ↑                                                     |
   |                                                     v
   |                                   Router 健康巡检 + 基线采集
   |                                                     |
   +---------- 下一轮决策更精准 ---------------------------+
```

---

## 5. 插件系统

### 5.1 插件架构

Hermes 插件通过 `plugin.yaml` 定义元数据和钩子配置，`main.py` 提供注册入口。

**核心概念**：
- **钩子机制**：`pre_llm_call`（LLM 调用前）和 `post_llm_call`（LLM 调用后）
- **入口点注册**：通过 `pyproject.toml` 的 `[project.entry-points."hermes.plugins"]` 声明
- **配置优先级**：环境变量 (ENV) > 项目 config/ 目录 > 代码默认值

### 5.2 plugin.yaml 格式

```yaml
name: plugin-id
version: "1.0.0"
description: "插件功能描述"
hooks:
  pre_llm_call:
    callback: pre_llm_call
    description: "钩子功能描述"
    enabled: true
dependencies:
  - requests>=2.25.0
```

### 5.3 插件注册函数

每个插件必须在 `main.py` 中实现 `register()` 函数：

```python
def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", pre_llm_call)
```

### 5.4 插件模板

`plugins/hermes-plugin-template/` 提供标准模板，包含：
- `plugin.yaml` — 插件元数据
- `main.py` — 注册入口函数
- `pyproject.toml` — 构建配置

---

## 6. 模块详解

### 6.1 知识导航插件 (knowledge-navigation)

> **路径**: `plugins/knowledge-navigation/`
> **类型**: 插件（需重启 hermes-gateway）
> **部署位置**: `/root/.hermes/plugins/knowledge-navigation/`

#### 职责

在每次 LLM 调用前通过 LLM Router 智能决策注入路径，实现三路召回：
- **H（经验域）**：从 Hindsight 召回相关记忆
- **KT（知识域）**：通过 knowledge-tree-plugin 召回知识树
- **S（能力域）**：Skill 三级混合筛选（关键词预筛 Top-30 → Embedding 精筛 Top-20 → LLM 精排 Top-3）

#### 核心目录结构

```
plugins/knowledge-navigation/
├── src/knowledge_navigation/
│   ├── __init__.py          # 插件注册入口
│   ├── cli.py               # CLI 调试工具
│   ├── client.py            # 兼容 shim → adapters.hindsight
│   ├── config.py            # KnowledgeNavigationConfig 配置类
│   ├── logger.py            # 日志模块
│   ├── adapters/
│   │   ├── hindsight.py     # Hindsight 客户端适配器
│   │   └── ...
│   └── core/
│       ├── hooks.py         # pre_llm_call 钩子实现
│       ├── filtering.py     # 召回结果过滤与格式化
│       ├── router.py        # LLM Router 路由决策
│       ├── skill_matcher.py # Skill 三级混合筛选
│       ├── circuit_breaker.py # 熔断器
│       └── recall_logger.py # 召回日志记录
├── config/                  # 评估查询配置
├── scripts/                 # 辅助脚本（基线采集、Skill 评估）
├── tests/                   # 测试套件
├── plugin.yaml              # 插件元数据
└── pyproject.toml
```

#### 关键类与函数

| 类/函数 | 文件 | 说明 |
|---------|------|------|
| `KnowledgeNavigationConfig` | `config.py` | 配置类，定义 Hindsight API、召回行为、性能参数等，支持 ENV 覆盖 |
| `pre_llm_call()` | `core/hooks.py` | 核心钩子，在 LLM 调用前执行四路召回逻辑 |
| `HindsightClient` | `adapters/hindsight.py` | Hindsight 服务客户端，封装 recall API 请求 |
| `SkillMatcher` | `core/skill_matcher.py` | Skill 三级混合筛选：关键词预筛 → Embedding 精筛 → LLM 精排 |
| `Router` | `core/router.py` | LLM Router 决策，返回 `{h, kt, s, sag}` mask，含三层 JSON 解析兜底 |
| `CircuitBreaker` | `core/circuit_breaker.py` | 熔断器，连续 3 次失败熔断 120s，状态持久化至 `circuit_breaker.json` |
| `filter_and_format()` | `core/filtering.py` | 召回后处理：去重、Compaction、HitCounter、时态衰减、跨域去重、XML 格式化 |
| `RecallLogger` | `core/recall_logger.py` | 统一召回日志，记录 source/count/latency_ms/score_stats |

#### 数据流

```
用户消息 → pre_llm_call() → 熔断器检查
  → Hindsight recall(经验域) + 知识树 recall(知识域) + Skill 匹配(能力域)
  → 排除标记记忆 → HitCounter → Compaction → 跨域去重(threshold=0.85)
  → 分数过滤(时态衰减) → XML <memory-context> 注入 → TaskTracker
  → 注入 user_message
```

#### 配置要点

| 参数 | 默认值 | 说明 |
|------|--------|------|
| Hindsight API URL | `http://127.0.0.1:9177` | Hindsight 服务地址 |
| 熔断器阈值 | 3 次连续失败 | 触发熔断 |
| 熔断恢复时间 | 120s | 半开状态探测 |
| 跨域去重阈值 | 0.85 | cosine 相似度去重 |
| Compaction 阈值 | 20 轮 | 超过后降为 1 条 |

---

### 6.2 知识树在线插件 (knowledge-tree-plugin)

> **路径**: `plugins/knowledge-tree-plugin/`
> **类型**: 插件（需重启 hermes-gateway）
> **部署位置**: `/root/.hermes/plugins/knowledge-tree-plugin/`

#### 职责

在 LLM 响应后自动增量学习，提取知识点并放入知识树；向知识导航插件提供 `recall_from_tree()` / `multi_hop_recall()` 公共 API。

#### 核心目录结构

```
plugins/knowledge-tree-plugin/
├── src/knowledge_tree_plugin/
│   ├── config.py            # 配置（数据库连接、模型设置、推理参数）
│   ├── hooks.py             # post_llm_call 钩子实现
│   ├── recall.py            # 召回机制（科目定位、注意力筛选、结果格式化）
│   ├── logger.py            # 日志模块
│   └── adapters/
│       └── database.py      # 数据库交互适配器
├── config/default.yaml      # 默认配置
├── tests/                   # 测试套件
├── plugin.yaml
└── pyproject.toml
```

#### 关键类与函数

| 类/函数 | 文件 | 说明 |
|---------|------|------|
| `post_llm_call()` | `hooks.py` | 核心钩子：分析对话 → 提取知识点 → 增量放置到知识树 |
| `should_skip_extraction()` | `hooks.py` | 轻量门控判断，减少无效提取 |
| `ExtractTask` | `hooks.py` | 提取任务数据结构，入队异步处理 |
| `recall_from_tree()` | `recall.py` | 公共 API：从知识树召回相关知识点 |
| `multi_hop_recall()` | `recall.py` | 实体多跳召回：沿 `kt_entity_links` 展开共享实体关联知识点 |
| `locate_subject()` | `recall.py` | 科目定位：关键词匹配 + embedding 余弦定位最相关科目节点 |
| `attention_filter()` | `recall.py` | 注意力筛选：Q×K^T / sqrt(d) 机制对知识点相关性打分 |
| `format_tree_results()` | `recall.py` | 格式化为 XML `<memory source="knowledge_tree">` 标签供 LLM 注入 |

#### 增量学习机制

```
LLM 响应 → post_llm_call() → 门控判断(should_skip_extraction)
  → 提取任务入队(ExtractTask) → 异步处理
  → 增量去重(cosine > 0.95 合并 source_ids)
  → 矛盾检测(cosine > 0.8 + 关键词对立 → review_queue)
  → K 向量 EMA 更新
```

---

### 6.3 知识树构建器 (knowledge-tree-builder)

> **路径**: `scripts/knowledge-tree-builder/`
> **类型**: 手动执行 / Cron
> **部署位置**: `/root/.hermes/scripts/knowledge-tree-builder/`

#### 职责

从文档批量构建知识树，支持 5 阶段全管线 + consolidate 周期维护。

#### 关键类与函数

| 类/函数 | 文件 | 说明 |
|---------|------|------|
| `app` (typer.Typer) | `cli.py` | CLI 入口，定义 `add`/`ingest`/`tree`/`find`/`move`/`edit`/`remove`/`merge`/`consolidate` 命令 |
| `models.py` | `models.py` | 数据库模型：节点、关系、实体等核心数据结构 |
| `Extractor` | `core/extractor.py` | LLM 知识提取器，从文本中提取实体和关系 |
| `Placement` | `core/placement.py` | 知识放置逻辑，将提取的实体放入知识树 |
| `Consolidation` | `core/consolidation.py` | 整合模块，确保知识树完整性和一致性 |

#### 5 阶段管线

| 阶段 | 名称 | 实现 | 说明 |
|------|------|------|------|
| Step 1 | LLM 提取 | `phase/scan.py` + `phase/analyze.py` | 提取 3-8 个关键知识点，区分原理/方法论 vs 配置/操作 |
| Step 1.5 | 准入过滤 | 规则驱动 | 4 条规则过滤，无 LLM |
| Step 2 | HDBSCAN 聚类 | `phase/split.py` | 递归 sub-clustering + 自动干跑迭代 |
| Step 3 | 结构判断 | `phase/split.py` | LLM 判断多叉/二叉结构 + 二分校验 |
| Step 4 | 节点命名 | `phase/admit.py` | LLM 命名（科目 4-8 字，知识点 2-8 字） |
| Step 5 | PG 写入 | `adapters/database.py` | 含增量去重 + 矛盾检测 |

#### Consolidate 维护阶段（Cron 周一 11:00）

| 阶段 | 动作 |
|------|------|
| 1 | 碎片 domain 合并（子节点 < 5） |
| 2 | 子科目拆分（HDBSCAN，> 50 触发） |
| 3 | confidence 更新 |
| 4 | 跨科建边（共现率 > 80%） |
| 5 | 超时审查项处理 |

#### CLI 命令

```bash
knowledge-tree-builder run --input-dir <目录> --merged -j 3   # 建树
knowledge-tree-builder backfill-k-vectors                       # k_vector 回填
knowledge-tree-builder redistribute                            # 领域重分类
knowledge-tree-builder consolidate run                          # 一键维护
```

---

### 6.4 记忆聚类分析 (clustering-analysis-v3)

> **路径**: `scripts/clustering-analysis-v3/`
> **类型**: Cron（周一 10:00）
> **部署位置**: `/root/.hermes/scripts/clustering-analysis-v3/`

#### 职责

对 Hermes 对话记忆进行向量嵌入 + HDBSCAN 语义聚类，自动识别主题簇 + 实体挂靠 + 因果关系链检测，优化 RAG 索引结构。

#### 核心目录结构

```
scripts/clustering-analysis-v3/
├── src/clustering_analysis/
│   ├── cli.py               # CLI 入口（typer）
│   └── config.py            # 配置管理
├── config/default.yaml      # 默认配置
├── scripts/                 # 辅助脚本（基线采集、cron wrapper、去重、治理）
└── tests/                   # 测试套件
```

#### 关键函数

| 函数 | 文件 | 说明 |
|------|------|------|
| `app` (typer.Typer) | `cli.py` | CLI 入口，支持 `--dry-run` 安全模式 |
| `AppConfig` | `config.py` | 配置类，管理聚类参数、Hindsight 连接等 |

#### 依赖

- `hdbscan` — HDBSCAN 聚类算法
- `pgvector` — PostgreSQL 向量操作
- `httpx` — Hindsight API 交互
- `numpy` / `scikit-learn` — 数值计算

---

### 6.5 记忆清理 (memory-cleanup)

> **路径**: `scripts/memory-cleanup/`
> **类型**: Cron（每日 13:00）
> **部署位置**: `/root/.hermes/scripts/memory-cleanup/`

#### 职责

LLM 驱动的智能记忆管理，对 MEMORY.md + USER.md 进行 6 类分类清理，控制 token 开销。

#### 核心目录结构

```
scripts/memory-cleanup/
├── src/memory_cleanup/
│   ├── cli.py               # CLI 入口，组织两阶段清理流程
│   ├── config.py            # AppConfig 配置类
│   ├── __main__.py          # python -m 入口
│   ├── core/
│   │   ├── classifier.py    # 分类器核心逻辑
│   │   ├── lifecycle.py     # 记忆生命周期管理
│   │   ├── prompts.py       # LLM prompt 模板
│   │   ├── reporter.py      # 报告输出
│   │   ├── utils.py         # 公共工具函数
│   │   └── verifier.py      # Phase 2 验证逻辑
│   └── adapters/
│       ├── llm_client.py    # LLM API 客户端
│       └── session_db.py    # SQLite 会话数据库
├── config/default.yaml
└── tests/
```

#### 关键类与函数

| 类/函数 | 文件 | 说明 |
|---------|------|------|
| `AppConfig` | `config.py` | 配置数据类，定义 LLM 参数、批处理大小、生命周期阈值等，支持 YAML + ENV 覆盖 |
| `run` / `main` | `cli.py` | CLI 主命令，组织两阶段流程：Phase 1 分类 → Phase 2 验证 |
| `classify_all()` | `core/classifier.py` | 并行分类函数，对记忆条目批量 LLM 分类 |
| `calc_remove_candidates()` | `core/classifier.py` | 候选分拣：根据分类结果确定 remove/merge/compress 候选 |
| `validate_merge_quality()` | `core/classifier.py` | 合并质量校验 |
| `validate_compress_quality()` | `core/classifier.py` | 压缩质量校验 |
| `detect_cold_memories()` | `core/lifecycle.py` | 检测冷记忆（长期未访问），基于启发式规则 |
| `detect_hot_memories()` | `core/lifecycle.py` | 检测高频记忆（高访问次数） |
| `phase2_verify()` | `core/verifier.py` | Phase 2 验证：通过 LLM 确认是否应移除/修正/保留 |
| `LLMClient` | `adapters/llm_client.py` | LLM 客户端，封装 HTTP 调用、JSON 解析、`classify_batch` / `verify_one` |
| `SessionDB` | `adapters/session_db.py` | SQLite 交互，提供 FTS 搜索和关键词重叠度计算 |
| `build_system_prompt()` | `core/prompts.py` | 根据源类型（MEMORY/USER）返回分类规则 prompt |
| `print_report()` | `core/reporter.py` | Phase 1 分类结果报告 |
| `print_v2_detail()` | `core/reporter.py` | Phase 2 验证详细报告 |

#### 两阶段清理流程

```
Phase 1（分类）: 读取 MEMORY.md/USER.md → 并行 LLM 批处理分类 → 6 类分类结果
  → retain / remove / merge / compress / hindsight / flagged

Phase 2（验证）: 对 remove 候选 → 查询 SessionDB 上下文 → LLM 验证
  → 确认移除 / 修正 / 保留

执行: --apply 才实际执行，默认 dry-run
```

---

### 6.6 自我进化研究 (self-evolving)

> **路径**: `scripts/self-evolving/`
> **类型**: 研究
> **部署位置**: `/root/.hermes/scripts/self-evolving/`

#### 职责

SE-Agent 自进化智能体算子研究，包含三大进化算子 + 反思回路。

#### 核心目录结构

```
scripts/self-evolving/
├── src/self_evolving/
│   ├── cli.py               # CLI 入口
│   ├── config.py            # 配置管理
│   ├── prompt_loader.py     # Prompt 加载器（支持热更新）
│   ├── adapters/
│   │   └── llm_client.py    # LLM 客户端适配器
│   ├── models/
│   │   ├── risk_assessment.py  # 风险评估模型
│   │   ├── trajectory.py       # 推理轨迹模型
│   │   └── failure_diagnosis.py # 故障诊断模型
│   ├── operators/
│   │   ├── refinement.py    # Refinement 算子
│   │   ├── revision.py      # Revision 算子
│   │   └── recombination.py # Recombination 算子
│   └── scripts/
│       ├── se_refine.py     # Refinement 执行脚本
│       ├── se_revision.py   # Revision 执行脚本
│       └── se_recombine.py  # Recombination 执行脚本
├── src/kanban_reflection/
│   └── core/reflector.py    # Kanban 反思子系统
├── config/default.yaml
├── config/prompts.yaml
└── tests/
```

#### 关键类与函数

| 类/函数 | 文件 | 说明 |
|---------|------|------|
| **Refinement** | `operators/refinement.py` | 风险感知优化器：扫描潜在风险 → 识别冗余 → 迭代优化内容 |
| **Revision** | `operators/revision.py` | 失败驱动策略生成器：故障类型识别 → 原因分析 → 替代方案生成 → 内容修正 |
| **Recombination** | `operators/recombination.py` | 跨轨迹知识合成器：提取可重用组件 → 语义匹配 → 冲突检测 → 最优组合生成 |
| `RiskLevel` / `RiskCategory` / `RiskReport` | `models/risk_assessment.py` | 风险评估数据结构 |
| `ToolCall` / `Step` / `Trajectory` | `models/trajectory.py` | 推理轨迹数据结构 |
| `FailureType` / `DiagnosisResult` / `AlternativeSolution` | `models/failure_diagnosis.py` | 故障诊断数据结构 |
| `LLMClient` | `adapters/llm_client.py` | 兼容 OpenAI API 格式的 LLM 服务客户端 |
| `PromptLoader` | `prompt_loader.py` | Prompt 加载器，支持外部化管理和热更新 |
| `Reflector` | `kanban_reflection/core/reflector.py` | Kanban 反思：失败分析 → 结构化反思结果 → 注入重试 prompt |

#### 算子串联模式

支持 B / D 两种串联模式，可通过 `python -m self_evolving.scripts.*` 调用。

---

### 6.7 AI 报告生成系统 (ai-report-system)

> **路径**: `scripts/ai-report-system/`
> **类型**: 手动执行
> **部署位置**: `/root/.hermes/scripts/ai-report-system/`

#### 职责

基于 Hermes 工具集的智能报告生成管线：多数据源分析 → DAG 并行执行 → 质量评估 → 多格式导出。

#### 核心目录结构

```
scripts/ai-report-system/
├── src/ai_report/export/
│   ├── chart_renderer.py    # 图表渲染器
│   └── docx_exporter.py     # DOCX 报告导出
├── scripts/
│   ├── export_docx.py       # DOCX 导出入口脚本
│   └── docx_comments.py     # DOCX 评论处理
├── tests/
└── pyproject.toml
```

#### 关键类与函数

| 类/函数 | 文件 | 说明 |
|---------|------|------|
| `DocxExporter` | `export/docx_exporter.py` | DOCX 报告导出：报告结构管理、样式管理、段落/表格/图片插入 |
| `ChartRenderer` | `export/chart_renderer.py` | 图表渲染：数据转换、图表生成、插入 DOCX |
| `export_docx()` | `scripts/export_docx.py` | CLI 入口：解析参数 → 调用导出流程 |

#### 特性

- 多数据源（Excel/CSV/API/Web 搜索）
- DAG 并行化执行
- 质量评估循环
- 多格式导出（Word/PDF/Markdown）
- StateGraph 工作流编排
- Mermaid 图表缓存（SHA256）
- VLM 视觉审查错误处理

---

### 6.8 Draw.io 矢量图生成 (drawio-generator)

> **路径**: `scripts/drawio-generator/`
> **类型**: 手动执行
> **部署位置**: `/root/.hermes/scripts/drawio-generator/`

#### 职责

根据规格说明自动生成 draw.io / SVG 矢量图，支持 CLI 和 Python API 调用。

#### 核心目录结构

```
scripts/drawio-generator/
├── src/drawio_generator/
│   ├── render.py            # 主渲染入口
│   ├── drawio_renderer.py   # DrawIO 格式渲染器
│   ├── svg_renderer.py      # SVG 格式渲染器
│   ├── layout.py            # 节点布局引擎（层级布局 + 连线路由）
│   ├── shapes.py            # 节点形状定义
│   ├── geometry.py          # 几何计算
│   ├── palettes.py          # 调色板配置
│   └── validator.py         # 输出校验器
├── config/default.yaml
└── tests/
```

#### 关键类与函数

| 类/函数 | 文件 | 说明 |
|---------|------|------|
| `render()` | `render.py` | 主渲染入口，根据格式选择 DrawIO 或 SVG |
| `DrawIORenderer` | `drawio_renderer.py` | DrawIO XML 格式渲染 |
| `SVGRenderer` | `svg_renderer.py` | SVG 格式渲染 |
| `LayoutEngine` | `layout.py` | 层级布局 + 连线路由 |
| `Validator` | `validator.py` | 输出合规校验 |

---

### 6.9 Dream Synth

> **路径**: `scripts/dream-synth/`
> **类型**: Cron
> **部署位置**: `/root/.hermes/scripts/dream-synth/`

#### 职责

梦境合成：从历史记忆中发现模式、评估显著性、合成新的知识组合。包含 pattern-discovery、promote-judge、significance-filter、synthesis 四个 prompt 阶段。

#### 核心文件

| 文件 | 说明 |
|------|------|
| `scripts/dream-daily.py` | 每日合成主入口，协调各阶段执行 |
| `scripts/dream-daily.sh` | Shell wrapper |
| `config.yaml` | 配置文件 |
| `prompts/` | 四阶段 prompt 模板 |

---

### 6.10 SkillOpt Runner

> **路径**: `scripts/skillopt-runner/`
> **类型**: Cron（每日 15:00）
> **部署位置**: `/root/.hermes/skillopt-runner/`

#### 职责

基于对话负反馈自动优化 skill 文档的调度入口，处理技能清单和运行配置。

#### 关键文件

| 文件 | 说明 |
|------|------|
| `skillopt_runner.py` | 主脚本：技能清单筛选、负反馈分析、调用 SkillOpt-Sleep 优化 |

---

### 6.11 SkillOpt Sleep

> **路径**: `scripts/skillopt-sleep/`
> **类型**: 独立引擎
> **部署位置**: `/root/.hermes/skillopt-sleep/`

#### 职责

SkillOpt 训练引擎本体，实现 rollout → reflect → revise 循环，支持多 benchmark（alfworld、docvqa、officeqa、searchqa、spreadsheetbench）。

#### 核心目录结构

```
scripts/skillopt-sleep/
├── skillopt/
│   ├── config.py            # 配置管理
│   ├── engine/trainer.py    # 训练引擎
│   ├── datasets/            # 数据集加载
│   └── envs/                # 各 benchmark 环境适配器
│       ├── alfworld/        # ALFWorld 环境
│       ├── docvqa/          # DocVQA 环境
│       ├── officeqa/        # OfficeQA 环境
│       └── searchqa/        # SearchQA 环境
├── scripts/
│   ├── train.py             # 训练脚本
│   └── eval_only.py         # 仅评估脚本
├── configs/                 # 各 benchmark 配置
├── plugins/                 # 插件（Claude Code、Codex、Copilot、OpenClaw）
└── data/                    # 数据集
```

---

### 6.12 P0 Benchmark

> **路径**: `scripts/p0-benchmark/`
> **类型**: 手动执行
> **部署位置**: `/root/.hermes/scripts/p0-benchmark/`

#### 职责

性能基准测试：去重基准、LLM 调用基准、Skill 匹配基准。

#### 核心目录结构

```
scripts/p0-benchmark/
├── src/p0_benchmark/
│   ├── cli.py               # CLI 入口
│   ├── config.py            # 配置管理
│   └── core/
│       ├── dedup_benchmark.py   # 去重基准测试
│       ├── llm_benchmark.py     # LLM 调用基准测试
│       └── skill_benchmark.py   # Skill 匹配基准测试
└── tests/
```

---

### 6.13 Recall Eval

> **路径**: `scripts/recall-eval/`
> **类型**: 手动执行
> **部署位置**: `/root/.hermes/scripts/recall-eval/`

#### 职责

召回质量评估：使用评估查询数据集评估知识导航的召回效果。

#### 核心目录结构

```
scripts/recall-eval/
├── src/recall_eval/
│   ├── cli.py               # CLI 入口
│   ├── config.py            # 配置管理
│   ├── adapters/llm_client.py  # LLM 客户端
│   └── core/
│       ├── dataset.py       # 评估数据集管理
│       ├── metrics.py       # 评估指标计算
│       └── runner.py        # 评估执行器
├── config/default.yaml
├── data/eval_queries.json   # 评估查询数据集
└── tests/
```

---

### 6.14 每日在线学习 (daily-learn)

> **路径**: `scripts/daily-learn/`
> **类型**: Cron（工作日 09:00）
> **部署位置**: `/root/.hermes/scripts/daily-learn/`

#### 职责

从 GitHub/ArXiv 自动采集知识入库，扩展知识库覆盖面。

---

## 7. Cron 定时任务系统

### 7.1 架构

使用 Hermes 内置 cron 调度，所有任务通过 shell wrapper 统一入口，共享 `cron_common.sh` 公共库。

```
cron_common.sh → flock 防重入 + 日志落盘 + 飞书通知 + 任务状态记录
     ↑
     ├── health-check-cron.sh          # 系统健康巡检
     ├── flywheel-health-report.sh     # 飞轮健康报告
     ├── kn-router-health-check.sh     # Router 健康巡检
     ├── knowledge-navigation-baseline.sh  # 知识导航基线采集
     ├── run-skill-eval.sh             # Skill 评估
     ├── memory-cleanup/daily_dryrun.sh  # 记忆清理
     ├── skillopt-runner/              # SkillOpt 增量优化
     └── daily-learn/daily_learn.sh    # 每日在线学习
```

### 7.2 Cron 公共库 (`cron_common.sh`)

提供以下机制：
- **flock 互斥**：防重入，同一任务不会并发执行
- **日志包装**：统一日志格式和落盘路径
- **飞书通知**：任务完成/失败时发送通知
- **任务状态记录**：记录执行时间和结果
- **补偿修复**：`cron-catchup-repair.sh` 处理因停机遗漏的任务

### 7.3 定时任务清单

| 任务 | 时间（北京时间） | 脚本 | 说明 |
|------|------------------|------|------|
| 系统健康巡检 | 工作日 08:00 | `health-check-cron.sh` | 3-tier 架构巡检 + 飞书告警 |
| 每日在线学习 | 工作日 09:00 | `daily-learn/daily_learn.sh` | GitHub/ArXiv 知识采集 |
| 聚类分析 | 周一 10:00 | `clustering-analysis-cron.sh` | HDBSCAN 聚类 + 因果链 |
| 知识树 consolidate | 周一 11:00 | `knowledge-tree-consolidate.sh` | 知识树周期维护 |
| 知识导航基线 | 每日 12:00 | `knowledge-navigation-baseline.sh` | 基线采集 + 退化检测 |
| Skill Eval 评估 | 每日 12:00 | `run-skill-eval.sh` | Skill 匹配质量评估 |
| 记忆清理 | 每日 13:00 | `memory-cleanup/daily_dryrun.sh` | LLM 分类清理 |
| Router 健康巡检 | 每日 14:00 | `kn-router-health-check.sh` | Router 解析失败率/稳定性检查 |
| SkillOpt 增量优化 | 每日 15:00 | `skillopt-nightly-run.sh` | Skill 文档自动优化 |

### 7.4 飞轮健康报告

`flywheel-health-report.py` 从多数据源读取信息，分析各飞轮模块状态：

- **数据源**：trace.log、cron-state、baselines
- **报告内容**：SAG 熔断器状态、Router 健康度、召回指标、记忆清理指标、7 天趋势
- **输出**：飞书通知 + Markdown 报告

---

## 8. 部署系统

### 8.1 架构

三层架构：

```
deploy/deploy.sh              # 分发入口（list / plan / deploy / rollback / history / cleanup）
    ↓
deploy/projects/<project>.sh  # 各项目独立配置（源路径、目标路径、服务名、清理规则）
    ↓
deploy/lib/common.sh          # 共享函数库（备份/回滚/清单展开/防残留/技能部署）
```

### 8.2 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| **deploy.sh** | `deploy/deploy.sh` | 分发入口，接收子命令，调用对应项目脚本 |
| **common.sh** | `deploy/lib/common.sh` | 共享函数库：备份、回滚、清单展开、防残留、Skill 部署/回滚、服务重启 |
| **项目配置** | `deploy/projects/<project>.sh` | 定义源目录、目标目录、重启服务、Skill 部署路径、文件级清单路径 |
| **文件清单** | `deploy/manifests/<project>.manifest` | glob 模式定义包含/排除清单 |

### 8.3 部署命令

```bash
./deploy/deploy.sh list                          # 列出可部署项目
./deploy/deploy.sh plan <project>                # 预览将部署的文件（不动文件系统）
./deploy/deploy.sh deploy <project>              # 一键部署（文件级备份 + 部署）
./deploy/deploy.sh rollback <project>            # 回滚（先清残留再还原备份）
./deploy/deploy.sh history <project>             # 查看部署历史
./deploy/deploy.sh cleanup <project>             # 清理旧备份
```

### 8.4 部署目标与重启

| 项目 | 运行时位置 | 重启服务 |
|------|------------|---------|
| knowledge-navigation | `/root/.hermes/plugins/knowledge-navigation/` | `hermes-gateway.service` |
| knowledge-tree-plugin | `/root/.hermes/plugins/knowledge-tree-plugin/` | `hermes-gateway.service` |
| 其他脚本项目 | `/root/.hermes/scripts/<project>/` | — |

### 8.5 设计原则

- **文件级备份**：部署前自动备份目标文件，支持回滚
- **防残留**：清理目标目录中不在清单内的旧文件
- **Skill 同步**：自动将 `skills/` 同步至 `/root/.hermes/skills/`
- **服务重启**：插件部署后自动重启 hermes-gateway
- **MD5 校验**：部署后验证文件完整性

---

## 9. 依赖关系图

### 9.1 模块间依赖

```
                    ┌──────────────────────┐
                    │   Hermes Gateway     │
                    │   (端口 8642)        │
                    └──────┬───────┬───────┘
                           │       │
                    ┌──────▼──┐ ┌──▼──────────┐
                    │ knowledge│ │ knowledge-  │
                    │ navigation│ │ tree-plugin │
                    │ (pre_llm) │ │ (post_llm)  │
                    └──┬───┬──┘ └──┬──────────┘
                       │   │       │
              ┌────────▼┐ ┌▼────┐ ┌▼──────────────┐
              │Hindsight │ │ KT  │ │ KT Builder     │
              │ (L4)    │ │ (L5)│ │ (离线建树)     │
              └────┬────┘ └──┬──┘ └───────────────┘
                   │         │
         ┌─────────▼──┐  ┌──▼──────────┐
         │ clustering  │  │ memory-     │
         │ analysis    │  │ cleanup     │
         └────────────┘  └─────────────┘

         ┌──────────────┐  ┌──────────────┐
         │ skillopt-    │  │ self-        │
         │ runner/sleep │  │ evolving     │
         └──────────────┘  └──────────────┘
```

### 9.2 外部服务依赖

| 服务 | 端口 | 使用者 |
|------|------|--------|
| Hindsight RAG | 9177 | knowledge-navigation (H 路召回) |
| shared-postgres | 5434 | knowledge-navigation、knowledge-tree-plugin/builder、clustering-analysis、Hindsight |
| LiteLLM | 4142 | 所有需要 LLM 的模块（统一模型网关） |
| Hermes Gateway | 8642 | 插件运行时 |
| 飞书 API | — | cron 通知、self-evolving 反思 |

### 9.3 Python 包依赖矩阵

| 模块 | 关键依赖 |
|------|----------|
| knowledge-navigation | `requests`, `httpx`, `typer`, `psycopg2-binary`, `numpy` |
| knowledge-tree-plugin | `psycopg2-binary`, `numpy`, `httpx` |
| knowledge-tree-builder | `typer`, `psycopg2-binary`, `hdbscan`, `numpy`, `scikit-learn` |
| clustering-analysis-v3 | `hdbscan`, `pgvector`, `httpx`, `numpy`, `scikit-learn` |
| memory-cleanup | `typer`, `httpx`, `pyyaml` |
| self-evolving | `typer`, `httpx`, `pyyaml` |
| ai-report-system | `python-docx`, `matplotlib`, `Pillow` |
| drawio-generator | `typer`, `pyyaml` |
| dream-synth | `httpx`, `pyyaml` |
| recall-eval | `typer`, `httpx`, `pyyaml` |
| p0-benchmark | `typer`, `httpx`, `numpy` |

---

## 10. 项目运行方式

### 10.1 环境要求

- Python 3.10+
- WSL2 (Ubuntu)
- Hermes 运行时已部署在 `/root/.hermes/`
- PostgreSQL (shared-postgres, 端口 5434)
- LiteLLM (端口 4142)
- Hindsight RAG (端口 9177)

### 10.2 安装子项目（开发模式）

```bash
# 主项目
cd /mnt/d/HermesProject && pip install -e .

# 各子项目
cd scripts/ai-report-system && pip install -e .
cd scripts/clustering-analysis-v3 && pip install -e .
cd scripts/drawio-generator && pip install -e .
cd scripts/knowledge-tree-builder && pip install -e .
cd scripts/memory-cleanup && pip install -e .
cd plugins/knowledge-navigation && pip install -e .
cd plugins/knowledge-tree-plugin && pip install -e .
```

### 10.3 运行测试

```bash
# 各项目测试
cd scripts/ai-report-system && pip install -e . && pytest
cd scripts/clustering-analysis-v3 && pip install -e . && pytest
cd scripts/drawio-generator && pip install -e . && pytest
cd scripts/knowledge-tree-builder && pip install -e . && pytest
cd scripts/memory-cleanup && pip install -e . && pytest
cd plugins/knowledge-navigation && pip install -e . && pytest

# 排除集成测试
pytest -m "not integration"

# 覆盖率检查
pytest --cov=package_name --cov-report=term-missing --cov-fail-under=80
```

### 10.4 CLI 入口

| 模块 | 命令 | 说明 |
|------|------|------|
| knowledge-tree-builder | `knowledge-tree-builder run --input-dir <dir> --merged -j 3` | 批量建树 |
| knowledge-tree-builder | `knowledge-tree-builder consolidate run` | 周期维护 |
| memory-cleanup | `memory-cleanup run --dry-run` | 记忆清理（默认 dry-run） |
| memory-cleanup | `memory-cleanup run --apply` | 记忆清理（实际执行） |
| clustering-analysis | `clustering-analysis run --dry-run` | 聚类分析（安全模式） |
| drawio-generator | `drawio-generator render <spec>` | 生成矢量图 |
| p0-benchmark | `p0-benchmark run` | 性能基准测试 |
| recall-eval | `recall-eval run` | 召回评估 |
| self-evolving | `python -m self_evolving.scripts.se_refine` | Refinement 算子 |
| self-evolving | `python -m self_evolving.scripts.se_revision` | Revision 算子 |
| self-evolving | `python -m self_evolving.scripts.se_recombine` | Recombination 算子 |

### 10.5 部署流程

```
1. 修改源码 — 在 D:\HermesProject 修改对应子项目
2. 本地测试 — 运行项目级测试确认修改正确
3. 代码审查 — 将修改提给用户审查，等待确认
4. 修正迭代 — 根据审查意见修正，直至用户确认通过
5. 部    署 — 用户确认后，通过 deploy.sh 部署到运行环境
```

```bash
# 部署示例
cd /mnt/d/HermesProject
./deploy/deploy.sh deploy knowledge-navigation  # 自动重启 hermes-gateway
./deploy/deploy.sh deploy memory-cleanup
./deploy/deploy.sh rollback <project>            # 回滚
```

---

## 11. 工程规范

### 11.1 项目结构

所有子项目统一采用 **src layout**：

```
project-name/
├── src/{package_name}/        # 源码包
│   ├── __init__.py            # 干净的 __all__ 导出
│   ├── cli.py                 # CLI 入口（typer）
│   ├── config.py              # 配置管理（Dataclass + ENV 覆盖）
│   ├── core/                  # 核心业务逻辑（禁止直接依赖外部 I/O）
│   └── adapters/              # 外部集成适配层
├── tests/                     # 测试代码
├── config/                    # 运行时配置文件（YAML/JSON）
├── pyproject.toml
└── README.md
```

### 11.2 技术栈规范

| 类别 | 选型 | 版本 |
|------|------|------|
| Python | >=3.10 | 最低 3.10 |
| CLI 框架 | `typer>=0.9.0` | 统一，禁止 argparse/click |
| 配置管理 | `dataclass` + ENV 覆盖 | 优先级：ENV > config file > default |
| 测试 | `pytest>=7.0` + `pytest-cov>=4.0` | 覆盖率目标 80%+ |
| Lint | `ruff>=0.1.0` | 替代 flake8/isort |
| Format | `black>=23.0` | line-length=100 |

### 11.3 命名规范

| 元素 | 规范 | 示例 |
|------|------|------|
| Python 代码 | snake_case | `knowledge_navigation` |
| Shell 脚本 | kebab-case | `flywheel-health-report.sh` |
| 配置文件 | kebab-case | `default.yaml` |
| Git 提交 | `type(scope): 中文描述` | `feat(kn): 新增 SAG 熔断器` |

### 11.4 Git 提交规范

```
<type>(<scope>): <中文描述>

type: feat | fix | refactor | docs | chore | style
scope: kn | kt | mem | cluster | deploy | report | skillopt | evo | cron | drawio | dream | recall | bench
描述: ≤ 72 字中文
```

### 11.5 核心架构约束

- 5 层记忆体系边界不可逾越
- 模块边界隔离：`core/` 不得直接依赖外部 I/O
- compat 兼容层：旧接口通过 shim 指向新实现
- 部署统一入口：仅通过 `deploy/deploy.sh` 执行
- 禁止直接修改 `/root/.hermes/` 运行时文件

---

## 12. 外部服务依赖

### 12.1 服务清单

| 组件 | 版本 | 端口 | 用途 |
|------|------|------|------|
| Hermes Agent Gateway | latest | 8642 | 插件运行时、消息路由 |
| Hindsight Daemon | latest | 9177 | 深度记忆服务（RAG） |
| Axiom Wiki MCP SSE | latest | 4143 | 结构化知识库（MCP） |
| LiteLLM | 1.83.14 | 4142 | 统一模型网关 |
| shared-postgres | 15 | 5434 | Hindsight + LiteLLM + 知识树 |
| Dify | 0.10.0 | 80/443 | 业务前端 RAG 平台 |

### 12.2 模型路由配置

| 用途 | 模型 | 提供商 |
|------|------|--------|
| 主对话模型 | sensenova-6.7-flash-lite | SenseNova |
| Hindsight LLM | s-deepseek-v4-flash | DeepSeek via SiliconFlow |
| Embedding | BAAI/bge-m3 | SiliconFlow |
| Reranker | BAAI/bge-reranker-v2-m3 | SiliconFlow |

### 12.3 数据库核心表

| 表 | 数据库 | 用途 |
|----|--------|------|
| `memory_units` | shared-postgres | Hindsight 记忆单元 |
| `memory_links` | shared-postgres | 因果链接 |
| `unit_entities` | shared-postgres | 实体映射 |
| `knowledge_point` | shared-postgres | 知识树知识点 |
| `kt_entity_links` | shared-postgres | 知识树实体关联（多跳） |
| `litellm_model` | shared-postgres | LiteLLM 模型配置 |
| `litellm_spend` | shared-postgres | Token 消费记录 |

### 12.4 关键端口映射

| 端口 | 服务 | 协议 |
|------|------|------|
| 8642 | Hermes Gateway | HTTP |
| 4142 | LiteLLM | HTTP |
| 4143 | Axiom Wiki MCP | HTTP SSE |
| 5434 | shared-postgreSQL | TCP |
| 9177 | Hindsight API | HTTP |
| 8000 | Windows-MCP | TCP SSE |

---

> 本文档基于 HermesProject 仓库代码自动生成，如需更新请重新运行分析。
