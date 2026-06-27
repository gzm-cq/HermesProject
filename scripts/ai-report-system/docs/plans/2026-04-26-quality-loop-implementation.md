# AI报告系统 — 质量闭环与迭代优化实施计划

> **执行方式:** subagent-driven-development，每任务一个子代理，两阶段审查

**目标:** 在已有「搜索并行+写作串行」的文字生成基础上，增加三层质量闭环：
1. Phase 2 — 逐章质量闭环（搜索→诊断→修正）
2. Phase 3 — 全文质量闭环（评估→并行修复→循环）
3. Phase 4 — 图表生成与校验（Markdown 图表，规则校验）

**前提条件（已完成，不在此计划内）:**
- Phase 1 文字生成已完成（`content_generator.py` v3）
- `WorkflowState` 上下文管理已就绪
- `web_searcher` 委托式搜索已就绪
- `chart_advisor` 图表推荐已就绪

**涉及修改的文件:**
- `src/planning/content_generator.py` — 增加质量闭环逻辑
- `src/hermes_tools/quality_loop.py` — 新增（质量闭环核心）
- `src/hermes_tools/chart_generator.py` — 新增（Markdown 图表生成+校验）
- `src/integration/workflow_orchestrator.py` — 重写编排流程
- `tests/hermes_tools/test_quality_loop.py` — 新测试
- `tests/hermes_tools/test_chart_generator.py` — 新测试

---

## 整体流程图

```
用户输入报告主题
    │
Phase 1: 首次生成（已完成）
    ├─ 搜索样本 → 规划大纲
    ├─ 并行搜索资料
    ├─ 串行逐章写作（整体+上章+本章）
    └─ 文字版报告完成
    │
    ▼
Phase 2: 逐章质量闭环 ← 本次实施
    for each 章节:
        quality_score >= 0.6? → 跳过
        quality_score < 0.6:
            ├─ web_search(本章主题) → 获取新资料
            ├─ LLM 诊断:
            │   ├─ 跑题 → 重写（修正方向）
            │   └─ 内容不足 → 增量补充（保留原文+插入新内容）
            └─ 更新章节
    │
    ▼
Phase 3: 全文质量闭环 ← 本次实施
    loop (max 5 次 or score ≥ 80):
        ├─ 全报告评估 → score
        ├─ 标记不合格章节列表
        ├─ 并行修复（带衔接锚点）
        │   for each 不合格章节:
        │       ├─ 锚点 = 上章尾 + 下章首（来自原始版本）
        │       └─ 执行逐章修正
        ├─ 全文一致性检查
        └─ 再次评估
    │
    ▼
Phase 4: 图表生成与校验 ← 本次实施
    ├─ chart_advisor.advise() → 推荐图表
    ├─ for each 推荐:
    │   ├─ LLM 生成 Markdown 图表（表格/柱状图/折线图等）
    │   └─ 规则校验（类型/配色/尺寸/网格）
    │       ├─ 通过 → 插入报告
    │       └─ 不通过 → LLM 按反馈修正
    └─ 报告完成 ✅
```

---

## 任务列表

### Task 1: 新建 `QualityLoop` — 逐章质量闭环核心

**目标:** 创建质量闭环的核心模块，提供「搜索→诊断→修正」三步骤

**文件:**
- 创建: `src/hermes_tools/quality_loop.py`
- 测试: `tests/hermes_tools/test_quality_loop.py`

**数据结构设计:**

```python
@dataclass
class ChapterDiagnosis:
    """章节诊断结果。"""
    title: str
    score_before: float        # 修正前质量分
    diagnosis: str              # off_topic / insufficient / good
    reason: str                 # 判断依据
    suggested_action: str       # rewrite / enrich / skip
    search_data: str = ""       # 新搜索到的资料

@dataclass
class ChapterFixResult:
    """章节修正结果。"""
    title: str
    diagnosis: ChapterDiagnosis
    original_content: str
    fixed_content: str
    score_after: float
    attempts: int = 1

class QualityLoop:
    """逐章质量闭环。
    
    三步法：
    1. search(query) → 获取额外资料
    2. diagnose(content, spec, new_data) → 诊断问题
    3. fix(content, diagnosis) → 修正内容
    """
    
    QUALITY_THRESHOLD = 0.6     # 质量阈值
    DIAGNOSIS_PROMPT = """...""" # 诊断 prompt 模板
    
    def diagnose(self, content, spec, search_data) -> ChapterDiagnosis
    def fix(self, content, diagnosis) -> str
    def run_chapter(self, state, title, searcher) -> ChapterFixResult
```

