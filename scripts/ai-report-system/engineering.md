# AI报告生成系统 — 工程手册

> 更新时间：2026-05-03
> 每次改方案前先看本节，再动手。

---

## 1. 开发环境

| 项目 | 值 |
|------|----|
| Python | 3.10+（pyproject.toml requires-python） |
| 测试 | `python3 -m pytest tests/ -v` |
| 单元测试 | `python3 -m pytest tests/ -m unit -q`（244 个，无 API 调用） |
| 集成测试 | `python3 -m pytest tests/ -m integration`（3 个，需 API key） |
| 依赖管理 | `pyproject.toml`（setuptools） |
| 外部依赖 | Dify API（docker compose）、Tavily API（可选） |
| 项目根路径 | `/mnt/c/Users/1/Desktop/AI/报告团队/ai_report_system` |
| ECC 规则 | `/app/everything-claude-code/rules/` |
| 脚本目录 | `scripts/` — 代理层搜索、管线测试、反馈修订（1008 行） |
| 源文件目录 | `reports/<topic>/inputs/` — 用户素材文件（v4.9.0+） |

### 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `DIFY_DATASET_API_KEY` | ✅ | Dify 知识库 API Key |
| `DIFY_DATASET_ID` | ✅ | Dify 数据集 ID |
| `TAVILY_API_KEY` | ❌ | Tavily 搜索（备选 DuckDuckGo 免费） |
| `DIFY_COMPOSE` | ❌ | 默认 `/app/dify/docker/docker-compose.yml` |
| `DIFY_API` | ❌ | 默认 `http://api:5001` |
| `SOURCE_DOC_PATH` | ❌ | 源文档精确路径 |

### 外部依赖状态

| 服务 | 状态 | 启动方式 |
|------|------|---------|
| Dify API | ✅ 运行中 | `docker compose -f /app/dify/docker/docker-compose.yml up -d` |
| Weaviate | ✅ 运行中 | 需显式指定 `--profile weaviate` |

---

## 2. 模块地图

### 2.1 总体架构

```
管线内 (无 hermes_tools)
┌──────────────────────────────────┐
│ src/graph/                       │
│   search_refs → 从缓存池读大纲    │
│   optimize_structure → 仅用预定义 │
│   synthesize → 仅用预定义章节     │
│   curate → 读 fact_bank 匹配事实  │
├──────────────────────────────────┤
│ src/planning/                    │
│   content_generator → 无搜索     │
│   只用已注入 materials_text       │
├──────────────────────────────────┤
│ src/hermes_tools/                │
│   QualityLoop → Tavily/DDG       │
│   FullReportLoop → 无搜索        │
├──────────────────────────────────┤
│ 管线内无 hermes_tools            │
│ 搜索仅限 QualityLoop 补搜        │
└──────────────────────────────────┘
```

> **注意：** `pre_search` 不再挂载在 `run_report.sh` 标准流程中。
> 目录在聊天中确认后直接写入 `report_goal.chapter_prompts`，无需 LLM 参考文章。
> `pre_search` 保留为可选工具（供需要时手动调用）。

### 2.2 StateGraph 规划层（`src/graph/`）

```
src/graph/
├── report_graph.py         — StateGraph 5 节点管线，主入口（865行 → 函数均≤50行）
├── material_service.py     — 统一素材准备服务（含 Tavily/DDG 搜索，llm_caller 注入）
└── types.py                — 共享数据类
```

#### `run_planning(topic, source_content, report_type, language, report_goal, domain_config, reference_outlines)`
- **作用**：一键运行 StateGraph 规划管线
- **输出**：`{optimized_prompts[], report_goal, chapter_prompts[], reference_outlines[], raw_materials[]}`

**5 节点**：

| 节点 | 函数 | 行数 | 说明 |
|------|------|------|------|
| 1. `define_goal` | 16行 | 调用 `_build_define_goal_prompt()` → `call_llm` → `_parse_goal_response()` |
| 2. `search_refs` | 40行 | 从统一素材池 `all_articles.json` 读 `toc_lines` → 参考大纲。**不再网络搜索** |
| 3. `optimize_structure` | 11行 | **确定性节点（v5.3.0）**。检查 `report_goal.chapter_prompts`，有内容则直接返回；为空则 `raise ValueError` 报错退出。**不再调用 LLM 重生成** |
| 4. `synthesize` | 19行 | 使用预定义 chapter_prompts（含 writing_intent / key_points / materials_text），跳过 LLM 重生成 |
| 5. `curate` | 30行 | 按 writing_intent 过滤素材，打包 materials_text |
| 6. `coverage_check` | ~130行 | 覆盖度检查，不足 70% 时自动补搜 |
| 7. `prompt_review` | 16行 | 调用 `_build_prompt_review_prompt()` → LLM → `_parse_prompt_review_response()` |

大函数拆分（2026-04-29 ECC 合规重构）：
- `define_goal`：145 行 → `_build_define_goal_prompt()`(22) + `_parse_goal_response()`(32) + 主函数(16)
- `synthesize`：134 行 → `_build_synthesize_prompt()`(52) + `_parse_synthesize_response()`(37) + 主函数(19)
- `prompt_review`：86 行 → `_build_prompt_review_prompt()`(33) + `_parse_prompt_review_response()`(31) + 主函数(16)

#### `MaterialService`
- **作用**：统一素材准备服务，所有环节共用
- **缓存优先**：`search_cache/{chapter_key}/base_{hash}.json` 命中直接返回
- **搜索降级**：Tavily → DuckDuckGo → httpx + lxml 取全文
- **依赖注入**：构造时接受 `llm_caller` 参数（测试时可 mock）

