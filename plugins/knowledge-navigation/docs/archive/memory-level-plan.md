# Memory 分级应用 — 实现方案

> ⚠️ **已废弃**：本方案描述的规则式 `_classify_intent` 三级分类已被 LLM Router 方案替代。
> 参见 [SPEC-router.md](../plans/SPEC-router.md)
>
> 参考来源：`asgeirtj/system_prompts_leaks` → Claude Fable 5 memory 分级策略
> 目标：按消息类型决定 recall 深度，减少不必要的 LLM 调用和 token 开销

---

## 一、现状问题

knowledge-navigation 的 `pre_llm_call` 对所有消息**无差别三路并行 recall**：

- Hindsight recall（经验域）
- 知识树 recall（知识域）
- Skill matcher（技能注入）

用户说一句"你好"和一句"修复这个bug"触发相同的三路开销。从 trace.log 的历史数据看（2026-06-23），35 次 recall 中有 4 次空召回，大量通用短消息触发了不必要的全量 recall。

## 二、参考策略（Claude Fable 5）

Claude Fable 5 的 memory 应用规则：

| 场景 | 行为 |
|------|------|
| 通用/概念性问题（"什么是K8s"） | 0 条记忆 |
| 工作上下文（修 bug、配配置） | 静默应用相关记忆（不标记"回忆"） |
| 明确要求个性化（"记得我上次说的"） | 全面 recall + 显式引用 |
| 简单问候（"你好"、"hi"） | 只记名字 |
| 技术查询 | 匹配经验级别 |

核心原则：**默认不触发记忆，除非有理由。**

## 三、实现方案

### 改动范围

只改 `knowledge-navigation` 插件入口：`src/knowledge_navigation/core/hooks.py`

### 新增函数：`_classify_intent(message: str) -> str`

纯规则分类，零额外 LLM 调用。基于消息文本特征判别意图等级：

```
INTENT_LEVELS:
  "generic"  → 通用/问候/概念性问题 → LEVEL 0
  "task"     → 工作上下文            → LEVEL 1 （默认）
  "personal" → 明确引用历史          → LEVEL 2
```

**规则逻辑：**

```
1. 包含"记得""上次""之前""我刚说的"等 → personal（LEVEL 2）
2. 长度 < 6 字 + 问候词（你好/hi/hello）→ generic（LEVEL 0）
3. 包含"什么是""解释""说明""介绍" → generic（LEVEL 0）
4. 包含项目专属术语（cron/hindsight/dify/mcp/litellm/wiki） → task（LEVEL 1）
5. 长度 < 15 字且无项目术语 → generic（LEVEL 0）
6. 默认 → task（LEVEL 1）
```

**分类器代码结构：**

```python
"""新文件或 hooks.py 内新增"""

INTENT_KEYWORDS = {
    "personal":     ["记得", "上次", "之前", "我刚说的", "以前讨论",
                     "earlier", "previously", "as we discussed", "you mentioned"],
    "greeting":     ["你好", "您好", "hi", "hello", "hey", "早上好", "下午好"],
    "generic_q":    ["什么是", "解释", "说明", "介绍", "什么是",
                     "what is", "explain", "define", "introduce"],
    "project_term": ["cron", "hindsight", "dify", "mcp", "litellm", "wiki",
                     "skill", "soh", "health", "check", "repair", "catchup"],
}

def _classify_intent(message: str) -> str:
    """纯规则意图分类，零 LLM 调用。返回 'generic' | 'task' | 'personal'"""
    msg = message.strip()
    msg_lower = msg.lower()
    
    # LEVEL 2: 明确引用历史
    for kw in INTENT_KEYWORDS["personal"]:
        if kw in msg_lower:
            return "personal"
    
    # LEVEL 0: 短问候
    if len(msg) < 20:
        for kw in INTENT_KEYWORDS["greeting"]:
            if kw in msg_lower:
                return "generic"
    
    # LEVEL 0: 通用概念性问题
    for kw in INTENT_KEYWORDS["generic_q"]:
        if kw in msg_lower:
            return "generic"
    
    # LEVEL 1: 含项目术语
    for kw in INTENT_KEYWORDS["project_term"]:
        if kw in msg_lower:
            return "task"
    
    # 极短消息 → generic
    if len(msg) < 10:
        return "generic"
    
    # 默认走工作上下文
    return "task"
```

### 修改函数：`_inject_memory()` 的分支逻辑

```python
# 当前（伪代码）
def _inject_memory(message, config):
    # 三路并行，对所有消息无差别
    hs_items = hindsight_recall(query)
    kt_items = knowledge_tree_recall(query)
    skills = skill_matcher(query)
    return format_context(hs_items, kt_items, skills)

# 改为
def _inject_memory(message, config):
    intent = _classify_intent(message)
    
    if intent == "generic":
        # LEVEL 0: 只跑 skill matcher（仅匹配名+描述，不注入全文）
        skills = skill_matcher(message, full_content=False)
        return ""  # 不注入任何记忆内容
    
    elif intent == "personal":
        # LEVEL 2: 全面 recall + 显式引用
        hs_items = hindsight_recall(message)
        kt_items = knowledge_tree_recall(message)
        skills = skill_matcher(message, full_content=True)
        return format_context(hs_items, kt_items, skills, 
                              prefix="<context type='personal'>",
                              suffix="</context>")
    
    else:  # task
        # LEVEL 1: 正常 recall，静默注入（不标记"回忆"）
        hs_items = hindsight_recall(message, limit=5)  # 降低数量
        kt_items = knowledge_tree_recall(message, limit=3)
        skills = skill_matcher(message, full_content=True)
        return format_context(hs_items, kt_items, skills, 
                              prefix="<context type='task'>",
                              suffix="</context>")
```

### 动态 RRF 兼容

三路召回变为两路/一路后，RRF 融合的归一化分母需调整：

```python
def compute_rrf(items, intent: str):
    """根据 intent 动态选择 fusion 策略"""
    if intent == "generic":
        return []  # 跳过全部
    k = 60  # RRF constant
    if intent == "personal":
        # 三路 full
        pass
    else:  # task
        # 两路（Hindsight + KT），无 skill match 分数
        pass
```

## 四、性能收益估算

| 等级 | 占比（估算） | 节省时间/轮 |
|------|------------|------------|
| generic | ~30%（问候/短问题） | ~800ms（跳过 Hindsight + KT） |
| task | ~60%（默认） | ~200ms（减少 Hindsight limit + 精简格式） |
| personal | ~10%（明确引用） | 0（全量 recall） |

加权平均每轮节省：`0.3×800 + 0.6×200 = 360ms`

## 五、风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| 规则误分类：重要记忆被跳过 | 低 | 中 | generic 仍有 skill matcher（最低保底）；task 是默认兜底 |
| 项目术语不全导致 task 被误判为 generic | 中 | 低 | 可扩展 INTENT_KEYWORDS["project_term"] |
| trace.log 统计口径变化 | 低 | 低 | 只影响分类分支，不影响 trace 记录本身 |
| 动态 RRF 分母未处理 | 中 | 中 | 需在 compute_rrf 中加 intent 参数分支 |

## 六、测试方法

1. 单元测试：对 `_classify_intent()` 的 10+ 边界消息测试
2. trace.log 验证：generic 消息不应出现 hindsight/kt recall 记录
3. 回归测试：personal 消息仍触发全量 recall
4. 性能侧写：`_inject_memory()` 耗时降低验证

## 七、不做事项（NON-GOALS）

- 不改 Hindsight 核心代码（只控制是否调用 API）
- 不改知识树 recall 内部逻辑
- 不做 LLM 分类器（纯规则已足够）