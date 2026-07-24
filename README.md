# HermesProject

> Hermes 智能体平台 — AI 报告生成 + 记忆聚类 + 知识导航 + 图表生成 + 记忆清理 + 自我进化研究，统一管理、一键部署。

## 项目概览

HermesProject 是 Hermes 智能体平台的**开发主仓库**，包含 10+ 独立可部署的子项目，共同支撑端到端的 AI 助手系统：

| # | 项目 | 路径 | 类型 | 简介 |
|---|------|------|------|------|
| 1 | **知识导航插件** | `plugins/knowledge-navigation/` | 插件 | LLM Router 三路注入（Hindsight 经验 + 知识树 + Skill），pre_llm_call 智能召回，Skill 三级混合筛选（关键词预筛→Embedding精筛→LLM精排） |
| 2 | **知识树在线插件** | `plugins/knowledge-tree-plugin/` | 插件 | 知识树 pre_llm_call recall + post_llm_call 增量学习 |
| 3 | **SkillOpt 增量优化** | `scripts/skillopt-runner/` | Cron | 基于对话负反馈自动优化 skill 文档 |
| 4 | **系统健康巡检** | `scripts/system-health-check/` | Cron | 3-tier 架构每日巡检组件状态，异常飞书告警 |
| 5 | **记忆清理** | `scripts/memory-cleanup/` | Cron | LLM 驱动的 MEMORY.md/USER.md 分类清理 |
| 6 | **记忆聚类分析** | `scripts/clustering-analysis-v3/` | Cron | HDBSCAN 聚类 + LLM 因果链检测 |
| 7 | **每日在线学习** | `scripts/daily-learn/` | Cron | 从 GitHub/ArXiv 自动采集知识入库 |
| 8 | **知识分域建树管线** | `scripts/knowledge-tree-builder/` | 手动 | 从文档批量建知识树（Step 1-5 全管线） |
| 9 | **Draw.io 矢量图生成** | `scripts/drawio-generator/` | 手动 | 根据描述自动生成 draw.io/SVG 架构图 |
| 10 | **AI 报告生成系统** | `scripts/ai-report-system/` | 手动 | 多数据源分析，自动生成报告 |
| 11 | **自我进化研究** | `scripts/self-evolving/` | 研究 | SE-Agent 自进化算子研究 + 反思回路 |
| — | **Cron 定时脚本集合** | `scripts/cron-wrappers/` | Cron | 统一 shell wrapper（cron_common.sh），flock 防重入 |

> Hermes Gateway（消息网关）、SkillOpt-Sleep（优化引擎）、Hindsight RAG（记忆系统）是系统级的常驻服务，其源码独立管理，不在此仓库中。
>
> **从零搭建完整环境？请先看 [docs/setup-guide.md](docs/setup-guide.md)** — 包含常驻服务安装、环境变量、验证清单。
>
> 📖 **想深入了解架构、模块职责、关键类与函数？请看 [CODE_WIKI.md](CODE_WIKI.md)** — 完整代码百科（项目架构、模块详解、依赖关系、运行方式）。

## 系统架构
Hermes 采用 **5 层记忆体系** + **技能强制注入**，三个子项目 + 两个插件协同，实现：控制 token 开销 + 最大化记忆有效性 + 技能即时可用。

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
│ 用户消息 ──→ 知识导航 (Hook pre_llm_call) ──→ LLM Router 决策 ──→ 按 mask 条件执行 ──→ 融合组装 ──→ LLM 调用 ──→ 响应 │
│                            │                         │             ↑               │
│                    ┌───────┴───────┐                  │             │               │
│                    ▼               ▼                  │             │               │
│           Hindsight recall   知识树 recall              │             │               │
│           （经验域）         （知识域）                 │             │               │
│                    │               │                  │             │               │
│                    └───────┬───────┘                  │             │               │
│                            │                          │             │               │
│                            ▼                          │             │               │
│                    ┌──────────────┐                   │             │               │
│                    │  Skill 匹配   │────────────────────             │               │
│                    │  (第三路)     │ 自动注入 <auto_loaded_skills>   │               │
│                    │  三级混合筛选  │ 到用户消息                        │               │
│                    │  (关键词预筛→Embedding精筛→LLM精排) │                   │             │               │
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
│ 优化 RAG 检索结构│  │ 必要保留→MEMORY/USER          │   │
│ + 因果链检测      │  │ 其余降级→RAG                 │   │
│ → 提升 recall 率 │  │ → 减少 token 开销            │   │
└───────────────────┘  └──────────────────────────────┘   │
                                                          │