#### 数据类（`types.py`）
- `ChapterPrompt` — 每章完整提示词包
- `ReportGoal` — 报告总目标
- `MaterialPack` — 素材准备产出

### 2.3 内容生成（`src/planning/content_generator.py`）

**v4.11.0 后：DAG 驱动并行写入，3 层分层。**

```
Phase 1: DAG 驱动并行写入（3 层，按 section_type）
  每层内章节可并行，层间传摘要链确保连贯
  单章路径：_write_chapter() → task_executor / call_llm
  并行路径：_write_chapters_parallel() → sibling_chapters 注入

Phase 2: 全文质量闭环（FullReportLoop，一致性检查）
```

**清理历史：**
- `_parallel_search()` — 已删除（死代码，StateGraph 模式从未调用）
- `_run_quality_loop()` — 已删除（依赖 HermesWebSearcher）
- `self._searcher` / `self._kb_retriever` — 已清除
- `HermesWebSearcher` / `DifyKBRetriever` 导入 — 已清除
- `QualityLoop` 导入 — 已清除

### 2.4 编排器（`src/integration/`）

```
src/integration/
├── workflow_orchestrator.py  — 端到端报告生成入口（863行）
└── report_goal_helpers.py    — goal 持久化/校验/截断检测（新，110行）
```

#### `ReportWorkflowOrchestrator.run(topic, ...)`
- **Stage 0**：目标提取与确认（交互式，可复用已确认 goal）
- **Stage 1**：StateGraph 规划（5 节点，可跳过 define_goal）
- **Stage 2**：内容生成（含自审修订循环 + 立即保存 .md）
- **Stage 3**：全文质量闭环（FullReportLoop，含跨章事实一致性审计）
- **Stage 4**：图表生成（含去重 + 执行摘要）
- **Stage 5**：质量检查 + 输出清洗 + LLM 评估

**2026-04-29 重构：** 6 个 goal 相关静态方法（`_goal_dir_for_topic`, `_check_goal_exists`, `_validate_report_goal`, `_check_goal_truncation`, `_load_report_goal`, `_save_report_goal`）提取到 `report_goal_helpers.py`，主文件 812→720 行。

### 2.5 图表渲染（`src/export/`）

```
src/export/
├── chart_renderer.py    — matplotlib 真实数据渲染
└── docx_exporter.py     — markdown → .docx（Word Heading + TOC + 图片）
```

**支持类型：** `architecture_diagram`, `architecture_table`, `timeline`, `comparison`
**原则：** 仅 chart_spec.data 有真实数据才渲染
**v4.7.0+ 增强：** 每图显示所属章节标题、去重缓存（MD5 数据指纹）、动态 figsize、bbox_inches=\"tight\"
**v4.7.1 修复：** docx 图表偏移计算（prompt_idx + 4，H1-only guard 防止子标题重复嵌入）
**投资按网络层分类：** `_match_chapter_investments()` 用网络层关键词匹配

### 2.6 LLM & 素材接入（`src/hermes_tools/`）

**2026-04-29 ECC 合规重构：**

| 文件 | 行数 | 变更 |
|------|------|------|
| `ai_client.py` | 267 | 多 Provider 路由 |
| `quality_loop.py` | 612 | call_llm 改懒加载注入，`_search_supplement` 用 Tavily/DDG |
| `chart_generator.py` | 306 | call_llm 改懒加载注入 |
| `dify_kb_retriever.py` | 242 | 硬编码密钥→`_require_env()` |
| `full_report_loop.py` | 292 | 去 searcher 参数 |
| `workflow_state.py` | 410 | `.clear()` → `= {}` / `= []` |
| `web_searcher.py` | 225 | — |

**拆分包：**

| 原文件 | 行数 | 拆分后 | 现在 |
|--------|------|--------|------|
| `diagram_generator.py` | 683→47 | `diagrams/engine.py`(369) + `generators.py`(215) + `core.py`(135) | ✅ 全<400 |
| `quality_assessor.py` | 575→308 | `quality_assessor/checks.py`(178) + `dimensions.py`(129) | ✅ 全<400 |
| `document_parser.py` | 574→294 | `document_parser/formats.py`(194) + `utils.py`(118) | ✅ 全<400 |

### 2.7 评估（`src/evaluation/`）

| 模块 | 方法 | 说明 |
|------|------|------|
| `report_evaluator.py` | `evaluate_report()` | 8 维度 LLM 评估。`llm_caller` 注入已添加 |
| `state_manager.py` | 状态持久化 | SQLite 检查点 |

---

## 3. 引擎分离架构

### 3.1 搜索引擎选择

| 层 | 组件 | 搜索引擎 | 原因 |
|----|------|---------|------|
| **管线外** | `extract_facts.py` | 无搜索（从源文档提取） | 确定性事实提取，不从网络搜 |
| **管线外（可选）** | `pre_search.py` | `hermes_tools.web_search` | **不再挂载在标准流程中**。仅需手动调用 |
| **管线内** | `search_refs` | **从池读取** | pre_search 已写入，不重复搜索 |
| **管线内** | `material_service._do_web_search` | Tavily → DuckDuckGo | 子进程无 hermes_tools |
| **管线内** | `QualityLoop._search_supplement` | Tavily → DuckDuckGo | 同上 |
| **管线内** | `content_generator` | **无搜索器** | 素材已由外部注入 |

### 3.2 缓存结构

```
search_cache/
├── materials/
│   └── all_articles.json       ← pre_search 写入的统一素材池
│                                  {articles: [{title, url, content, toc_lines, credibility}]}
├── chapter-{n}-{title}/
│   ├── base_{hash}.json        ← MaterialService 搜索缓存
│   └── supplement.json         ← supplement_search 写入
reports/{topic}/
├── report_goal.json            ← 已确认的 report_goal
├── charts/                     ← 图表 PNG
└── {report}_{timestamp}.md     ← 最终报告
```

