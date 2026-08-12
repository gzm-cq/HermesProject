---
name: knowledge-navigation
description: |
  知识导航系统 — agent 处理任务前自动执行：三层门控 → LLM Router {h,kt,s} mask → 按 mask 条件执行 HS/KT/SK → 后处理注入。
  当 agent 需要 recall 历史记忆、注入上下文、或优化 Hindsight 检索结果时使用。
  主动在 agent 每次 LLM 调用前触发 pre_llm_call Hook。
version: 1.2.0
related_skills: [hindsight-memory, clustering-analysis]
---

# 知识导航系统 — Knowledge Navigation Protocol

> **版本：V1.2.0 · 2026-06-28** — 新增 LLM Router 三路注入开关（替代 _classify_intent 规则），优化 prompt 设计原则，短消息阈值 10→3
>
> **核心工作流**：pre_llm_call Hook → 三层门控 → LLM Router {h,kt,s} mask → 按 mask 条件执行 HS/KT/SK → 后处理注入

## 一、当前管线流程

pre_llm_call Hook 每次 LLM 调用前自动执行：

```
用户消息
  ↓
[三层门控] ← turn_gate (skip_non_user / skip_system_prompt / skip_pre_llm_call)
  ├─ 来源门控：非用户平台(curator/cron/子代理) → return None
  ├─ 系统提示词门控：系统构造的第一轮长英文 → return None
  └─ 文本门控：操作型/短/确认消息 → return None（短消息阈值 ≤3 字）
  ↓ 通过
[熔断器检查] ──HS 熔断打开?──→ _hs_circuit_open=True（仅跳过 Hindsight，KT/Skill 照常）
  ↓
[LLM Router] → 输出 {h: bool, kt: bool, s: bool}（基于 need analysis）
  │ 异常/超时 → fallback 全开（h=kt=s=True）
  │ 缓存 key=(session_id, message) 精确匹配，新 message 重走
  ↓
全 false? ──→ return None
  ↓
按 mask 条件执行
  ├─ 2+ 路激活 → ThreadPoolExecutor 并行（HS+KT 并行，Skill 串行）
  └─ 1 路激活 → 串行
      ├─ h → _do_hindsight_recall()
      ├─ kt → _do_kt_recall() → multi_hop_expand()
      └─ s → _do_skill_match()
  ↓
[后处理（全部保留）]
  exclude_marked() → HitCounter.boost_scores() → causal_boost()
  → Compaction → filter_by_score() → CE 分数跨度压缩 → eval_match
  → 跨域去重(HS+KT) → KT 对齐 → turn-to-turn 去重 → 文本去重
  → score_stats → 分来源语义标签化组装
  → TaskTracker(每5轮注射任务摘要)
  ↓
注入 user message（XML 语义标签格式）
```

### 关键配置项（config.py）

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `hindsight_api_url` | `http://localhost:9177/v1/...` | Hindsight API 端点 |
| `min_score` | 0.6 | rerank_score 最低阈值 |
| `max_results` | 3 | 最大注入条数（Compaction 超 20 轮后降为 1）|
| `max_text_length` | 200 | 单条截断字符数 |
| `timeout_seconds` | 25 | Hindsight API 超时 |
| `enable_causal_chain` | `true` | 因果链 boost（PG memory_links 表查询）|
| `max_retries` | 0 | API 重试次数（0=不重试，依赖熔断器）|
| `circuit_breaker_threshold` | 3 | 熔断阈值（连续失败次数）|
| `circuit_breaker_cooldown` | 120 | 熔断冷却秒数 |
| `feishu_app_id` / `feishu_app_secret` / `feishu_home_channel` | "" | 飞书 OpenAPI 应用凭据与告警群聊（可空，空时不告警）|
| `enable_temporal` | true | 是否启用时态融合排序 |
| `eval_queries_path` | "" | 评估查询 JSON 文件路径（支持 `eval_queries_auto.json` 自动生成版） |
| `eval_min_score` | 0.1 | 关键词匹配阈值 |
| `max_trace_bytes` | 50MB | trace.log 轮转上限 |
| `backup_count` | 3 | trace.log 保留备份数 |

