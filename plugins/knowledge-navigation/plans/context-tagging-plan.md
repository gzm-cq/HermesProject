# 上下文语义标签化 — 实现方案

> 参考来源：`asgeirtj/system_prompts_leaks` → Cursor system prompt 的 `<user_query>` / `<context>` / `<metadata>` 标签分隔
> 目标：用 XML 标签包装不同来源的上下文，让 LLM 清晰区分"用户说的" vs "插件自动注入的"

---

## 一、现状问题

当前 `pre_llm_call` 将 Hindsight 回忆、知识树节点、技能全文等自动注入到 user message，格式为纯文本拼接：

```
【上下文】
昨日的 cron-catchup-repair 已修复...
【知识】
Cron: Hermes cron 追赶设计
【技能】
hermes-project-workflow: ...
```

**问题：**
1. LLM 无法区分哪些内容是用户主动输入、哪些是插件自动注入
2. 各种来源的内容混杂在一起，降低 LLM 对用户原始意图的注意力
3. `pre_llm_call` 不能改 system prompt，只能注入到 user message，导致 user message 膨胀
4. 后期 trace/日志分析无法按来源拆解

## 二、参考策略（Cursor）

Cursor 在 system prompt 中定义了一系列语义标签来包裹不同来源的上下文：

| 标签 | 用途 |
|------|------|
| `<user_query>` | 用户原始输入 |
| `<context>` | 自动附带的当前状态 |
| `<metadata>` | 编辑器状态信息 |
| `<system_notification>` | 系统通知 |
| `<tone_and_style>` | 语气/风格控制 |
| `<making_code_changes>` | 代码变更上下文 |

效果：LLM 在阅读提示词时能瞬间理解"这是用户自己说的" vs "这是系统自动添加的背景信息"，从而正确处理优先级。

## 三、实现方案

### 改动范围

`knowledge-navigation/src/knowledge_navigation/core/hooks.py` 中的上下文组装函数（推测为 `_format_context()` 或等效函数）。**单文件改动，~50 行新代码。**

### 新增函数：`_format_xml_context()`

```python
"""将不同来源的上下文包装为 XML 标签格式"""

CONTEXT_BLOCKS = {
    "user_query": """<user_query>{content}</user_query>""",
    
    "memory": """<recalled_memory source="hindsight" count="{count}" score_avg="{avg_score:.2f}">
{content}
</recalled_memory>""",
    
    "knowledge_tree": """<knowledge source="knowledge_tree" count="{count}">
{content}
</knowledge>""",
    
    "skills": """<auto_loaded_skills count="{count}" intent="{intent}">
{content}
</auto_loaded_skills>""",
    
    "system_state": """<system_state>
{content}
</system_state>""",
}

def _format_xml_context(hs_items, kt_items, skills, system_state, user_message, intent):
    """将各来源内容组装为带 XML 标签的结构"""
    blocks = []
    
    # 1. 用户原始消息（始终第一块）
    blocks.append(CONTEXT_BLOCKS["user_query"].format(
        content=user_message
    ))
    
    # 2. Hindsight 回忆（依 intent 等级决定是否包含）
    if hs_items:
        avg_score = sum(item.get("score", 0) for item in hs_items) / max(len(hs_items), 1)
        blocks.append(CONTEXT_BLOCKS["memory"].format(
            content=_format_memory_items(hs_items),
            count=len(hs_items),
            avg_score=avg_score,
        ))
    
    # 3. 知识树节点
    if kt_items:
        blocks.append(CONTEXT_BLOCKS["knowledge_tree"].format(
            content=_format_kt_items(kt_items),
            count=len(kt_items),
        ))
    
    # 4. 技能加载
    if skills:
        blocks.append(CONTEXT_BLOCKS["skills"].format(
            content=_format_skills(skills),
            count=len(skills),
            intent=intent,
        ))
    
    # 5. 系统状态（每次注入）
    blocks.append(CONTEXT_BLOCKS["system_state"].format(
        content=_format_system_state(system_state)
    ))
    
    return "\n\n".join(blocks)
```

### 注入策略

`_inject_memory()` 中调用 `_format_xml_context()` 替代当前的纯文本拼接：

```python
def _inject_memory(message, config):
    intent = _classify_intent(message)
    
    # 按 intent 等级决定 recall 深度（参考 memory-level-plan.md）
    hs_items, kt_items, skills = _recall_by_intent(message, intent)
    
    # 系统状态提取
    system_state = {
        "pwd": os.getcwd(),
        "provider": config.get("provider", ""),
        "model": config.get("model", ""),
        "time": datetime.now().isoformat(),
    }
    
    # 标签化组装
    injected = _format_xml_context(
        hs_items=hs_items,
        kt_items=kt_items,
        skills=skills,
        system_state=system_state,
        user_message=message,
        intent=intent,
    )
    
    return injected  # 注入到 user message
```

### 最终注入到 user message 的格式

```xml
<user_query>
检查一下 memory-cleanup 昨天的状态
</user_query>

<recalled_memory source="hindsight" count="3" score_avg="0.52">
memory-cleanup-daily cron (c194bd1bc26e) 于 2026-06-23T14:07 失败
根因: daily_dryrun.sh 脚本文件缺失
修复: cron-catchup-repair.sh 已创建，手动追赶确认 6/24 21:24 恢复
</recalled_memory>

<knowledge source="knowledge_tree" count="2">
Cron: Hermes cron 追赶设计 (cover+catchup+detect 三层)
Cron: cron_common.sh 框架 (日志/flock/通知/状态文件)
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

### 与当前系统的兼容

当前的注入是通过 `pre_llm_call` 返回新的 `messages` 数组，将标签化内容追加为用户消息的最后一条 `role: "user"` 内容。Hermes gateway 在将消息发给 LLM 时会把这一条和用户原始消息一起发送。

**不需要改动除了 `_format_context()` 之外的其他代码。** 下游的 trace.log 记录、post-processing 等都只关心 `injected_count` 和字符数，不关心格式。

## 四、token 开销分析

每个标签新增 ~50-100 chars 的固定开销。按每轮平均注入 ~1,044 chars 计算：

```
当前: 1,044 chars/轮
改为标签化: 1,044 + 100(标签) = 1,144 chars/轮
增幅: ~9.6%
```

对于 190K token 的上下文窗口，完全可忽略。

## 五、兼容性检查

| 下游组件 | 依赖字符串格式？ | 影响 |
|---------|----------------|------|
| trace.log 统计 | 只关心 injected_count / total_chars | 不影响（字符数不变或略增） |
| Hindsight retain | 不读注入内容 | 不影响 |
| pre_llm_call 返回值 | 返回 messages 数组 | 不影响（语义不变） |
| 用户可见回复 | 用户看不到注入内容 | 不影响 |
| 测试用例 | 如果有格式断言 | 需更新 |

**结论：无兼容性问题。** 如果测试用例中有对注入格式的硬编码断言，需同步更新。

## 六、调试与日志

标签化格式使 trace.log 分析更清晰——可以按标签 grep：

```bash
# 只看用户原始消息
grep '<user_query>' trace.log

# 只看召回记忆
grep '<recalled_memory' trace.log

# 统计各来源注入量
grep -c '<knowledge' trace.log
grep -c '<loaded_skills' trace.log
```

## 七、不做事项（NON-GOALS）

- 不改 Hermes gateway 核心代码
- 不改 system prompt（仍然只在 user message 注入）
- 不做 post-processing 标签解析（标签仅对 LLM 可见）
- 不涉及 Hindsight 侧的任何改动