---

## 4. 关键设计原则

1. **先定报告目标，再想章节怎么写** — 不跳步
2. **report_goal.json 是用户在每个决策点上冻结的确定性记录** — 管线各节点只读、不猜、不改。缺的东西要么在 report_goal 里，要么在前置脚本（extract_facts）中产生。**管线内不调用 LLM 重生成目录。**
3. **引擎分离：管线外=hermes_tools，管线内=Tavily/DDG** — 各自用最适合的搜索
4. **search_refs 只读缓存** — 不重复搜索，pre_search 已写完
5. **素材注入优先于搜索** — content_generator 只用已注入的 materials_text
6. **可信度由搜索域名限定** — 不靠 LLM 判断来源
7. **有数据才画图** — 不编造假数据
8. **输出先保存再继续** — 内容生成完立即落盘，后续阶段超时不丢文件
9. **管线内不直接调工具，委托给 Hermes Agent 决定实现** — 管线模块（`src/planning/`, `src/evaluation/`, `src/graph/`）不应直接 import 和调用 `hermes_tools` 或搜索 API。应通过 `task_executor`（封装 `delegate_task`）将目标+上下文 JSON 发给 Agent，由它判断该用什么工具。仅有确定性搜索任务的基础设施层（`scripts/pre_search.py`, `scripts/material_service.py`）例外。参考实现：`content_generator.py` 的 `task_executor` 模式。
10. **通用写作高手原则** — 用户把任意文件丢进 `inputs/`，系统自己读、自己分类、自己写，不需要任何手动标记或配置。参见 §10。
11. **Prompt 层修复优先于代码逻辑改动** — 当 output 不符合预期时，优先考虑 prompt 调整（示例、约束、措辞），再考虑后处理清洗。代码逻辑只作为最后手段。

---

## 5. LLM 调用模式（ECC #11 合规）

### 5.1 三层架构

```
                    ┌─────────────────┐
                    │   ai_client.py   │ ← 唯一直接调 LLM API 的文件
                    │   call_llm()     │
                    └────────┬────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
  管线模块(LLM就是业务)   工具模块(LLM是工具)   其他模块
  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐
  │ report_graph   │  │ material_svc   │  │ content_gen  │
  │ report_evaluat │  │ quality_loop   │  │ (task_exec)  │
  │ chart_generator│  │ chart_generat  │  │              │
  │ (无注入，静态度 │  │ (llm_caller注入)│  │              │
  │  量函数)       │  │                │  │              │
  └────────────────┘  └────────────────┘  └──────────────┘
```

### 5.2 注入模式

**工具模块（`hermes_tools/`）：`llm_caller` 注入**
```python
def __init__(self, llm_caller=None):
    if llm_caller is not None:
        self._llm_caller = llm_caller
    else:
        from ..hermes_tools.ai_client import call_llm as _fallback
        self._llm_caller = _fallback
```

**管线模块（`planning/`）：`task_executor` 注入**
```python
def __init__(self, task_executor=None):
    self._task_executor = task_executor  # None → 降级 call_llm
```

### 5.3 已迁移文件

| 文件 | 模式 | 状态 |
|------|------|------|
| `quality_loop.py` | `llm_caller` 参数 | ✅ 懒加载 fallback |
| `chart_generator.py` | `llm_caller` 构造注入 | ✅ 懒加载 fallback |
| `material_service.py` | `llm_caller` 构造注入 | ✅ 懒加载 fallback |
| `report_evaluator.py` | `llm_caller` 构造注入 | ✅ 懒加载 + `_ai_evaluate` 去 `@staticmethod` |
| `content_generator.py` | `task_executor` 注入 | ✅ 降级路径用 call_llm |
| `report_graph.py` | 静态函数直接调 call_llm | ✅ 保留（LLM 是业务，ECC 例外） |

---

## 5.5 已删除/弃用的模块

| 模块 | 状态 | 替代 |
|------|------|------|
| `ChartAdvisor` | ❌ 已移除 | StateGraph synthesize 规划 chart_spec |
| `ChartGenerator.generate_all()`（LLM编数据版） | ❌ 已移除 | `ChartRenderer.render_chart()` 从源文档提真实数据 |
| `_parallel_search()` 旧版无差别 KB 检索 | ❌ 已删除 | StateGraph curate 节点按意图过滤 |
| `HermesWebSearcher`（质量闭环中） | ❌ 已移除 | Tavily/DuckDuckGo 直接搜索 |
| `_run_quality_loop()` in content_generator | ❌ 已删除 | 依赖 HermesWebSearcher，不再需要 |
| `_build_materials_text()` in report_graph | ❌ 已删除 | synthesize 内联格式化 |
| `_goal_dir_for_topic` 等 6 个静态方法 | ❌ 移到 helpers | `report_goal_helpers.py` |

---

## 6. 质量增强（v4.8.0 — 2026-04-30）

四项质量提升将报告从"良好"（0.90）提升至"优秀且精致"（0.96）。

### 6.1 自审修订循环

**文件：** `src/planning/content_generator.py` — `_build_revision_prompt()`

每章写作后 LLM 自审 4 维度：关键点覆盖、论据深度、逻辑完整性、数据一致性。仅当质量有提升时才替换原内容。

```python
# 调用方式（content_generator Phase 1 内）：
revised = self._llm(self._build_revision_prompt(content, spec, topic))
if _estimate_quality(revised) > _estimate_quality(content):
    content = revised  # 仅替换有提升的
```

