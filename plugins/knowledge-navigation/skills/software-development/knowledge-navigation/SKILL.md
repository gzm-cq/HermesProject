---
name: knowledge-navigation
description: |
  知识导航系统 — agent 处理任务前自动增强 Hindsight recall 结果的精度过滤 + 排除标记记忆 + 时态衰减排序 + 轻量 Compaction + 高频记忆 boost + 定期任务回述，标记工具由聚类项目维护。
  当 agent 需要 recall 历史记忆、注入上下文、或优化 Hindsight 检索结果时使用。
  主动在 agent 每次 LLM 调用前触发 pre_llm_call Hook。
version: 1.1.0
related_skills: [hindsight-memory, clustering-analysis]
---

# 知识导航系统 — Knowledge Navigation Protocol

> **版本：V1.1.0 · 2026-06-18** — 修正文档、删除死代码、重构熔断器为独立模块、新增知识树集成说明
>
> **核心工作流**：pre_llm_call Hook → 熔断器检查 → recall → 失败分类计数 · 连续失败熔断 → 排除已标记记忆 → HitCounter boost → Compaction 限流 → 分数过滤（可选时态融合）→ TaskTracker 摘要 → XML 格式化注入 → **recall@k 日志 + eval_query_id 打点（有 expected_ids 时计算 recall_hit）**

## 一、当前管线流程

pre_llm_call Hook 每次 LLM 调用前自动执行：

```
用户消息
  ↓
[熔断器检查] ──熔断打开?──→ return None（跳过 recall）
  ↓ 正常
Hindsight recall(query, trace=true) + 知识树 recall（可用时）
  ↓
recall 失败? ──→ _circuit_record_failure(类别)
  │               └─ 连续 3 次 → 熔断打开 + 飞书告警
  ↓ 成功
_circuit_record_success() 重置熔断计数
  ↓
exclude_marked() —— 排除 [标记: 错误/作废/可疑] 等
  ↓
_HitCounter.boost_scores() —— 高频记忆 rerank_score 提升，最大 2x
  ↓
_CompactionTracker —— 超过 20 轮后 max_results→1，防 context 膨胀
  ↓
filter_by_score() —— min_score 过滤 + 可选时态衰减排序
  │                 └─ enable_temporal=true: score × (floor + (1-floor) × exp(-days/halflife))
  ↓
format_context_lines() —— 旧版 CLI 用 <memory-context> 格式；主线用分来源语义标签
  ↓
_TaskTracker —— 每 5 轮注射 [任务状态摘要]，防长对话目标漂移
  ↓
注入 user message（不修改 system prompt，保留 prompt cache）
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

## 二、Phase 2 新增能力

### C-P1-1: 轻量级 Compaction（_CompactionTracker）

- 按 session 跟踪调用轮次
- 前 20 轮：正常 `max_results` 注入
- 超过 20 轮：`max_results→1`，仅注入 1 条
- 防止长对话中 Hindsight 行数反噬 context

### C-P1-4: 本地重要性缓存（_HitCounter）

- 按 node_id 累计命中次数
- 重复命中的记忆获得 rerank_score boost：`boost = min(1 + 0.1*(hits-1), 2.0)`
- **上限 2x**，防单条记忆长期垄断
- 在 `filter_by_score()` 之前执行

### C-P1-3: 定期任务回述（_TaskTracker）

- 每 5 轮检查一次
- 到轮次时向 context_lines 追加 `[任务状态摘要]` 提示
- 日志记录 `task_summary_round` 字段

### XML 语义标签化注入格式（pre_llm_call）

旧格式（纯文本前缀）：
```
[Hindsight] 记忆内容...
```

旧版中间格式（format_context_lines，CLI 用）：
```xml
<memory-context>
  <memory source="hindsight" node_id="uuid">记忆内容...</memory>
  <memory source="hindsight" node_id="uuid">记忆内容...</memory>