┌──────────────────────────────────────────────────────────┘
│
┌─────────────────────────────────────────────────────┐
│ AI 报告生成 (Cron / 手动)                                   │
│ 多数据源分析 → DAG 并行执行 → 质量评估 → 导出报告       │
└─────────────────────────────────────────────────────┘

╔═════════════════════════════════════════════════════╗
║ 本仓库 /mnt/d/HermesProject                                ║
║ 统一部署: ./deploy/deploy.sh deploy <project>              ║
╚═════════════════════════════════════════════════════╝
```

**设计目标**：
- **知识导航**：确保有效记忆主动召回，避免 LLM 不主动提取记忆的问题
- **技能强制注入**：Skill 三级混合筛选（关键词预筛→Embedding精筛→LLM精排），自动匹配相关 skill 并注入全文到 `<auto_loaded_skills>`，匹配不到不强行注入，解决 LLM 不主动 `skill_view()` 的问题
- **聚类分析**：优化 RAG 库结构，最大化提升召回记忆的有效性与 recall 率
- **记忆清理**：精简核心记忆，控制 token 开销，其余降级到 RAG 按需召回
- **综合效果**：每条消息只携带必要上下文，又不丢失任何历史记忆，最大化记忆有效性

## 目录结构

```
HermesProject/
├── config/                  # 全局配置（common.yaml、gateway.yaml.example）
├── scripts/
│   ├── ai-report-system/         # AI 报告生成系统
│   ├── clustering-analysis-v3/   # 记忆聚类分析（HDBSCAN + 因果链）
│   ├── cron-wrappers/            # 统一 cron shell wrapper 集合
│   ├── daily-learn/              # 每日在线学习（ArXiv/GitHub）
│   ├── drawio-generator/         # Draw.io 矢量图生成
│   ├── knowledge-tree-builder/   # 知识分域建树管线
│   ├── memory-cleanup/           # 记忆分类清理
│   ├── self-evolving/            # 自我进化研究 + 反思回路
│   ├── skillopt-runner/          # SkillOpt 增量优化 runner
│   ├── skillopt-sleep/           # SkillOpt-Sleep 优化引擎（独立依赖）
│   └── system-health-check/      # 系统健康巡检
├── plugins/
│   ├── knowledge-navigation/     # 知识导航插件（融合双域）
│   ├── knowledge-tree-plugin/    # 知识树在线插件（增量学习）
│   └── hermes-plugin-template/   # 插件开发模板
├── deploy/                  # 一键部署系统
│   ├── deploy.sh            # 分发入口（list / plan / deploy / rollback / history / cleanup）
│   ├── lib/
│   │   └── common.sh        # 共享函数库（备份/回滚/清单展开/技能部署/防残留）
│   ├── projects/            # 各项目独立配置脚本
│   │   ├── ai-report-system.sh
│   │   ├── clustering-analysis-v3.sh
│   │   ├── drawio-generator.sh
│   │   ├── knowledge-navigation.sh
│   │   └── memory-cleanup.sh
│   ├── manifests/           # 各项目文件级部署清单（glob 模式）
│   └── README.md            # 部署系统说明文档
├── docs/                    # 文档入口 → docs/README.md
│   ├── README.md            # 文档索引
│   ├── architecture/        # 架构设计
│   ├── plans/               # 活跃开发计划
│   ├── research/            # 行业调研
│   ├── reviews/             # 审查报告
│   ├── archive/             # 已归档文档
│   └── engineering-standards.md
├── .qoder/                  # 开发规范体系（6 个规则文件）
│   └── rules/
│       ├── architecture-spec.md
│       ├── deployment-spec.md
│       ├── git-workflow-spec.md
│       ├── naming-conventions.md
│       ├── requirements-spec.md
│       └── testing-spec.md
├── AGENTS.md                # Agent 配置与交互规则
├── pyproject.toml           # 主项目构建配置（hermes-gateway）
├── requirements.txt         # 主项目依赖（占位符）
└── .gitignore
```

## 快速开始

### 环境要求
- Python 3.10+
- WSL2 (Ubuntu)
- Hermes 运行时已部署在 `/root/.hermes/`

> 完整环境搭建指南（PostgreSQL、LiteLLM、Hindsight、Gateway 安装与配置）见 **[docs/setup-guide.md](docs/setup-guide.md)**。

### 安装子项目（开发模式）

```bash
# 主项目
cd /mnt/d/HermesProject && pip install -e .

