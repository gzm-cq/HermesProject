# AI报告系统 — 报告生成流程重构实施计划

> **执行方式:** subagent-driven-development（每任务一个子代理，两阶段审查）

**目标:** 将报告生成流程从「并行独立章节写作」重构为「搜索并行 + 写作串行」，实现：
1. 先搜同类样本再规划大纲
2. 整体目标 + 上章概要 + 本章搜索资料 → 逐章写作
3. 业务章节自动委托 copywriting skill
4. 全文完成后统一补图表

**架构:**
```
Phase 1 搜索样本(并行) → 规划大纲
Phase 2 搜索资料(并行) → 逐章写作(串行) → 文字版完成
Phase 3 图表分析 → 图表生成 → 最终报告
```

**已完成的模块（不在此计划内）:**
- `src/hermes_tools/delegator.py` ✅
- `src/hermes_tools/chart_advisor.py` ✅（Phase 3 使用）
- `src/hermes_tools/web_searcher.py` ✅（已重写）
- `src/hermes_tools/business_writer.py` ✅（Phase 2 使用）

**涉及修改的文件:**
- `src/planning/report_planner.py` — 新增样本搜索功能
- `src/hermes_tools/workflow_state.py` — 新增（串行写作上下文管理）
- `src/planning/content_generator.py` — 重写核心逻辑
- `tests/hermes_tools/test_workflow_state.py` — 新的测试
- `tests/planning/test_content_generator.py` — 重写测试
- `tests/planning/test_report_planner.py` — 新增测试

---

## 整体流程图

```
用户输入: "生成一份AI市场调研报告"
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: 规划大纲                                           │
│                                                             │
│ ① 搜索样本（并行）                                          │
│    delegate_task([
│       {搜索: "AI市场调研报告 范文 结构"},
│       {搜索: "market research report template structure"},
│    ])                                                       │
│                                                             │
│ ② report_planner.create_plan(                               │
│       topic, report_type, samples)                          │
│    → ReportPlan {                                           │
│        title, type, language, sections: [                   │
│          {title, type:regular/business/summary, est_words},  │
│          ...                                                │
│        ]                                                    │
│      }                                                      │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 2: 逐章写作（搜索并行 + 写作串行）                      │
│                                                             │
│ Step 2a: 搜索所有章节资料（并行）                              │
│    for 所有章节:                                             │
│       delegate_task({搜索: 本章关键词}) → 存入 search_pool   │
│                                                             │
│ Step 2b: 逐章生成（串行）                                    │
│    main_context = "整篇报告的目标说明"                        │
│    prev_summary = ""                                         │
│                                                             │
│    for i, 章节 in enumerate(sections):                       │
│        prompt = f"""                                         │
│        整体目标: {main_context}                               │
│        {上章概要 if i>0 else ""}                              │
│        本章: {章节标题}                                       │
│        搜索资料: {search_pool[章节]}                          │
│        预估字数: {est_words}                                  │
│        """                                                   │
│                                                             │
│        if 章节.type == "business":                           │
│            prompt += business_writer 补充                     │
│                                                             │
│        content = call_llm(prompt)                            │
│        prev_summary = extract_summary(content)               │
│        sections_output.append(content)                       │
│                                                             │
│    → 文字版报告完成                                           │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 3: 图表补充（待后续计划执行）                            │
│                                                             │
│ chart_advisor.advise(full_report)                            │
│ → 推荐图表规格 → LLM 按规格生成 → 插入报告                   │
└─────────────────────────────────────────────────────────────┘
```

---

## 任务列表

### Task 1: 新增 `WorkflowState` 上下文管理器

**目标:** 创建一个管理串行写作上下文的数据结构，保存整体目标、上章摘要、搜索资料池

**文件:**
- 创建: `src/hermes_tools/workflow_state.py`
- 测试: `tests/hermes_tools/test_workflow_state.py`

**数据结构设计:**

```python
@dataclass
class ChapterContext:
    """单个章节的上下文。"""
    spec: SectionSpec          # 章节规格
    search_data: str           # 本章搜索资料（搜索阶段预填）
    generated_content: str     # 生成的文字内容（写作阶段填充）
    summary: str               # 自动提取的摘要（供下一章使用）

@dataclass
class WorkflowState:
    """串行写作的全局上下文。"""
    topic: str
    report_type: str
    main_context: str          # 整体目标（LLM 生成）

    sections: Dict[str, Any]   # 来自 ReportPlan
    chapter_contexts: Dict[str, ChapterContext]  # 各章节上下文
    
    prev_summary: str          # 上一章的摘要（串行时更新）
    
    def set_chapter_search(self, title: str, data: str) -> None
    def get_chapter_prompt(self, title: str) -> str  # 构建三要素 prompt
    def set_chapter_result(self, title: str, content: str, summary: str) -> None
```

**验证条件:**
- 可以正确存储和读取各章节的搜索资料
- get_chapter_prompt 返回的 prompt 包含整体目标 + 上章摘要 + 本章搜索资料
- 第一章调用时 prev_summary 为空字符串
- 后续章节调用时 prev_summary 有值

---

### Task 2: 改造 `report_planner.create_plan()` 加样本搜索

**目标:** 在规划大纲前，先搜索同类优质文档样本作为参考