**成本：** +1 LLM 调用/章（6 章 ≈ +120s）
**日志特征：** `📝 修订后质量提升: 0.90 → 0.90`（微调无分变）

### 6.2 跨章节事实一致性审计

**文件：** `src/hermes_tools/full_report_loop.py` — `_cross_chapter_fact_check()`

检查相邻章节对同一数据点（投资额、百分比、截止日期）是否存在矛盾。只取每章前 800 字做比较，成本可控。

**检查项：**
1. 同一数据点不同数值（投资额 8.2 亿 vs 6.7 亿）
2. 同一政策文件不同解读
3. 同一概念不同定义
4. 时间线逻辑矛盾（A 说 2026 启动，B 说 2027）

**日志：** `事实一致性检查通过: 相邻章节无数据冲突`

### 6.3 推断依据标注

**文件：** `src/graph/report_graph.py` — define_goal prompt 修改

`output_conventions` 从"推断内容加注（推断）标识"改为：
```
推断内容须附带推断依据说明，如：(推断，基于XX数据)
```

**效果：** 推断语句从"我估计"变为"我估计，理由如下"。

### 6.4 执行摘要生成

**文件：** `src/integration/workflow_orchestrator.py` — `_generate_executive_summary()`

Stage 5 中取前 3000 字，LLM 生成 4 要素：核心问题（1句）+ 方案概述（2-3点）+ 投资与回报（关键数字）+ 建议决策（1句）。最终报告 `# 执行摘要` 作为首个 H1。

**成本：** +1 LLM 调用（~5s）

### 6.5 输出质量清洗

**文件：** `src/integration/workflow_orchestrator.py` — `_clean_report()`

**关键修复：** `_clean_report()` 必须在文件保存前执行（原先在保存后，导致 .md/.docx 仍含脏数据）

**清洗项：**
- 正则去除 `**本章小结**`、`**核心结论**` 等加粗标签（77→9 个残留）
- 跳过表格内 `**`（表格强调保留，docx 导出时独立清理）
- 标题编号跳号修复（`_renumber_headers()`）

**残留的 9 个 `**`** 全在表格列头内，docx 导出器独立处理。

### 6.6 Docx 排版增强

**文件：** `src/export/docx_exporter.py` — 全部重写渲染管线

| 之前 | 之后 |
|------|------|
| `---` 分隔线被忽略 | `---` → **Word 分页符** |
| `> 引用` 行被忽略 | `>` → **灰色左边框信息框** |
| `**加粗**` 原文显示 | `**加粗**` → 解析为 **Word 加粗字体** |
| 表格中 `**` 残留 | 表格 `**` 在导出时 strip |

---

## 7. 多文件素材接入（v4.9.0 — 2026-04-30）

### 7.1 设计原则：通用写作高手

**核心：** 用户把任意文件丢进 `reports/<topic>/inputs/`，系统自己读、自己分类、自己写，不需要任何手动标记或配置。

不做的：
- ❌ 不要求用户给文件加前缀/编号/标签
- ❌ 不要加 manifest.yaml 描述文件
- ❌ 不要硬编码文件名约定

做的：
- ✅ LLM sees `📄 文件名` 分隔标记 → 自动识别主题
- ✅ 所有限制在 prompt 层实现（零配置）
- ✅ 素材按 `_load_source_document()` 自动合并

### 7.2 文件加载优先级

```
1. reports/<topic>/inputs/  → 读取所有匹配扩展名的文件，合并为带 📄 标记的文本
2. reports/<topic>/         → 兼容旧流程（单文件在根目录）
3. SOURCE_DOC_PATH 环境变量 → 精确路径（单文件）
```

### 7.3 支持的文件格式

| 格式 | 依赖 | 说明 |
|------|------|------|
| `.md` / `.txt` | 无 | 直接读取 |
| `.docx` | python-docx | 自动提取纯文本，CRC 损坏文件跳过 |
| `.pdf` | pymupdf / pdfplumber（可选） | 自动提取纯文本 |

### 7.4 关键代码

```python
# src/integration/workflow_orchestrator.py

@staticmethod
def _read_file_content(fp: Path) -> str | None:
    ext = fp.suffix.lower()
    if ext in (".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".xml"):
        return fp.read_text(encoding="utf-8")
    elif ext == ".docx":
        from docx import Document
        return "\\n".join(p.text for p in Document(fp).paragraphs)
    elif ext == ".pdf":
        try: ...  # pymupdf → pdfplumber fallback
    return None
```

### 7.5 常见陷阱

**`_load_source_document()` 需要 report_type 参数才能找到 .docx 文件。** `report_config.yaml` 的 `defaults.source_extensions` 只有 `[.md, .txt]`，`.docx` 在 `tech.source_extensions` 中。如果不传 `report_type`，所有 .docx 文件被静默跳过。

```python
# ❌ 静默跳过 .docx
config = load_report_config(topic)  # 无 report_type → defaults
# ✅ 正确
config = load_report_config(topic, report_type="tech")
```

---

## 8. 源文档截断修复（v4.9.1 — 2026-04-30）

### 8.1 问题

多文件接入后源文档从 2,600 字暴增至 21,000+ 字，但所有 prompt 仍用 `source[:3000]` 硬截断。synthesize 节点只看到 14% 的内容——预算表、路线图、技术规范全部被切掉。

### 8.2 修复