# AI 报告系统
cd scripts/ai-report-system && pip install -e .

# 聚类分析
cd scripts/clustering-analysis-v3 && pip install -e .

# 知识导航（作为插件不需要 pip install，部署到插件目录即可）
```

### 运行测试

> **注意**：测试前先 `pip install -e .` 安装对应子项目，否则会报 `ModuleNotFoundError`。

```bash
# AI 报告系统
cd scripts/ai-report-system && pip install -e . && pytest

# 聚类分析
cd scripts/clustering-analysis-v3 && pip install -e . && pytest

# Draw.io 生成器
cd scripts/drawio-generator && pip install -e . && pytest

# 知识树构建器
cd scripts/knowledge-tree-builder && pip install -e . && pytest

# 记忆清理
cd scripts/memory-cleanup && pip install -e . && pytest

# 知识导航
cd plugins/knowledge-navigation && pip install -e . && pytest
```

## 开发工作流

所有代码修改必须遵循以下流程：

```
1. 修 改 源 码  —  在 D:\HermesProject 修改对应子项目
2. 本 地 测 试  —  运行项目级测试确认修改正确
3. 代码审查    —  将修改提给用户审查，等待确认
4. 修正迭代    —  根据审查意见修正，直至用户确认通过
5. 部    署    —  用户确认后，通过 deploy.sh 部署到运行环境
```

> ⚠️ **关键约束**：
> - **不得在审查通过前直接部署**。提审 → 用户确认 → 部署
> - 不得直接修改 `/root/.hermes/` 下的运行时文件
> - 所有修改一律从本仓库出发，经 review → deploy 流程发布

## 部署

详见 [deploy/README.md](deploy/README.md) 完整文档。

### 前提

部署是工作流的**最后一步**，必须在源码修改经过代码审查、用户确认通过后方可执行。切勿在审查通过前部署。

### 架构

deploy 采用三层架构：

- **`deploy.sh`** — 分发入口，接收 `list / plan / deploy / rollback / history / cleanup` 子命令
- **`deploy/projects/<project>.sh`** — 各项目独立配置（源路径、目标路径、服务名、旧文件清理规则、技能部署）
- **`deploy/lib/common.sh`** — 共享函数库（备份/回滚/清单展开/防残留/技能部署）
- **`deploy/manifests/<project>.manifest`** — glob 模式定义包含/排除清单

```bash
# 列出可部署项目
./deploy/deploy.sh list

# 预览将部署的文件（不动文件系统）
./deploy/deploy.sh plan ai-report-system

# 一键部署
./deploy/deploy.sh deploy ai-report-system
./deploy/deploy.sh deploy clustering-analysis-v3
./deploy/deploy.sh deploy cron-wrappers
./deploy/deploy.sh deploy daily-learn
./deploy/deploy.sh deploy drawio-generator
./deploy/deploy.sh deploy knowledge-navigation  # 自动重启 hermes-gateway
./deploy/deploy.sh deploy knowledge-tree-builder
./deploy/deploy.sh deploy knowledge-tree-plugin  # 自动重启 hermes-gateway
./deploy/deploy.sh deploy memory-cleanup
./deploy/deploy.sh deploy self-evolving
./deploy/deploy.sh deploy skillopt-runner
./deploy/deploy.sh deploy skillopt-sleep
./deploy/deploy.sh deploy system-health-check