**Router 专用配置**：

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `router_model` | `sensenova-6.8-flash-lite` | Router LLM 模型 |
| `router_api_url` | `http://127.0.0.1:4142/v1` | LLM API 端点 |
| `router_api_key` | `""` | API Key（ENV: `KN_ROUTER_API_KEY`）|
| `router_timeout` | 5 | 超时秒数，不影响主流程 |

## 二、LLM Router 设计

### 设计原则

**Router 的三路定义：**

| 源 | 域 | 回答的核心问题 |
|----|-----|-------------|
| **H** hindsight | 经验域 — 发生过的事、历史行为 | "之前怎么做的/为什么这样/结果如何" |
| **KT** knowledge_tree | 知识域 — 客观结构化的原理与事实 | "这是什么/怎么工作的/定义是什么" |
| **S** skill | 能力域 — 可复用的操作流程与工具用法 | "怎么做/配置什么/用什么工具/步骤" |

**核心原则**：
- Router 只决定"是否电通这条路"，不决定"这条路怎么跑"
- 三域分别由三个独立系统管理，Router 不干预检索策略

### Router Prompt 设计（need analysis 版）

```
你是一个注入路由判断器。
判断：为了准确回答用户消息，是否需要从以下知识源补充信息？

H — 经验/Hindsight
  回答这个问题是否需要参考过去做过的类似事情、
  之前遇到的方案和教训、历史经验？

KT — 知识/Knowledge Tree
  回答这个问题是否需要引用客观的概念定义、
  原理、公式、架构说明、事实关系？
  需要"这个东西是什么、怎么工作的"这类知识？

S — 能力/Skill
  回答这个问题是否需要参考操作步骤、
  配置方法、部署流程、工具用法？
  需要"这个事怎么做"这类指南？

输出 JSON：{"h": bool, "kt": bool, "s": bool}

要求：
- 思考问题是"本质需要哪种知识"
- 宁可多开不遗漏
- 只输出 JSON，不要任何包裹格式
- 相同语义的问题输出一致
```

关键区别：**Need analysis 不是 Keyword matching**。Router prompt 问的是"回答这个问题是否需要X"，而不是"这段话有没有Y关键词"。前者是 LLM 擅长的语义判断，后者是规则引擎的事。

### 多路绕过 Rerank 原则

多跳（multi-hop）找到的是语义上不在同一维度的关联内容，用同样的 query 去 rerank 永远排在向量结果后面。不是技术问题，是排序标准不同。因此多跳结果标注 `source="multi-hop"`，**跳过 rerank**，单独输出。

### Router 实现要点

- `core/router.py` — httpx 调用 LiteLLM 网关，JSON 解析，fresh 兜底
- `core/source_defs.py` — SOURCES 共享定义 + `build_router_prompt()` 拼接 prompt
- 缓存 key = `(session_id, message)` 精确匹配，同轮 tool call 复用
- 安全过滤：`safe_msg = message[:300] + message[-200:]` + 替换换行
- fallback：调用失败 → 全开

## 三、Phase 2 能力（保留）

### C-P1-1: 轻量级 Compaction（_CompactionTracker）
- 按 session 跟踪调用轮次
- 前 20 轮：正常 `max_results` 注入
- 超过 20 轮：`max_results→1`，仅注入 1 条

### C-P1-4: 本地重要性缓存（_HitCounter）
- 按 node_id 累计命中次数，重复命中的记忆获得 boost
- `boost = min(1 + 0.1*(hits-1), 2.0)`，上限 2x

### C-P1-3: 定期任务回述（_TaskTracker）
- 每 5 轮注射 [任务状态摘要]

### XML 语义标签化注入格式

```xml
<user_query>用户原始问题</user_query>

<recalled_memory source="hindsight" count="2" score_avg="0.75">
  <memory source="hindsight" node_id="uuid">记忆内容...</memory>
</recalled_memory>

<knowledge source="knowledge_tree" count="1">
  <memory source="knowledge_tree" node_id="kt1">知识点...</memory>
</knowledge>

<auto_loaded_skills>
  ### skill-name
  技能内容...
</auto_loaded_skills>

<system_state>
pwd: /root
time: 2026-06-24T22:30:00
</system_state>
```

### 评估基线系统

8 个维度 × 10 条 = 80 条评测查询。配置路径：`CONFIG.eval_queries_path`。`collect_baseline.py` 提供 Bootstrap 95% CI 和 Welch t-test 对比。