**关键逻辑 — 诊断 prompt:**

```
你是一个报告质量诊断专家。请分析以下章节内容，判断质量问题的类型。

章节标题：{title}
预估字数：{estimated_words}

已写内容：
{content}

补充搜索资料：
{search_data}

请返回下列 JSON 格式的诊断结果：
{
  "diagnosis": "off_topic" 或 "insufficient" 或 "good",
  "reason": "判断依据的简短说明",
  "suggested_action": "rewrite" 或 "enrich" 或 "skip"
}

判断标准：
- off_topic: 内容偏离了标题「{title}」的主题方向
- insufficient: 主题正确，但内容单薄、缺乏具体数据和细节
- good: 内容充实、主题一致，不需要修改

注意：重点对比标题与内容是否一致来判断是否跑题。
```

**验证条件:**
- `diagnose("标题讲A，内容写B", ...)` → diagnosis="off_topic"
- `diagnose("标题讲A，内容写A但只有50字", ...)` → diagnosis="insufficient"
- `diagnose("标题讲A，内容写A有500字有数据", ...)` → diagnosis="good"
- `run_chapter` 对 quality≥0.6 的章节直接跳过
- `run_chapter` 对 quality<0.6 的章节执行诊断+修正

---

### Task 2: 改造 `content_generator` — 集成逐章质量闭环

**目标:** 在 Phase 1 首次生成后，自动执行 Phase 2 逐章质量闭环

**文件:**
- 修改: `src/planning/content_generator.py`
- 测试: `tests/planning/test_content_generator_v3.py`（追加测试）

**修改点:**

```python
class HermesContentGenerator(BaseComponent):
    
    def generate_from_plan(self, plan) -> GeneratedReport:
        # 现有 Phase 1 代码不变...
        
        # ── Phase 2: 逐章质量闭环（本次新增） ──
        state = self._run_quality_loop(state, plan)
        
        # 组装报告
        return self._assemble_report(plan, state, sections, start_time)
    
    def _run_quality_loop(self, state, plan) -> WorkflowState:
        """逐章质量闭环。"""
        quality_loop = QualityLoop()
        for section_spec in plan.sections:
            result = quality_loop.run_chapter(
                state, section_spec.title, self._searcher,
            )
            if result and result.score_after > 0:
                # 更新章节质量
                pass
        return state
```

**验证条件:**
- 高质量章节（≥0.6）直接跳过
- 低质量章节触发搜索+诊断+修正
- 修正后章节内容更新到 WorkflowState

---

### Task 3: 新建 `FullReportLoop` — 全文质量闭环

**目标:** 创建全文闭环核心，实现「评估→标记→并行修复→一致性检查→循环」

**文件:**
- 在 `src/hermes_tools/quality_loop.py` 追加
- 测试: `tests/hermes_tools/test_quality_loop.py`（追加）

**核心逻辑:**