**文件:**
- 修改: `src/planning/report_planner.py`（新增 `search_samples()` 和改造 `create_plan()`）
- 测试: `tests/planning/test_report_planner.py`

**关键修改:**

```python
class HermesReportPlanner(BaseComponent):
    
    def search_samples(self, topic: str, report_type: str) -> List[str]:
        """搜索同类文档样本的结构摘要。"""
        queries = [
            f"{topic} {report_type} report template structure",
            f"{topic} 报告 范文 大纲",
        ]
        # 使用 web_searcher.search() 委托搜索
        # 返回样本结构摘要列表
    
    def create_plan(
        self,
        topic: str,
        report_type: Optional[str] = None,
        language: Optional[str] = None,
        samples: Optional[List[str]] = None,  # ← 新增参数
    ) -> ReportPlan:
        """生成报告计划，可选参考样本。"""
        ...
```

**验证条件:**
- create_plan 无 samples 时行为不变（向后兼容）
- create_plan 有 samples 时，生成的章节结构更丰富
- search_samples 返回非空结果（或优雅降级为空列表）

---

### Task 3: 重写 `content_generator.generate_from_plan()` — 搜索并行 + 写作串行

**目标:** 这是最核心的改动。将原来的「for 每章: 搜索+LLM」改成两阶段。

**文件:**
- 重写: `src/planning/content_generator.py`（核心方法重写）
- 测试: `tests/planning/test_content_generator.py`

**新流程:**

```python
class HermesContentGenerator(BaseComponent):

    def generate_from_plan(self, plan: ReportPlan) -> GeneratedReport:
        """两阶段生成：先并行搜索，再串行写作。"""
        
        # ── 创建 WorkflowState ──
        state = WorkflowState(topic=plan.topic, report_type=plan.report_type)
        
        # ── Step 1: 并行搜索所有章节资料 ──
        search_tasks = []
        for section in plan.sections:
            task = web_searcher.prepare(section.title + " " + plan.topic)
            search_tasks.append(task)  # ← 并行委托
        
        results = parallel_execute(search_tasks)  # 并行搜索
        for i, section in enumerate(plan.sections):
            state.set_chapter_search(section.title, results[i])
        
        # ── Step 2: 串行逐章写作 ──
        for i, section in enumerate(plan.sections):
            prompt = state.get_chapter_prompt(section.title)
            
            # 章节类型判断
            if section.section_type == "business":
                business_task = business_writer.prepare(
                    scenario=auto_detect(section.title),
                    key_findings=extract_key_findings(...),
                )
                prompt += f"\n【业务视角补充】\n{business_task.goal}\n"
            
            content = call_llm(prompt)
            summary = extract_first_paragraph(content)
            
            state.set_chapter_result(section.title, content, summary)
        
        # ── 组装报告（无图表） ──
        report = assemble_report(plan, state)
        return report
```

**关键方法:**

- `_build_chapter_prompt(section, state)` → 构建三要素 prompt
- `_detect_business_scenario(title)` → 自动识别业务场景
- `_extract_summary(content)` → 提取章节摘要（供下一章引用）
- `_assemble_report(plan, state)` → 拼接完整文字报告

**验证条件:**
- 第 N 章的 prompt 中包含第 N-1 章的摘要
- 业务章节的 prompt 中被注入了 copywriting 指导
- 生成的报告是纯文字版（无图表）
- 章节之间逻辑连贯，无矛盾

---

### Task 4: 更新 `workflow_orchestrator` 适配新流程

**目标:** 确保工作流编排器能调用改造后的内容生成器

**文件:**
- 修改: `src/integration/workflow_orchestrator.py`

**关键修改:**
- 在 orchestration 流程中，Phase 1 先搜索样本再规划
- Phase 2 调用新的 `generate_from_plan`（内部已实现搜索并行+写作串行）
- Phase 3 留空占位（待后续图表补充计划）

---

### Task 5: 运行全量测试

**目标:** 确保所有改动不影响已有功能

**文件:**
- 全量测试命令: `./run_tests.sh`

**验证条件:**
- 新增的 3 个测试文件全部通过
- 原有测试不受影响
- `hermes-run.sh -m pytest tests/ -v` 全部绿色

---

## 关于「放弃并行」的实际影响分析

| 维度 | 原方案（并行） | 新方案（搜索并行+写作串行） |
|------|-------------|------------------------|
| 搜索速度 | 串行搜，一章等一章 | ✅ 并行搜，全部同时完成 |
| 写作速度 | ✅ 并行写，N章同时 | 🐢 串行写，逐章等待 |
| 连贯性 | ❌ 各章可能矛盾 | ✅ 整体+上章+本章，自然连贯 |
| 质量 | ❌ 各章独立，口径不一 | ✅ 统一叙事线 |

**实际影响估算（5 章报告）：**
- 串行写作：5 × 15秒（LLM单次）= 75秒
- 并行写作：max(15秒) = 15秒
- 差距：约 60秒

对于一份完整报告的生成（规划+搜索+写作+评估），写作环节占比不大，**串行的质量收益远超这 60 秒的成本**。

---

## 阶段 3 图表补充（预留，本次不执行）

全文完成后统一补图表的计划将在本次重构完成后单独制定。`chart_advisor.py` 已就绪，等待被调用。