| 函数 | 文件 | 旧限制 | 新限制 | 节点 |
|------|------|--------|--------|------|
| `_build_define_goal_prompt()` | `report_graph.py` | 2000 | 4000 | define_goal |
| `_optimize_goal()` | `report_graph.py` | 2000 | 5000 | goal 优化 |
| `_build_synthesize_prompt()` | `report_graph.py` | **3000** | **8000** | synthesize（核心） |
| `curate()` | `report_graph.py` | 3000 | 6000 | curate |
| `_extract_chart_data_via_llm()` | `report_graph.py` | 4000 | 6000 | 图表数据提取 |

### 8.3 多文件 prompt 指引

在 `_build_synthesize_prompt()` 中新增：
```
注意：源文档由多个文件合并而成，每个文件以 📄 文件名 标记开头。
请识别每个文件的内容主题，将关键数据分配到对应章节。
```

### 8.4 验证

```bash
# 查找所有硬截断点（新增多文件支持后必须跑）
grep -rn 'source\[:[0-9]' src/ --include="*.py"
# 每个截断点必须审：是设计决策还是人为限制？
```

### 8.5 效果

| 指标 | 修复前（v4.9.0） | 修复后（v4.9.1） |
|------|-----------------|-----------------|
| synthesize 可见字符 | 3,000/21,642（14%） | 8,000/21,642（37%） |
| 报告字数 | 19,823 | 25,132 |
| 预算/投资引用 | ~5 | ~70 |
| 评分 | 0.90 | 0.97 |

---



## 9. 图表改进与Docx嵌入（v4.7.0-v4.7.1）

### 9.1 图表去重

**文件：** `src/export/chart_renderer.py`

在 `_inject_chart_data()` 中引入 `investments_by_chapter`——LLM 按章节提取独立投资数据，`_match_chapter_investments()` 模糊匹配章节标题到网络层。`_DEDUP_CACHE` 按数据 MD5 跳过完全相同的数据。

```python
_DEDUP_CACHE: set[str] = set()

def render_chart(chart_spec, ...):
    dhash = _data_hash(chart_spec["data"])
    if dhash in _DEDUP_CACHE:
        logger.info("skip duplicate chart for '%s'", chapter_title)
        return None
    _DEDUP_CACHE.add(dhash)
```

**验证：** 去重前 6 渲染→3 重复，去重后 2 渲染+3 跳过。

### 9.2 动态图表尺寸

`figsize` 自适应：`fig_height = max(3.5, len(items) * 0.5)`，中文标签用 `bbox_inches=\"tight\"` 替代 `tight_layout()`。

### 9.3 Docx 图表嵌入偏移修复（v4.7.1 关键修复）

**Bug A — 偏移量错误：** `chart_map = dict(chart_images)` 使用 prompt index（0 基），但 docx H1#1=报告标题，H1#2=首个章节（prompt[0]）。原代码 +2 偏移在加入执行摘要后失效。

