# 知识导航优化 — SPEC 实施计划

> ⚠️ **已废弃**：本实施计划描述的规则式 `_classify_intent` 方案已被 LLM Router 方案替代。
> 参见 [SPEC-router.md](../plans/SPEC-router.md)
>
> **目标**：合并上下文标签化和 Memory 分级两个计划，对 knowledge-navigation 插件做两处核心优化
> **架构**：纯规则 intent 分类 + 语义标签化上下文注入，只改 hooks.py，不涉及 Hindsight/KT 核心
> **技术栈**：Python 3.10+, re, html
> **来源**：context-tagging-plan.md + memory-level-plan.md

---

## 一、执行策略

```
Task 1 ──→ Task 2 ──→ Task 3 ──→ Task 4
(分类器)    (意图门控)  (标签化组装) (测试)
```

全部串行，因为 3 个任务都修改同一个文件 `hooks.py`，必须分批次避免合并冲突。

---

## 二、任务清单

### Task 1（~15min）：添加 `_classify_intent()` 纯规则意图分类器

| 字段 | 内容 |
|------|------|
| **来源** | memory-level-plan.md §三 |
| **当前状态** | 所有消息无差别触发三路并行 recall（Hindsight + KT + SkillMatch） |
| **目标状态** | 基于消息文本特征分类为 generic/task/personal 三级 |
| **期望目标** | 问候/概念性问题不触发 Hindsight+KT recall，节省 ~800ms/轮 |
| **改动位置** | `hooks.py` 新增函数 `_classify_intent()`，在 `_do_hindsight_recall`/`_do_kt_recall`/`_do_skill_match` 后 |
| **工作量** | ~15min |
| **检查方法** | 单元测试：10+ 边界消息分类验证 |
| **实现方式** | 自实现，纯规则（零 LLM 调用） |

规则逻辑：
```
LEVEL 2 (personal): 包含"记得""上次""之前""我刚说的"等 → 全面 recall
LEVEL 0 (generic):  问候/概念性问题/极短消息 → 跳过 Hindsight+KT
LEVEL 1 (task):     默认 → 正常 recall（当前行为）
```

---

### Task 2（~30min）：意图门控 — 修改 `pre_llm_call()` 按意图分级 recall

| 字段 | 内容 |
|------|------|
| **来源** | memory-level-plan.md §三 |
| **当前状态** | pre_llm_call 对所有消息都走三路 recall |
| **目标状态** | generic 跳过 HS+KT，task 降低 recall limit，personal 全量 + 显式引用 |
| **期望目标** | 加权平均每轮节省 ~360ms |
| **改动位置** | `hooks.py` 的 `pre_llm_call()` 函数，在 executor 之前插入 intent 门控 |
| **工作量** | ~30min |
| **检查方法** | 单元测试：generic 消息不触发 recall；personal 消息触发全量 |
| **实现方式** | 自实现，修改 pre_llm_call 中的分支逻辑 |

关键分支逻辑：
```python
# generic (LEVEL 0): 只跑 skill matcher，不调用 HS/KT recall
if intent == "generic":
    _skill_context = _do_skill_match(user_message)
    return _skill_context if _skill_context else None

# task (LEVEL 1): 正常 recall，降低 max_results
# personal (LEVEL 2): 全量 recall
```

---

### Task 3（~30min）：语义标签化上下文组装

| 字段 | 内容 |
|------|------|
| **来源** | context-tagging-plan.md §三 |
| **当前状态** | 统一 `<memory-context>` 块包所有来源；skill 单独 `<auto_loaded_skills>`；无 `<user_query>`、无 `<system_state>` |
| **目标状态** | 分来源的语义标签：`<user_query>` / `<recalled_memory>` / `<knowledge>` / `<loaded_skills>` / `<system_state>` |
| **期望目标** | LLM 能清晰区分"用户说的"vs"插件自动注入的"，调试时可按标签 grep |
| **改动位置** | `hooks.py` 的 `pre_llm_call()` 尾部上下文组装段 |
| **工作量** | ~30min |
| **检查方法** | 跑测试验证 XML 格式输出 |
| **实现方式** | 自实现，替换 `format_context_lines()` 调用为分段组装 |

输出格式：
```xml
<user_query>
检查一下 memory-cleanup 昨天的状态
</user_query>

<recalled_memory source="hindsight" count="2" score_avg="0.65">
  <memory source="hindsight" node_id="abc123">修复: cron-catchup-repair.sh 已创建</memory>
  <memory source="hindsight" node_id="def456">根因: daily_dryrun.sh 脚本文件缺失</memory>
</recalled_memory>

<knowledge source="knowledge_tree" count="1">
  <memory source="knowledge_tree" node_id="kt1">Cron: Hermes cron 追赶设计</memory>
</knowledge>

<auto_loaded_skills count="1" intent="task">
hermes-project-workflow: HermesProject 修改与部署工作流
</auto_loaded_skills>

<system_state>
pwd: /root
provider: custom/s-deepseek-v4-flash
time: 2026-06-24T22:30:00
</system_state>
```

---

### Task 4（~30min）：更新测试用例

| 字段 | 内容 |
|------|------|
| **来源** | 新增功能的回归保障 |
| **当前状态** | 现有测试验证 `<memory-context>` 格式 |
| **目标状态** | 新增 `_classify_intent` 测试 + 更新 XML 格式断言 |
| **改动位置** | `tests/test_hooks.py` |
| **工作量** | ~30min |
| **检查方法** | pytest 全部通过 |
| **实现方式** | 新增 TestClassifyIntent 类 + 更新现有测试的格式断言 |

---

## 三、关键接口约定

- `_classify_intent(message: str) -> str` — 返回 `"generic" | "task" | "personal"`
- `pre_llm_call()` 返回值仍是 `str | None`，兼容 Hermes hook 接口
- XML 标签仅对 LLM 可见，不涉及下游解析

## 四、实施路线图

| 步骤 | 内容 | 验证 |
|------|------|------|
| 1 | 添加 `_classify_intent()` + 单元测试 | pytest test_classify_intent |
| 2 | 修改 `pre_llm_call()` 意图门控 | pytest test_hooks.py |
| 3 | 修改上下文组装为分来源 XML | pytest test_hooks.py |
| 4 | 更新所有测试格式断言 | pytest 全部通过 |

## 五、风险与应对

| 风险 | 概率 | 应对 |
|------|------|------|
| 规则误分类导致重要记忆被跳过 | 低 | generic 仍保留 skill matcher 保底 |
| 现有测试 XML 格式断言失败 | 中 | 同步更新测试断言 |
| 下游 trace 分析依赖格式 | 低 | 标签化后更易 grep，不影响 log 字段 |

## 六、交付清单

- [ ] `hooks.py` — 新增 `_classify_intent()`
- [ ] `hooks.py` — 修改 `pre_llm_call()` 意图门控 + 标签化组装
- [ ] `test_hooks.py` — 新增 `TestClassifyIntent` + 更新格式断言
- [ ] 所有 pytest 通过