</memory-context>
```

当前线上格式（分来源语义标签）：
```xml
<user_query>用户原始问题</user_query>

<recalled_memory source="hindsight" count="2" score_avg="0.75">
  <memory source="hindsight" node_id="uuid">记忆内容...</memory>
  <memory source="hindsight" node_id="uuid">记忆内容...</memory>
</recalled_memory>

<knowledge source="knowledge_tree" count="1">
  <memory source="knowledge_tree" node_id="kt1">知识点...</memory>
</knowledge>

<auto_loaded_skills>
  ### skill-name (match=0.85)
  技能内容...
</auto_loaded_skills>

<system_state>
pwd: /root
time: 2026-06-24T22:30:00
</system_state>
```

- 使 LLM 能准确区分用户输入与插件自动注入
- 每条记忆包含 `node_id` 便于追根溯源
- 不同来源用不同标签，便于调试和 trace 分析

### 注入去重（_dedup_candidates）

- 所有 pass 分数过滤的结果按 `node_id` 去重
- 同 id只保留第一个出现的条目
- 在 `filter_by_score()` 内部执行

### 评估基线系统

#### E-P1-2: 评测查询集

8 个维度 × 10 条 = **80 条评测查询**：

| 维度 | 示例 |
|------|------|
| `semantic_*` | "LiteLLM 配置相关的问题怎么处理" |
| `entity_*` | "shared-postgres 相关的技术配置" |
| `causal_*` | "Gateway 崩溃的原因和修复" |
| `temporal_*` | "上周做的性能优化方案" |
| `conflict_*` | "老方案 v1 和当前方案有什么区别" |
| `tool_*` | "怎么用 mark_memory.py 搜索记忆" |
| `debug_*` | "Gateway 504 超时如何排查" |
| `api_*` | "Hindsight REST API 的端点有哪些" |

配置路径：`CONFIG.eval_queries_path`（JSON 文件）
匹配方式：精确匹配 > 关键词 Jaccard 重叠（阈值 `eval_min_score`）

#### E-P1-1: 基线增强（collect_baseline.py）

离线工具，从 trace.log 中提取并分析：

```bash
# 收集基线（Bootstrap 95% CI）
cd ~/.hermes/plugins/knowledge-navigation
python3 /root/.hermes/plugins/knowledge-navigation/scripts/collect_baseline.py

# JSON 输出（用于程序调用）
python3 /root/.hermes/plugins/knowledge-navigation/scripts/collect_baseline.py --json

# 对比两次基线（如优化前后）
python3 /root/.hermes/plugins/knowledge-navigation/scripts/collect_baseline.py --compare before.json after.json
```

输出示例：
```
📊 评估基线报表 (Bootstrap 95% CI)
  来源: /root/.hermes/logs/trace.log
  覆盖查询: 45 条 | 6/8 维度

  [semantic] 10 条查询
    kept:  2.10  [1.80, 2.40]
    score: 0.723  [0.689, 0.757]
    delay: 7502ms [7200, 7800]ms
```

对比模式输出 Welch t-test 结果（p < 0.05 标记 🟢）。

#### E-P1-3: LLM 合成评测查询（generate_eval_queries.py）

从 PostgreSQL `memory_units` + `unit_entities` 按 entity 分组自动生成自然用户发问，含 `expected_ids` 字段：

```bash
cd ~/.hermes/plugins/knowledge-navigation
CLUSTERING_DB_URL="postgresql://..." \
  LLM_API_URL="http://127.0.0.1:4142/v1/chat/completions" \
  LLM_MODEL="s-deepseek-v4-flash" \
  python3 scripts/generate_eval_queries.py --count 100 --output config/eval_queries_auto.json