**修复：** `chart_map[prompt_idx + 4]` = 目录(H1#1) + 摘要(H1#2) + 标题(H1#3) + 1-indexed(H1#4)

**Bug B — 子标题重复嵌入：** 图表嵌入逻辑在 `if level == 1:` 之外，每个 H2/H3 都触发一次嵌入。

**修复：** 嵌入逻辑必须在 `if level == 1:` 块内。

**验证命令：**
```bash
python3 -c "
import zipfile, xml.etree.ElementTree as ET
with zipfile.ZipFile('report.docx') as z:
    doc = z.read('word/document.xml')
    root = ET.fromstring(doc)
    ns = {'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture'}
    drawings = root.findall('.//w:drawing', ns)
    print(f'Embedded images: {len(drawings)} (expected: ≤ unique charts)')
"
```

---

## 10. 通用写作高手设计原则

**核心理念：** 系统应是通用写作高手——用户把文件丢进 `inputs/`，系统自己读懂内容、自动归类、写报告，不需要手动标记或配置。

### 三不原则

1. **不硬编码文件名约定** — 不加 `00_总纲`、`01_技术方案` 前缀
2. **不加配置文件** — 不加 manifest.yaml、tags、layer_map
3. **不加用户引导** — 不加"请选择文件类型"交互步骤

### 三做原则

1. **所有限制在 prompt 层实现** — 示例用通用占位符，不绑定特定主题
2. **多文件自动合并** — `📄 文件名` 分隔标记，LLM 自动识别
3. **截断限制充分** — synthesize 能看到 8000+ 字素材（DeepSeek 128K 上下文的 6%）

### 10.1 设计判断树

```
用户添加新源文件
  ├─ 是否改了文件名？
  │    ├─ 是 → ❌ 拒绝（不能依赖文件名约定）
  │    └─ 否 → ✅ 继续
  ├─ 是否改了文件结构？
  │    ├─ 是 → prompt 层适应，不写代码
  │    └─ 否 → ✅ 继续
  └─ 是否需要用户告诉我这文件讲什么？
       ├─ 是 → ❌ 拒绝（系统必须自己读懂）
       └─ 否 → ✅ 继续
```

---

## 11. 反馈修订系统（v4.8.2+）

**文件：** `scripts/feedback_revision.py`（447 行）

### 11.1 工作流程

```
你在 Word 里选中段落 → 右键 → 新建批注 → 写下反馈意见
→ Ctrl+S 保存 .docx
→ python3 scripts/feedback_revision.py 报告.docx
→ 打开 报告_revised.docx 看修订效果
```

### 11.2 技术要点

- 读取 .docx 所有批注（comment），通过 zipfile 底层解析 `word/comments.xml`
- 关联批注到所在 H1 章节（回溯最近 H1 Heading）
- 加载 `report_goal.json` 中的 `writing_role` 确保修订语气一致
- 仅修改有批注的章节，其他章节不变
- 输出用 `export_to_docx()` 重新导出

---

## 11.5 DAG 并行化章节生成（2026-04-30）

### 11.5.1 问题

章节生成是当前管线的主要性能瓶颈。7 章报告串行调用 7 次 LLM API（~30s/次 ≈ 210s），占全流程 50%+ 时间。

### 11.5.2 方案：3 层 DAG

利用现有 `section_type` 字段（intro/body/conclusion）自动推导依赖关系，层内可并行，层间串行。

```
串行（原） → Ch1→Ch2→Ch3→Ch4→Ch5→Ch6→Ch7           = 7 calls, ~210s
DAG（新）  → Layer 0: [Ch1(1/2)] ─ 并行 2 章（intro）
             Layer 1: [Ch3,Ch4,Ch5] ─ 并行 3 章（body）
             Layer 2: [Ch6,Ch7]    ─ 并行 2 章（conclusion）
             = 3 层, ~120s（节省 43%）
```

### 11.5.3 连贯保障三锚定

| 锚定层 | 机制 | 实现方式 |
|--------|------|---------|
| **宪法层** | report_goal + writing_role | 每章 system prompt 头部注入 |
| **层间** | 前层摘要链 | `all_prev_summaries` 传前层所有章的 200 字摘要 |
| **同层** | sibling_chapters 交换 | 告知同层其他章节标题+意图，避免内容重叠 |

### 11.5.4 DAG 推导规则

**文件：** `src/planning/dag_utils.py`（新，160 行）

```python
def derive_dag_layers(sections, chapter_prompts=None) -> list[list[int]]:
```

规则（不硬编码标题，仅用 section_type）：
- `intro/background/overview` → Layer 0
- `body/analysis` 或未知 → Layer 1
- `conclusion/summary/appendix/recommendation` → Layer 2

**降级：** 章节 ≤3 或无 intro+conclusion → 单层

### 11.5.5 并行写入

**文件：** `src/planning/content_generator.py`

新增 `_write_chapters_parallel()`：
- 单章节层 → 复用 `_write_chapter()` 串行路径
- 多章节层 → 构建 sibling_chapters 信息注入每章 context

`_build_chapter_context_json()` 新增 `sibling_chapters` 字段。

### 11.5.6 降级兼容

- `generate_from_plan()` API 签名不变，编排器零改动
- ≤3 章、无 section_type → 自动单层
- 章节顺序保持原始 `plan.sections` 顺序

### 11.5.7 测试

**文件：** `tests/planning/test_dag_utils.py`（14 个测试）
- 标准 3 层推导、降级、空、未知类型、prompts 覆盖
- 全部 `@pytest.mark.unit`，无外部依赖

---

## 12. 测试架构

### 12.1 测试分类

```
tests/
├── conftest.py                     — 全局 conftest（Dify 环境变量自动注入）
├── @pytest.mark.unit (244 tests)   — 纯逻辑，无 API 调用，快速
├── @pytest.mark.integration (3)    — 真实 LLM API，需 API key
├── planning/
│   └── test_dag_utils.py           — DAG 分层推导测试（14 个）
└── hermes_tools/
    ├── conftest.py                 — 子目录 conftest
    ├── test_dify_kb_retriever.py
    ├── test_quality_loop.py
    ├── test_chart_generator.py
    ├── test_ai_client.py           — 含 3 个 @integration + @skip
    ├── test_workflow_state.py
    └── test_new_modules.py         — 436 行
```

### 12.2 测试文件拆分

| 原文件 | 行数 | 拆分后 |
|--------|------|--------|
| `test_hermes_searcher.py` | 647→15 stub | `test_hermes_search_result.py`(130) + `test_hermes_searcher_base.py`(192) + `test_hermes_searcher_search.py`(326) |

### 12.3 运行方式

```bash
# 全量测试
python3 -m pytest tests/ -q

# 单元测试（推荐日常使用）
python3 -m pytest tests/ -m unit -q

# 集成测试（需要 API key）
python3 -m pytest tests/ -m integration -v

# 特定文件
python3 -m pytest tests/hermes_tools/test_quality_loop.py -v
```

---

## 13. ECC 合规状态（2026-04-29）

| ECC 规则 | 状态 | 说明 |
|----------|------|------|
| #1 文件大小(≤800行) | ⚠️ workflow_orchestrator ≈ 863 行（超限） | 注解：主入口文件，含 6 个 Stage + _clean_report 等复杂逻辑，拆分到 helpers 后仍偏大 |
| #1 函数大小(≤50行) | ✅ 合规 | report_graph 3 大函数已拆分（16/19/16 行） |
| #2 不可变性 | ✅ 合规 | workflow_state .clear() → ={} / =[] |
| #3 类型注解 | ✅ 合规 | 全部有类型注解 |
| #4 错误处理 | ✅ 合规 | 无裸 except |
| #5 输入验证 | ✅ 合规 | 边界校验 |
| #6 密钥管理 | ✅ 合规 | dify_kb_retriever `_require_env()` |
| #7 嵌套≤4层 | ✅ 合规 | report_graph 多级 try/JSON 已拆分 |
| #8 可变默认参数 | ✅ 合规 | 全部使用 None 模式 |
| #9 资源管理 | ✅ 合规 | 全部使用 with |
| #10 测试标记 | ✅ 合规 | 13 测试文件加 @pytest.mark |
| #11 LLM 注入 | ✅ 合规 | 5 文件已迁移，2 文件保留（ECC 例外） |
| #14 文件组织 | ✅ 合规 | 7 个 ≥400 行文件已拆分 |
| #15 安全 | ✅ 合规 | 无硬编码密钥，无注入路径 |
| #17 日志格式 | ✅ 合规 | %s 占位符 |
| #19 导入顺序 | ✅ 合规 | 分组并按字母序 |
| #20 `from __future__` | ✅ 合规 | 全部 37 文件包含 |

---

## 14. 变更记录

| 时间 | 改动 | 涉及文件 |
|------|------|---------|
| 2026-04-28 | 去中心化配置 + 缓存优先架构 + pre_search 代理层搜索 | `config/report_config.py`, `material_service.py`, `scripts/pre_search.py` |
| 2026-04-28 | supplement_search 注入 + goal 持久化 + 两步流程 | `scripts/supplement_search.py`, `report_graph.py`, `workflow_orchestrator.py` |
| 2026-04-29 | **引擎分离 v4.5.0** — 代理层=hermes_tools，管线内=Tavily/DDG | 全模块 |
| 2026-04-29 | content_generator 清理：删 `_parallel_search`, `_run_quality_loop`, HermesWebSearcher | `content_generator.py` |
| 2026-04-29 | supplement_search Tavily→hermes_tools（与 pre_search 统一） | `scripts/supplement_search.py` |
| 2026-04-29 | search_refs 从池读 toc_lines（不再搜索） | `report_graph.py` |
| 2026-04-29 | QualityLoop HermesWebSearcher→Tavily/DDG，去 searcher 参数 | `quality_loop.py`, `full_report_loop.py` |
| 2026-04-29 | **ECC CRITICAL 修复** | 全模块 |
| 2026-04-29 | 硬编码密钥→`_require_env()`，删除 fallback | `dify_kb_retriever.py` |
| 2026-04-29 | report_graph 3 大函数拆分（define_goal 145→16, synthesize 134→19, prompt_review 86→16） | `report_graph.py` |
| 2026-04-29 | workflow_orchestrator 812→720 行（6 goal 方法→`report_goal_helpers.py`） | `workflow_orchestrator.py`, `report_goal_helpers.py`（新） |
| 2026-04-29 | 7 文件 call_llm 改懒加载注入 | `quality_loop`, `chart_generator`, `material_service`, `report_evaluator` |
| 2026-04-29 | 13 测试文件加 `@pytest.mark.unit` / `@pytest.mark.integration` | `tests/` |
| 2026-04-29 | **ECC HIGH 修复** | |
| 2026-04-29 | workflow_state `.clear() → = {} / = []`（不可变性） | `workflow_state.py` |
| 2026-04-29 | test_hermes_searcher.py 647→3 文件拆分 | `tests/test_hermes_searcher_*.py` |
| 2026-04-29 | **ECC MEDIUM 修复** — 大文件拆分 | |
| 2026-04-29 | diagram_generator.py 683→`diagrams/` 包（engine 369 + generators 215 + core 135） | `src/hermes_tools/diagrams/` |
| 2026-04-29 | quality_assessor.py 575→`quality_assessor/` 包（checks 178 + dimensions 129） | `src/hermes_tools/quality_assessor/` |
| 2026-04-29 | document_parser.py 574→`document_parser/` 包（formats 194 + utils 118） | `src/hermes_tools/document_parser/` |
| 2026-04-30 | **v4.7.0 图表增强** — 去重缓存、章节标题、动态 figsize | `chart_renderer.py` |
| 2026-04-30 | **v4.7.1 Docx 图表嵌入修复** — +4 偏移 + H1-only guard | `docx_exporter.py` |
| 2026-04-30 | **v4.8.0 质量增强** — 自审修订、事实审计、推断标注、执行摘要、输出清洗、docx 排版 | `content_generator.py`, `full_report_loop.py`, `report_graph.py`, `workflow_orchestrator.py`, `docx_exporter.py` |
| 2026-04-30 | **v4.9.0 多文件接入** — inputs/ 目录、`_read_file_content()`、通用写作高手原则 | `workflow_orchestrator.py` |
| 2026-04-30 | **v4.9.1 源截断修复** — synthesize 3000→8000，全部 prompt 截断提升 | `report_graph.py` |
| 2026-04-30 | engineering.md 更新到 2026-04-30 全量状态 | `engineering.md` |
| 2026-04-30 | **DAG 并行化** — 3 层 DAG 章节并行写入（dag_utils.py + content_generator Phase 1 重构） | `src/planning/dag_utils.py`（新）, `content_generator.py` |
| 2026-04-30 | 添加 DAG 测试 14 个（全部标记 unit，无外部依赖） | `tests/planning/test_dag_utils.py`（新） |
| 2026-05-01 | **BUG 修复 — feedback_revision.py** | |
| 2026-05-01 | BUG 1: _load_report_goal() 不再扫描全部目录，改为精确+模糊+单目录回退三级匹配 | `scripts/feedback_revision.py` |
| 2026-05-01 | BUG 2: _find_topic_from_path() 修复 docx 直接放在 reports/ 下的边界条件 | `scripts/feedback_revision.py` |
| 2026-05-01 | BUG 3: _export_to_docx(chart_images=[]) → 从 charts/ 目录读取原图保留 | `scripts/feedback_revision.py` |
| 2026-05-01 | **代码质量 — DAG 层推导去重** | |
| 2026-05-01 | derive_layers_from_prompts() 委托给 dag_utils.derive_dag_layers() 消除重复逻辑 | `scripts/hermes_dag_orchestrator.py` |
|| 2026-05-01 | **清理** — 删除 11 个过期中间 docx/md 文件，保留最终 r2.docx 交付物 | `reports/智能制造企业转型建设规划/` |
|| 2026-05-01 | **ISSUE 8: chart 单章注入** — 在写作 prompt 的「本章图表数据」段注入 chart_spec.data，LLM 可在正文引用具体数值，保证文字与图表数据一致 | `content_generator.py` _build_intent_driven_prompt() |
|| 2026-05-03 | **v5.3.0 确定性管线** — optimize_structure 去掉 LLM 重生成分支（`raise ValueError`），run_report.sh 移除 pre_search，report_goal.chapter_prompts 成为目录唯一确定性来源 | `report_graph.py`, `run_report.sh` |

---

## 15. 性能基线

| Run | 日期 | 版本 | 源文 | 时间 | 章节 | 字数 | 图表 | 评分 | 备注 |
|-----|------|------|------|------|------|------|------|------|------|
| 基线 | 04-29 | v4.5.0 | 2,673 | 179s | 5 | 16,162 | 2 | 0.9145 | 去重测试 |
| 质量增强 | 04-30 | v4.8.0 | 2,600 | 441s | 6 | 24,145 | 3 | 0.9010 | -250s vs 基线 |
| 多文件 | 04-30 | v4.9.1 | 21,642 | ~374s | 6 | 25,132 | 3 | 0.97 | 截断修复后 |
| 最新 | 04-30 20:02 | — | 3 docx | — | 7 | 68,994 | — | — | 三网AI工具链验证 |


## 16. 事实库（Fact Bank）系统（v5.0.0 新增）

### 设计原则

用**结构化事实**替代**原文段落截取**作为写作素材。

### 流程

```
源文档（多文件）
  ↓ LLM 自由提取（不预设类别）
结构化事实清单（fact_bank.json）
  ↓ 自动去重 + 冲突检测
冲突标红 → 用户确认 ✓
  ↓ curate 节点按章节匹配事实
事实按类别分组 → materials_text
  ↓ 写作 LLM
结构化事实 → 原创内容（非抄袭原文）
```

### 文件

| 文件 | 职责 |
|------|------|
| `scripts/extract_facts.py` | 从源文档提取事实，构建 fact_bank.json |
| `reports/<topic>/fact_bank.json` | 结构化事实库（产出物） |
| `src/graph/report_graph.py` curate 节点 | 读取 fact_bank，按章节匹配，写入 materials_text |

### fact_bank.json 结构

```json
{
  "facts": [
    {
      "fact": "三网四年累计总投资5.269亿元",
      "evidence": "原文摘录",
      "category": "投资金额",
      "source": "文件名",
      "status": "user_confirmed"
    }
  ],
  "stats": { "total_files": 5, "total_facts": 267, ... },
  "resolved_conflicts": [
    { "issue": "总投资金额不一致", "resolution": "互联网50+工控网650+内网5.2亿=5.269亿" }
  ]
}
```

### curate 节点匹配策略

1. key_points 关键词命中事实
2. writing_intent 关键词命中事实
3. 核心类别（投资金额/时间节点/架构方案）无条件补充
4. 回退：fact_bank 不存在时使用原文段落切分（旧逻辑）

### 冲突检测

当前支持两种：
- **投资金额冲突**：含"总"/"合计"/"累计"的金额不一致
- **时间周期冲突**：整体实施年份范围不一致

## 17. 今日完成（2026-05-03）

### 确定性管线改造（v5.3.0）

**改动 A: `run_report.sh` 移除 `pre_search`**
- 移除 `pre_search` 后台启动（L162-166）和等待完成（L179-184）
- 原因：目录已在聊天中确认，无需 LLM 参考文章
- 文件：`scripts/run_report.sh`

**改动 B: `optimize_structure` 去掉 LLM 重生成**
- `chapter_prompts` 为空时不再调用 LLM 生成，直接 `raise ValueError` 报错退出
- 原因：目录必须由用户在聊天中确认后结构化写入，管线不猜
- 文件：`src/graph/report_graph.py`

**改动 C: `key_points` 链路确认**
- 验证 `content_generator.py` L175/196/226 正确消费 `key_points`
- 结论：代码链路正确，无需改代码

### 流程修正
- 用户确认目录 → 结构化写入 `report_goal.chapter_prompts` → `extract_facts` → 管线
- 移除了 "LLM 在管线内重生成目录" 环节
- 核心不变式：`report_goal.json` 是用户在决策点上冻结的确定性记录

### 工程文件更新
- `engineering.md` — v5.3.0 全量状态
- `docs/daily-log/2026-05-03.md` — 本日工作日志
- 待更新 skill

## 18. 修复：投资估算汇总跨章不一致（2026-05-02）

### 根因
LLM 在写总结章的「投资估算汇总」时，没有严格使用素材原文中的原始数字：
- 工控网层 470-630万（总价）→ 被 LLM 除以4变成了"约120万/年"
- 内网层 5.20亿（总价）→ 被 LLM 蒸发成了"约500万/年"（差100倍）
- 自行发明了4分类（软件/算力/硬件/实施）与前面各章的6分类不一致

### 修复1: Prompt 约束（src/planning/content_generator.py）
`_build_intent_driven_prompt()` 中，对标题含"总结"/"汇总"/"总览"的章节追加5条数值完整性约束：
1. 数字必须与前面各章明细数据严格一致
2. 禁止发明新分类维度
3. 禁止将总价换算为年均价
4. 所有数字必须能在素材原文中找到出处
5. 宁缺勿编

### 修复2: 跨章数值审计（src/hermes_tools/full_report_loop.py）
扩展 `_cross_chapter_fact_check()`：
- Phase 1: 原有 LLM 相邻章节检查（不变）
- Phase 2: 新增 `_cross_chapter_numeric_audit()` — 正则提取全量金额数据
  - 提取所有章节中的 X万元/亿元/万/年 等模式
  - 按网络层（互联网/工控网/内网）分组
  - 两两比对，差异 >50% 时标记为冲突并记录
  - 纯正则实现，不依赖LLM，0 token成本