# 回滚（先清残留再还原备份）
./deploy/deploy.sh rollback <project>
```

**部署目标路径：**

| 项目 | 运行时位置 | 重启服务 |
|------|------------|---------|
| AI 报告系统 | `/root/.hermes/scripts/ai-report-system/` | — |
| 聚类分析 | `/root/.hermes/scripts/clustering-analysis-v3/` | — |
| Cron wrappers | `/root/.hermes/scripts/cron-wrappers/` | — |
| 每日在线学习 | `/root/.hermes/scripts/daily-learn/` | — |
| Draw.io 生成 | `/root/.hermes/scripts/drawio-generator/` | — |
| 知识树构建器 | `/root/.hermes/scripts/knowledge-tree-builder/` | — |
| 记忆清理 | `/root/.hermes/scripts/memory-cleanup/` | — |
| 知识导航 | `/root/.hermes/plugins/knowledge-navigation/` | `hermes-gateway.service` |
| 知识树插件 | `/root/.hermes/plugins/knowledge-tree-plugin/` | `hermes-gateway.service` |
| 自我进化 | `/root/.hermes/scripts/self-evolving/` | — |
| SkillOpt Runner | `/root/.hermes/scripts/skillopt-runner/` | — |
| SkillOpt Sleep | `/root/.hermes/scripts/skillopt-sleep/` | — |
| 系统健康巡检 | `/root/.hermes/scripts/system-health-check/` | — |

> 部署系统完整说明见 [deploy/README.md](deploy/README.md)。

## 各子项目详情

### 1. AI 报告生成系统
基于 Hermes 工具集的智能报告生成管线：
- 多数据源（Excel/CSV/API/Web搜索）
- DAG 并行化执行
- 质量评估循环
- 多格式导出（Word/PDF/Markdown）
- StateGraph 工作流编排

### 2. 记忆聚类分析
对 Hermes 对话记忆进行向量嵌入 + HDBSCAN 语义聚类：
- 自动识别主题簇 + 实体挂靠
- 因果关系链检测
- 每日 cron 定时执行
- `--dry-run` 安全模式

### 3. Draw.io 矢量图生成
根据规格说明自动生成 draw.io / SVG 矢量图：
- 支持 draw.io 和 SVG 两种输出格式
- 可配置画布尺寸、调色板、节点样式
- 节点布局引擎（层级布局 + 连线路由）
- 内置校验器确保输出合规
- 可通过 CLI 或 Python API 调用

### 4. 记忆清理
LLM 驱动的智能记忆管理（MEMORY.md + USER.md）：
- 6 类分类（retain / remove / merge / compress / hindsight / flagged）
- 并行 LLM 批处理（BATCH_SIZE=20）
- 默认 dry-run，`--apply` 才实际执行
- 支持 Hindsight API 同步删除

### 5. 知识分域建树管线
从文章自动构建二叉树知识树：
- Step 1：LLM 提取 3-8 个关键知识点（区分原理/方法论 vs 配置/操作）
- Step 1.5：规则驱动的准入过滤（4 条规则，无 LLM）
- Step 2：HDBSCAN 递归 sub-clustering + 自动干跑迭代
- Step 3：LLM 判断多叉/二叉结构 + 二分校验
- Step 4：LLM 节点命名（科目 4-8 字，知识点 2-8 字）
- Step 5：PG 写入（含增量去重 + 矛盾检测）
- 用户命令：`add` / `ingest` / `tree` / `find` / `move` / `edit` / `remove` / `merge`

### 6. 知识导航插件（LLM Router 三路注入）
在每次 LLM 调用前通过 LLM Router 智能决策注入路径：
- **H（经验域）**：从 Hindsight 召回相关记忆
- **KT（知识域）**：通过 knowledge-tree-plugin 召回知识树，沿 `kt_entity_links` 表展开共享实体的关联知识点（实体多跳）
- **S（能力域）**：Skill 三级混合筛选（关键词预筛 Top-30 → Embedding 余弦相似度精筛 Top-20 → LLM 精排 Top-3），自动注入
- 动态执行：≥2 路并行，1 路串行
- 熔断器 + 飞书告警 + Router 异常 fallback 全开；Embedding 调用失败自动降级
- 注入去重、Compaction、HitCounter、时态衰减、跨域去重

### 7. 知识树在线插件
增量学习，在 LLM 响应后自动补充知识：
- `post_llm_call`：分析对话 → 提取知识点 → 增量放置到知识树（格式不匹配修复、numpy 布尔歧义修复已完成）
- 向知识导航插件提供 `recall_from_tree()` / `multi_hop_recall()` 公共 API
- `multi_hop_recall()`：沿 `kt_entity_links` 表展开共享实体的关联知识点（实体多跳），标记 `source="multi-hop"`，跳过 rerank
- 增量去重（cosine > 0.95 合并 source_ids）
- 矛盾检测（cosine > 0.8 + 关键词对立 → review_queue）
- K 向量 EMA 更新

### 8. 自我进化研究（SE-Agent）

自进化智能体算子研究项目，包含：

**三大进化算子**：
- **Revision**：失败驱动的策略生成（`se-revision`）
- **Recombination**：跨轨迹知识合成（`se-recombine`）
- **Refinement**：风险感知的内容优化（`se-refine`）

**Phase B 反思回路**（已完成）：
- `kanban-reflect` CLI：分析失败任务并输出结构化反思结果
- `kanban_reflect_hook.py`：Kanban 集成 + 注入重试 prompt
- 29 个测试全部通过

支持 B / D 两种算子串联模式，可通过 `python -m self_evolving.scripts.*` 调用。

## 开发规范

- **代码风格**：见 [docs/engineering-standards.md](docs/engineering-standards.md)
- **提交规范**：`<type>(<scope>): <中文描述>`（见 `.qoder/rules/git-workflow-spec.md`）
  - 类型：`feat` / `fix` / `refactor` / `docs` / `chore` / `style`
  - 描述 ≤ 72 字中文
- **分支策略**：直接使用 `main` 分支（单开发者模式），见 [GIT-7]
- **命名规范**：Python snake_case、Shell kebab-case、配置 kebab-case（完整见 `.qoder/rules/naming-conventions.md`）
- **架构约束**：5 层记忆体系、模块边界隔离、compat 兼容层规则（见 `.qoder/rules/architecture-spec.md`）

## License

MIT

> ⚠️ 切勿手动修改 `/root/.hermes/` 下的运行时文件 —— 一律从本仓库经 review → deploy 流程发布。

## 技能文件（Skills）

多个子项目包含 `skills/` 目录，提供可复用的 Agent playbook：

| 项目 | Skill |
|------|-------|
| `scripts/ai-report-system/` | `software-development/ai-report-generation-system-implementation/SKILL.md` |
| `scripts/clustering-analysis-v3/` | `mlops/clustering-analysis/SKILL.md`、`operations/memory-correction/SKILL.md` |
| `scripts/drawio-generator/` | `diagramming/drawio-generator/SKILL.md` |
| `scripts/memory-cleanup/` | `devops/memory-md-cleanup/SKILL.md`、`software-development/memory-cleanup/SKILL.md` |
| `scripts/self-evolving/` | `se-agent-evolution/SKILL.md` |
| `scripts/knowledge-tree-builder/` | 建树管线 Step 1-5 CLI |
| `plugins/knowledge-navigation/` | `software-development/knowledge-navigation/SKILL.md` |
| `plugins/knowledge-tree-plugin/` | `public_api.recall_from_tree()` 公共 API |

部署时通过 `deploy.sh` 自动将 skills/ 同步至 `/root/.hermes/skills/`。