```

- 按 entity 分组 + 按 fact_type round-robin 采样，保多样性
- 无 entity 的记忆 fallback 到纯文本采样
- 输出含 `expected_ids`（该 entity 相关的所有 memory_id）
- 结合 `KN_EVAL_QUERIES_PATH` 环境变量指向自动评测集

#### E-P1-4: recall@k 离线评估

hooks.py 在 `pre_llm_call` 中自动记录：

- `recalled_ids`：过滤后保留的 memory_id 列表
- `eval_expected_ids`：评测查询期望召回的 memory_id（来自 `expected_ids` 字段）
- `eval_recall_hit` / `eval_recall_k`：命中数和总数

`collect_baseline.py` 自动从 trace.log 提取并计算 recall@k：

```
📊 评估基线报表 (Bootstrap 95% CI)
  ...
  [semantic_001]
    avg_recall_at_k: 0.5234  [0.4123, 0.6345]  ← 新增
```

#### E-P1-5: LLM Relevance Judge（collect_baseline.py --judge）

离线用 LLM 批量评估 recall 的 relevance，不受在线延迟影响：

```bash
LLM_API_URL="http://127.0.0.1:4142/v1/chat/completions" \
  LLM_API_KEY="..." \
  LLM_MODEL="s-deepseek-v4-flash" \
  python3 /root/.hermes/plugins/knowledge-navigation/scripts/collect_baseline.py --judge
```

- `collect_all_recalls()` 读取 trace.log 中所有 `event: "recall_success"` 记录
- 采样最多 200 条，LLM 输出 0-1 relevance score
- 汇总：相关率（score>=0.5）、平均 relevance、Bootstrap 95% CI
- 输出示例：

```
📊 LLM Relevance Judge 报表
  总 recall 记录: 156
  已评分: 200 条
  相关率 (score>=0.5): 72.5%
  平均 relevance:  0.6843
  Bootstrap 95% CI: [0.6521, 0.7165]
  （采样 200 条，LLM 评估。低分说明 recall 需要优化）
```

#### 评估系统闭环

```
generate_eval_queries.py (合成查询 + expected_ids)
  ↓
hooks.py (实时 recall@k 日志打点)
  ↓
collect_baseline.py (Bootstrap CI + recall@k 统计)
  ↓
collect_baseline.py --judge (LLM relevance 离线评分)
```

### 日志字段

trace.log 中 `event: "recall_success"` 的完整字段：

| 字段 | 说明 |
|------|------|
| `total_results` | 原始召回总数 |
| `excluded_marked` | 被标记排除的条数 |
| `kept_results` | 过滤后保留条数 |
| `injected_count` | 实际注入条数（含 TaskTracker 摘要行） |
| `total_chars` | 注入总字符数 |
| `score_stats` | `{min, max, avg, count}` |
| `score_comparison` | 每个保留结果的双分对比 [{node_id, base_score, temporal_score}] |
| `eval_query_id` | 匹配到的评测查询 ID（如有） |
| `recalled_ids` | 过滤后保留的 memory_id 列表 |
| `eval_expected_ids` | 该查询期望召回的 memory_id 列表（有 expected_ids 时） |
| `eval_recall_hit` | 命中数：`len(expected_ids & recalled_ids)` |
| `eval_recall_k` | 期望召回总数：`len(expected_ids)` |
| `task_summary_round` | TaskTracker 触发时的轮次号（如有） |
| `latency_ms` | 总耗时 |

## 三、记忆标记系统（2026-05 上线）

### mark_memory.py

脚本位置：`~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py`

```bash
# 标记一条错误记忆（不再被 recall）
CLUSTERING_DB_URL="postgresql://..." \
  python3 /root/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py \
  mark <unit_id> 错误 "可选说明"

# 移除标记（可逆恢复）
CLUSTERING_DB_URL="postgresql://..." \
  python3 /root/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py \
  unmark <unit_id>
