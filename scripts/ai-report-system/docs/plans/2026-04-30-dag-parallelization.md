# DAG 并行化章节生成 — 实施计划

> **目标：** 将 content_generator.py Phase 1 从纯串行改为 3 层 DAG 并行，保持上下文连贯性和主题一致性。

**架构：** 利用现有 `section_type`（intro/body/conclusion）字段自动推导依赖关系，同层并行通过 delegate_task 实现，层间传递摘要链确保连贯。

**风险控制：** 不改变现有 API 签名（`generate_from_plan()` 输入输出不变），降级路径保留（非 3 层章节配置自动回退串行）。

---

### Task 1: DAG 层推导函数

**目标：** 根据 sections + chapter_prompts 推导 3 层 DAG 结构。

**文件：** 新增 `src/planning/dag_utils.py`

**设计：**

```python
def derive_dag_layers(
    sections: list[SectionSpec],
    chapter_prompts: list[dict] | None = None,
) -> list[list[int]]:
    """
    从章节规格推导 DAG 分层。
    
    规则（不使用硬编码章节标题，仅使用 section_type）：
    - Layer 0: intro 类型 → 无依赖
    - Layer 1: body/analysis 类型 → 依赖 Layer 0
    - Layer 2: conclusion/appendix 类型 → 依赖全部前层
    
    Returns:
        分层结构 [[idx0, idx1], [idx2, idx3, idx4], [idx5]]
        每层内的章节可并行，层间串行。
    """
```

**降级规则：**
- 如果只有 1-2 章 → 返回 `[[0, 1]]` 单层（退化为并行，无层间依赖）
- 如果没有 intro 层 → Layer 0 为空，Layer 1 为所有 body 章节
- 如果没有 conclusion → Layer 2 为空，不生成
- 总章数 ≤ 3 → 直接返回 `[[...全部索引...]]` 单层

---

### Task 2: 并行章节写入函数

**目标：** 在 content_generator.py 中新增并行写入层的方法。

**文件：** `src/planning/content_generator.py`

**新增方法：**

```python
def _write_chapters_parallel(
    self,
    layer_indices: list[int],
    sections: list[SectionSpec],
    state: WorkflowState,
    plan: ReportPlan,
    retries: int,
    all_prev_summaries: list[str],
) -> list[GeneratedSection]:
    """
    并行写入一层的所有章节。
    
    实现：
    - 如果 len(layer) == 1 → 复用现有 _write_chapter() 串行路径
    - 如果 len(layer) >= 2 → batch delegate_task
      每个章节的 context 包含：
        - report_goal（主题锚定）
        - writing_role（语气锚定）
        - ALL_prev_summaries（前层摘要，而非只前一个章节）
        - 本层其他章节的 title + writing_intent（避免内容重叠）
    """
```

**主题锚定增强：**
在 `_build_chapter_context_json()` 中新增 `sibling_chapters` 字段，列出同层其他章节的标题和写作意图，让 LLM 感知并行写作语境：

```python
"sibling_chapters": [
    {"title": "xxx", "writing_intent": "yyy"},
    ...
]
```

---

### Task 3: 重构 generate_from_plan() Phase 1

**目标：** 替换串行循环为 DAG 驱动。

**文件：** `src/planning/content_generator.py`

**改动：**

```python
# Phase 1 原代码（~20 行串行循环）→ 改为：
from .dag_utils import derive_dag_layers

# 推导 DAG 层
dag_layers = derive_dag_layers(plan.sections, chapter_prompts)
logger.info("[Phase 1] DAG %d 层: %s", len(dag_layers),
            " → ".join(f"[{','.join(str(i+1) for i in layer)}]" for layer in dag_layers))

sections: list[GeneratedSection] = []
all_prev_summaries: list[str] = []

for layer_idx, layer in enumerate(dag_layers):
    logger.info("  Layer %d: 章节 %s", layer_idx,
                ", ".join(f"'{plan.sections[i].title}'" for i in layer))
    
    layer_sections = self._write_chapters_parallel(
        layer, plan.sections, state, plan, retries,
        all_prev_summaries,
    )
    sections.extend(layer_sections)
    
    # 收集本层摘要
    for i in layer:
        sec = plan.sections[i]
        summary = next(
            (s.content[:200].replace("\n", " ")
             for s in layer_sections if s.spec.title == sec.title),
            f"「{sec.title}」: (内容为空)"
        )
        all_prev_summaries.append(f"「{sec.title}」: {summary}")
```

**关键约束：** 章节顺序必须保持（`sections` 列表顺序与 `plan.sections` 一致），不能因为并行执行而打乱报告结构。使用 `(index, section)` 索引确保重排回原始顺序。

---

### Task 4: 更新 COMPONENT_DESCRIPTION + 测试

**目标：** 更新版本号和单元测试。

**文件：** 
- `src/planning/content_generator.py`（COMPONENT_DESCRIPTION 改为 "基于DAG并行化的报告内容生成器，支持3层并行章节写入"）
- `tests/planning/test_content_generator_v3.py`（更新描述断言）
- `tests/planning/test_dag_utils.py`（新增 DAG 推导测试）

**DAG 推导测试用例：**
```
测试 1: 3 intro + 3 body + 1 conclusion → [0,1,2], [3,4,5], [6]
测试 2: 0 intro + 4 body + 0 conclusion → [0,1,2,3] (单层)
测试 3: 1 intro + 0 body + 1 conclusion → [0], [1]
测试 4: 仅 2 章 → [0,1] (单层并行)
测试 5: 混合 section_type 未知 → 全部 body → 单层
测试 6: 空列表 → []
```

**并行写入测试（mock delegate_task）：**
```
测试 7: 2 同层章节 → 并行委托 → 收到 2 个结果
测试 8: 1 章节层 → 走串行路径（复用现有 _write_chapter）
测试 9: delegate_task 异常 → 降级到 call_llm
```

---

### Task 5: 更新 engineering.md

在 engineering.md 中新增 DAG 并行化文档章节。

---

### 不变式验证清单

- [ ] `generate_from_plan()` 签名不变 → 编排器零改动
- [ ] 报告章节顺序与 serial 输出一致
- [ ] 每章 prompt 仍有 report_goal + writing_role 锚定
- [ ] 每章仍能看到前层摘要（主题连贯）
- [ ] 新增同层章节写作意图交换（避免内容重叠）
- [ ] 降级路径完整保留（非 DAG 配置自动退化为串行）
- [ ] 单元测试覆盖正常/边界/异常