## 四、记忆标记系统

脚本位置：`~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py`

| 标记类型 | 含义 |
|----------|------|
| `错误` | 记忆内容错误，不应被 recall |
| `作废` | 记忆已作废，不再有效 |
| `可疑` | 内容不确定，需验证 |
| `已解决` | 问题已解决，仍可做参考 |
| `待验证` | 等待确认正确性 |

## 五、插件架构

### 文件结构

```
~/.hermes/plugins/knowledge-navigation/
├── plugin.yaml
├── __init__.py
├── src/knowledge_navigation/
│   ├── config.py
│   ├── turn_gate.py              # 三层门控（来源/系统提示词/文本）
│   ├── core/
│   │   ├── filtering.py
│   │   ├── hooks.py              # pre_llm_call 实现 + 后处理管线
│   │   ├── router.py             # LLM Router：need analysis + httpx + 缓存
│   │   ├── source_defs.py        # H/KT/S 共享定义 + build_router_prompt()
│   │   ├── skill_matcher.py      # 三级兜底 skill 匹配
│   │   └── circuit_breaker.py
│   └── adapters/
│       └── hindsight.py
├── config/
│   ├── eval_queries.json
│   ├── eval_queries.yaml
│   └── eval_queries_auto.json
├── scripts/
│   ├── collect_baseline.py
│   └── generate_eval_queries.py
└── test_nav_hooks.py              # 27 check，mock Router + HS/KT/SK
```

### Hook 注入机制

| 返回值 | 注入位置 | 说明 |
|--------|----------|------|
| 纯字符串 | user message | 分来源 XML 语义标签格式 |
| `None` | 不注入 | 全 false 或无结果时返回 |

## 六、重要约束

### fact_type 过滤
Hindsight 的 `causal_expanded` CTE 有 `AND mu.fact_type = $2` 过滤条件。因果链的 from 和 to 节点 **fact_type 必须相同**，否则跨类型的因果链在 graph 检索中被过滤。

### RRF 融合公式
```
RRF_score(d) = Σ 1/(60 + rank_in_list(d))  for list ∈ {semantic, BM25, graph}
```

### 已知限制
- recall tracer 的 `trace.visits[].link_type` 全部为 `None`，不要依赖
- graph 端点用 GET 不是 POST
- Cross-encoder 不 boost causal 结果

### ⚠️ Pitfall：S-only 路径被空 kept 吞掉

当 mask={h:0, kt:0, s:1} 时，`kept` 为空（H/KT 未跑），后处理中的 `if not kept and summary is None: if not _kt_active: return None` 会吞掉 `_skill_context`。修复：改为 `return _skill_context if _skill_context else None`。

### ⚠️ Pitfall：测试消息过短被 turn_gate 拦截

`skip_pre_llm_call()` 的 `len(msg) <= 3` 阈值可能拦截有意义的短 query。验证时测试消息应 ≥11 字以绕过门控。

## 七、当前系统状态

| 子系统 | 状态 | 说明 |
|--------|------|------|
| pre_llm_call Hook | ✅ 已上线 | 每次 LLM 调用前自动 recall |
| trace 精度过滤 | ✅ 已上线 | min_score=0.6，max_results=3 |
| 记忆标记排除 | ✅ 已上线 | `exclude_marked()` 5 种标记类型 |
| 时态衰减排序 | ✅ 代码就绪 | `enable_temporal` 配置控制 |
| XML 语义标签化 | ✅ 已上线 | 分来源标签 |
| 注入去重 | ✅ 已上线 | node_id 去重 |
| Compaction | ✅ 已上线 | 20 轮后 max_results→1 |
| HitCounter | ✅ 已上线 | 高频记忆 boost，上限 2x |
| TaskTracker | ✅ 已上线 | 每 5 轮注射任务摘要 |
| **LLM Router** | ✅ **V1 已上线 (2026-06-28)** | need analysis → {h,kt,s} mask 代替 _classify_intent 规则；缓存 key=(session_id, message)；异常 fallback 全开 |
| 评估基线系统 | ✅ 已上线 | Bootstrap 95% CI + recall@k |
| **熔断器** | ✅ **已上线** | 连续 3 次失败熔断 120 秒，飞书卡片告警 |
| 全量聚类 | ✅ 已上线 | 增量运行，永不删除 |