```python
class FullReportLoop:
    """全文质量闭环。
    
    流程:
    1. 全报告评估 → 章节评分列表
    2. 标记不合格章节
    3. 并行修复（带衔接锚点）
    4. 一致性检查
    5. 再次评估，循环
    """
    
    MAX_ITERATIONS = 5
    PASS_THRESHOLD = 0.8
    
    def run(self, state, plan, searcher) -> WorkflowState:
        for iteration in range(1, self.MAX_ITERATIONS + 1):
            # 1. 评估各章节质量
            scores = self._evaluate_all(state)
            
            # 2. 检查是否通过
            avg_score = sum(scores.values()) / len(scores)
            if avg_score >= self.PASS_THRESHOLD:
                logger.info("全文质量通过: avg=%.2f", avg_score)
                break
            
            # 3. 标记不合格
            failed = [t for t, s in scores.items() if s < 0.6]
            
            # 4. 并行修复（带衔接锚点）
            results = self._parallel_fix(state, failed, searcher)
            
            # 5. 一致性检查
            self._consistency_check(state)
            
            # 6. 检查是否停滞
            if iteration > 1 and not self._improved():
                logger.info("质量不再提升，提前终止")
                break
    
    def _build_anchor(self, state, title) -> str:
        """构建衔接锚点。"""
        chapters = state._chapter_order
        idx = chapters.index(title)
        
        prev_tail = ""
        if idx > 0:
            prev = state.chapter_contexts.get(chapters[idx - 1])
            if prev and prev.generated_content:
                prev_tail = prev.generated_content[-200:]
        
        next_head = ""
        if idx < len(chapters) - 1:
            next_ch = state.chapter_contexts.get(chapters[idx + 1])
            if next_ch:
                next_head = next_ch.generated_content[:200] or ""
        
        return f"上一章末尾：{prev_tail}\n\n下一章开头：{next_head}"
    
    def _parallel_fix(self, state, failed_titles, searcher):
        """并行修复所有不合格章节。"""
        quality = QualityLoop()
        # 所有不合格章节并行执行 run_chapter
        for title in failed_titles:
            result = quality.run_chapter(state, title, searcher)
            ...
```

**衔接锚点策略:**

```
不合格章节 [C2, C5, C7]
    │
    ├─ C2 锚点 = C1尾（合格，不变）+ C3头（合格，不变）
    ├─ C5 锚点 = C4尾（合格，不变）+ C6头（合格，不变）
    └─ C7 锚点 = C6尾（合格，不变）+ C8头（合格，不变）
    │
    全部可并行，互不依赖
```

**连续不合格的情况:**

```
不合格章节 [C2, C3]
    │
    ├─ C2 锚点 = C1尾（合格）+ C3头（原始版本）
    └─ C3 锚点 = C2尾（原始版本）+ C4头（合格）
    │
    依然可并行，只是锚点用的是原始版本而非重写版本
```

**一致性检查:**

```python
def _consistency_check(self, state):
    """检查全文逻辑一致性。"""
    full_text = state.get_full_text()
    
    # 规则检查：
    # 1. 重复段落检测（相似度 > 80%）
    # 2. 前后矛盾检测（同一个数据在不同地方不一致）
    # 3. 章节衔接检查（上一章末尾和下一章开头是否突兀）
    
    # 发现的问题统一修复
```

**验证条件:**
- 单次迭代中不合格章节被正确标记
- 所有不合格章节并行修复成功
- 连续不合格章节使用原始版本锚点
- 一致性检查发现重复/矛盾问题
- 循环在第 5 次或评分 ≥80 时终止
- 连续两次评分不涨时提前终止

---

### Task 4: 新建 `ChartGenerator` — Markdown 图表生成与校验

**目标:** 在全文文字定稿后，根据 `chart_advisor` 推荐生成 Markdown 图表

**文件:**
- 创建: `src/hermes_tools/chart_generator.py`
- 测试: `tests/hermes_tools/test_chart_generator.py`

**设计原则:**
- 只生成 Markdown 格式（表格、柱状图用文字模拟、折线图用文字模拟）
- 不生成任何视觉图片/HTML/SVG
- 规则校验自动执行，不走 LLM

**数据结构:**

```python
@dataclass
class ChartValidationResult:
    """图表校验结果。"""
    chart_spec: ChartSpec
    chart_markdown: str          # LLM 生成的 Markdown
    passed: bool                 # 规则校验是否通过
    issues: List[str]            # 违规项
    fix_attempts: int = 0

class ChartGenerator:
    """Markdown 图表生成器。
    
    流程：
    1. ChartAdvisor 推荐图表类型
    2. LLM 按规格生成 Markdown 图表
    3. 规则校验（不通过则 LLM 修正，最多 3 次）
    4. 插入报告
    """
    
    MAX_FIX_ATTEMPTS = 3
    
    def generate_for_report(self, state, plan) -> List[ChartValidationResult]
    def _validate(self, chart_md, spec) -> ChartValidationResult
    def _insert_into_report(self, state, spec, chart_md)
```

**规则校验项:**