```

| 标记类型 | 含义 |
|----------|------|
| `错误` | 记忆内容错误，不应被 recall |
| `作废` | 记忆已作废，不再有效 |
| `可疑` | 内容不确定，需验证 |
| `已解决` | 问题已解决，仍可做参考 |
| `待验证` | 等待确认正确性 |

## 四、Hook 插件架构

### 插件结构

```
~/.hermes/plugins/knowledge-navigation/
├── plugin.yaml                  # 插件清单
├── __init__.py                  # 注册入口（register(ctx) 必须）
├── src/knowledge_navigation/
│   ├── config.py                # 配置 + JSONFormatter + setup_logging
│   ├── core/
│   │   ├── filtering.py         # 过滤逻辑（标记排除 + 分数过滤 + 时态衰减 + 去重 + XML 格式化）
│   │   └── hooks.py             # pre_llm_call 实现 + Compaction/HitCounter/TaskTracker + eval_match + recall@k 日志 + 熔断器 + 飞书告警
│   └── adapters/
│       └── hindsight.py         # Hindsight API 客户端（重试 + 超时）
├── config/
│   ├── eval_queries.json        # 80 条手工评测查询
│   ├── eval_queries.yaml        # 同内容 YAML 版
│   └── eval_queries_auto.json   # LLM 自动生成评测查询（可选，需运行 generate）
├── scripts/
│   ├── collect_baseline.py      # 知识导航评估基线采集/对比/告警入口
│   └── generate_eval_queries.py # LLM 从 PG memory_units 自动生成评测查询
├── tests/                       # 60 个 pytest 测试
├── pyproject.toml
└── README.md
```

### 关键：register(ctx) 是必需的

Hermes 插件系统要求每个插件**必须**有 `register(ctx)` 函数，`plugin.yaml` 中的 `hooks:` 声明只是元数据。

```python
def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", pre_llm_call)
```

### Hook 注入机制

| 返回值 | 注入位置 | 说明 |
|--------|----------|------|
| 纯字符串 | user message | 分来源 XML 语义标签格式 |
| `None` | 不注入 | 无结果或无摘要时返回 |

- 注入到 **user message**（不是 system prompt），保留 prompt cache
- 注入内容**瞬时**，不持久化到 session DB
- 每个插件回调独立 try/except，不互相影响
- 自动 `truncation_strategy`（默认 10000 字符）

### Hook 限制
- `pre_llm_call` 只能注入文本，不能拦截 tool calls
- `post_llm_call` 返回值被忽略，只能用于副作用
- 插件 Hook 回调不能调用 Hermes 工具/MCP 工具
- 替代方案：通过 `urllib.request` 调 Hindsight daemon HTTP API

## 五、熔断器与飞书告警（2026-06 上线）

### 熔断器（Circuit Breaker）

插件内置模块级全局状态熔断器，位于 `core/circuit_breaker.py`（已从 `hooks.py` 重构为独立模块）：

| 变量 | 类型 | 说明 |
|------|------|------|
| `_circuit_failures` | int | 连续失败计数 |
| `_circuit_open_until` | float | 熔断解锁时间戳（0=未熔断）|
| `_circuit_failure_types` | Counter[str] | 各类失败出现次数 |
| `_LAST_NOTIFICATION_TIME` | float | 上次告警时间戳（限频用）|

**工作流程**：
1. `pre_llm_call` 入口检查 `_circuit_is_open()` → 熔断中则直接 return None
2. recall 异常 → `_circuit_record_failure("exception")`
3. recall 返回空 → `_circuit_record_failure("service_error")`
4. results 为空列表 → `_circuit_record_failure("empty_results")`
5. 正常 → `_circuit_record_success()` 重置所有计数
6. 连续失败 ≥ `CONFIG.circuit_breaker_threshold`（默认 3）→ 打开熔断 + 发送飞书告警

**配置参数（config.py）**：

| 配置 | 环境变量 | 默认值 | 说明 |
|------|---------|--------|------|
| `circuit_breaker_threshold` | `KN_CB_THRESHOLD` | 3 | 熔断阈值（连续失败次数）|
| `circuit_breaker_cooldown` | `KN_CB_COOLDOWN` | 120 | 冷却秒数 |

### 飞书卡片告警

熔断打开时自动通过飞书 OpenAPI 向配置的群聊发送 Interactive Card：

**错误分类**：

| 类别 | 标签 | 严重级别 |
|------|------|----------|
| `exception` | 🔴 未预期异常 | red（最高）|
| `service_error` | 🟡 服务返回空 | yellow |
| `empty_results` | 🟠 无匹配结果 | orange |

**告警逻辑**：
- 按最高严重级别选卡片颜色（exception→red > service_error→yellow > empty_results→orange）
- 失败分布按次数倒序排列在卡片中
- 同一进程至少间隔 5 分钟，防轰炸
- 未配置 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_HOME_CHANNEL` 时静默跳过
- 使用 `import requests as _req` 懒导入，不影响主流程

**配置方式（WSL）**：
```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET  # 已在安全环境中设置，勿在文档/仓库中写入真实值
export FEISHU_HOME_CHANNEL="oc_xxx"
```

## 六、重要约束

### fact_type 过滤（因果链局限）

Hindsight 的 `causal_expanded` CTE 有 `AND mu.fact_type = $2` 过滤条件。因果链的 from 和 to 节点 **fact_type 必须相同**，否则跨类型的因果链在 graph 检索中被过滤。

### RRF 融合公式（因果链排名低的原因）

```
RRF_score(d) = Σ 1/(60 + rank_in_list(d))  for list ∈ {semantic, BM25, graph}
```

### ⚠️ 已知限制

- recall tracer 的 `trace.visits[].link_type` 全部为 `None`，不要依赖
- graph 端点用 GET 不是 POST
- Cross-encoder 不 boost causal 结果

## 七、当前系统状态

| 子系统 | 状态 | 说明 |
|--------|------|------|
| pre_llm_call Hook | ✅ 已上线 | 每次 LLM 调用前自动 recall |
| trace 精度过滤 | ✅ 已上线 | min_score=0.8，max_results=3 |
| 记忆标记排除 | ✅ 已上线 | `exclude_marked()` 5 种标记类型 |
| 时态衰减排序 | ✅ 代码就绪 | `enable_temporal` 配置控制 |
| XML 语义标签化 | ✅ 已上线 | 分来源标签：`<user_query>`/`<recalled_memory>`/`<knowledge>`/`<auto_loaded_skills>`/`<system_state>` |
| 注入去重 | ✅ 已上线 | `_dedup_candidates()` node_id 去重 |
| Compaction | ✅ 已上线 | 20 轮后 max_results→1 |
| HitCounter | ✅ 已上线 | 高频记忆 boost，上限 2x |
| TaskTracker | ✅ 已上线 | 每 5 轮注射任务摘要 |
| 评估查询（80 条） | ✅ 已上线 | 8 维度 × 10 条，精确+关键词匹配 |
| 基线统计 | ✅ 已上线 | Bootstrap 95% CI + Welch t-test 对比 |
| recall@k 评估 | ✅ 代码就绪 | `recalled_ids` + `eval_expected_ids` 日志打点 |
| LLM 合成评测查询 | ✅ 代码就绪 | `generate_eval_queries.py` 从 PG 自动生成 |
| LLM relevance judge | ✅ 代码就绪 | `collect_baseline.py --judge` 离线批量评分 |
| 因果链补齐 | ✅ Phase 1 完成 | build-causal-links.py |
| **熔断器（Cicuit Breaker）** | ✅ **已上线** | 连续 3 次失败熔断 120 秒，飞书卡片告警（限频 5 分钟） |
| 文本富化 | ✅ 已上线 | 聚类 Phase 3 合并 text UPDATE |
| 全量聚类 | ✅ 已上线 | 增量运行，永不删除 |