```python
def _validate(self, chart_md, spec) -> ChartValidationResult:
    issues = []
    
    # 1. 图表类型检查
    if spec.chart_type == "table":
        if not re.search(r'\|.*\|.*\|', chart_md):
            issues.append("表格类型但缺少 Markdown 表格语法")
    
    elif spec.chart_type == "bar":
        if not re.search(r'[▇█▓▒░]', chart_md):
            issues.append("柱状图但缺少 ASCII 柱状条")
        if not re.search(r'\d+\s*[%％]', chart_md):
            issues.append("柱状图缺少数据标签")
    
    elif spec.chart_type == "line":
        if not re.search(r'[─━═]', chart_md):
            issues.append("折线图但缺少线条符号")
    
    # 2. 数据标签检查
    if not re.search(r'\d+', chart_md):
        issues.append("图表缺少具体数据")
    
    # 3. 标题检查
    if spec.title and spec.title not in chart_md:
        issues.append("缺少图表标题")
    
    return ChartValidationResult(
        chart_spec=spec,
        chart_markdown=chart_md,
        passed=len(issues) == 0,
        issues=issues,
    )
```

**LLM 生成 prompt 示例:**

````
请根据以下规格生成一个 Markdown 格式的图表（仅文字，不含任何图片/HTML/SVG）。

图表类型: bar
标题: 2025年AI芯片市场份额对比
配色: 深色主题
所需数据: 各厂商市场份额百分比

请输出格式如下的 Markdown（不要<html>/<svg>/<img>标签）：

## 图表: {标题}

{数据表格}

{柱状图，用文字条表示}  // 例如: NVIDIA ██████████████████████ 80%

{数据说明和来源}
````

**验证条件:**
- 生成的图表是纯 Markdown 格式
- 规则校验发现缺少标题/缺少数据/语法错误
- 不通过时 LLM 按反馈修正，最多 3 次
- 最终报告的图表部分全部通过校验

---

### Task 5: 重写 `workflow_orchestrator` — 集成四阶段流程

**目标:** 将编排器从现有的 5 阶段改成完整的 4 阶段报告生成流程

**文件:**
- 重写: `src/integration/workflow_orchestrator.py`

**新流程:**

```python
def run(self, topic, report_type=None, language=None, ...):
    """完整四阶段报告生成。"""
    
    # Phase 0: 样本搜索（已有）
    samples = self._planner.search_samples(topic, ...)
    
    # Phase 1: 规划 + 首次生成（已有）
    plan = self._planner.create_plan(topic, ..., samples=samples)
    report = self._generator.generate_from_plan(plan)
    
    # Phase 2: 逐章质量闭环（新增）
    report = self._run_chapter_quality_loop(report)
    
    # Phase 3: 全文质量闭环（新增）
    report = self._run_full_report_loop(report)
    
    # Phase 4: 图表生成（新增）
    report = self._generate_charts(report)
    
    return report

def _run_chapter_quality_loop(self, report) -> GeneratedReport
def _run_full_report_loop(self, report) -> GeneratedReport
def _generate_charts(self, report) -> GeneratedReport
```

**验证条件:**
- run() 输出包含完整文字 + 图表
- 每个阶段可独立跳过（配置项控制）
- 4 阶段全部完成后报告质量应高于首次生成

---

### Task 6: 全量测试

**文件:**
- 全量测试: `./run_tests.sh`
- 新增测试文件:
  - `tests/hermes_tools/test_quality_loop.py`
  - `tests/hermes_tools/test_chart_generator.py`

**预期结果:**
- 原有的 85 个测试全部通过
- 新增测试全部通过
- 覆盖率检查无空白

---

## 关键设计决策总结

| 决策 | 选择 | 理由 |
|------|------|------|
| 图表格式 | 纯 Markdown | 不引入视觉生成复杂度 |
| 图表校验 | 规则校验 + LLM 修正 | 免费快速，不走 LLM 视觉判断 |
| 全文修复并行度 | 锚点来自合格章节，可全并行 | 连续不合格章节用原始版本锚点 |
| 循环终止条件 | 5 次上限 或 ≥80 分 或 评分停滞 | 防死循环 + 保证质量 |
| 一致性检查 | 规则检查（重复/矛盾/衔接） | 轻量快速，不走 LLM |
