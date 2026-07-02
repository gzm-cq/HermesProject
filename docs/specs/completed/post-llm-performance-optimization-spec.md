# SPEC：知识树插件 post_llm_call 性能优化

> 状态：已进入源码优化执行稿  
> 日期：2026-06-13  
> 范围：`plugins/knowledge-tree-plugin` 与其依赖的 `scripts/knowledge-tree-builder` 外挂项目源码  
> 边界：不修改 Hermes core，不修改 Hindsight daemon，不修改 Hindsight memory/schema，不改 Hindsight recall 内部逻辑。

---

## S — Situation（现状）

| 组件 | 当前状态 | 实测/源码证据 |
|---|---|---|
| `post_llm_call` | LLM 调用后同步执行知识提取与入库 | `hooks.py:post_llm_call()` |
| LLM 提取 | 每次对拼接后的对话做单次 `analyze_and_split()` | `extract_new.py:extract_from_dialog()` |
| 输入截断 | user/assistant 各占一半预算 | `extract_new.py` 旧逻辑 |
| embedding | 初始批量 embedding 后，dedup/conflict 又对每条知识点重复 embedding | `placement.py` + `incremental.py` |
| 父节点 K 向量 | 每插入一条知识点都读取并更新一次 | `placement.py:_update_k_vector()` |
| sibling 查询 | 每条知识点循环内重复查询 siblings | `placement.py` |
| PG 索引 | 缺部分业务索引，但当前 5k 行规模查询毫秒级 | EXPLAIN 实测 |

实测耗时摘要：

```text
post_llm_call 平均 22.34s
LLM 提取平均 14.29s，占约 63%
增量放置平均 8.47s，占约 37%
PG 查询主要为毫秒级，不是当前主瓶颈
```

---

## P — Problem（问题）

### P1. LLM 提取单次大上下文调用耗时高

当前无论输入大小，都走一次 `analyze_and_split()`。当助手回复较长时，单次大上下文调用耗时显著增加，还可能因截断丢失尾部知识。

根因：缺少按输入大小动态路由、结构化切分、有限并行提取、提取结果合并去重。

### P2. embedding 重复调用

当前流程：

```text
batch_embed(all_points)
dedup_before_insert() -> embed_fn([point])
detect_conflict()     -> embed_fn([point])
```

根因：dedup/conflict 函数没有接收已计算的 `point_embedding`，导致无共享外部 API 调用。

### P3. 父节点状态和 sibling 候选无共享

同一批新知识点通常定位到同一个 parent，但每条都重复执行：

```text
get_sibling_points(parent_id)
get_node_embedding(parent_id)
get_placement_count(parent_id)
update_k_vector(parent_id)
```

根因：缺少批次级 `PlacementContext`。

### P4. 矛盾检测可能被实际跳过

`detect_conflict()` 需要 sibling 的 `k_vector`，但 `DatabaseAdapter.get_sibling_points()` 旧实现只返回 `id,name`。

根因：候选数据字段不完整。

### P5. 缺少阶段耗时指标

当前日志只有 enter / extraction / complete，无法精确看到：LLM、embedding、定位、dedup、conflict、DB 写入、K 更新各自耗时。

根因：无细粒度 timing instrumentation。

---

## E — Evaluation & Priority

| # | 任务 | ROI | 工时 | 类型 |
|---|---|:---:|:---:|:---:|
| P0-1 | 增加阶段耗时日志 | 🔴 高 | 0.5h | 观测 |
| P0-2 | 复用已计算 embedding，取消 dedup/conflict 重复调用 | 🔴 高 | 1h | 性能修复 |
| P0-3 | sibling/parent 状态批次级缓存，K 向量批内更新一次 | 🔴 高 | 1h | 性能修复 |
| P1-1 | LLM 提取动态路由：小输入单次、大输入分块并行 | 🟡 高 | 1.5h | LLM 优化 |
| P1-2 | 分块结果 exact/Jaccard 去重，最终限制 max_points | 🟡 高 | 0.75h | 质量保护 |
| P1-3 | 动态输入预算：user 最多 800 字符，assistant 首尾保留 | 🟡 中 | 0.5h | LLM 优化 |
| P2-1 | stronger turn_gate：操作型/短响应跳过提取 | 🟢 中 | 1h | 触发优化 |
| P2-2 | PG 业务索引补充 | 🟢 低 | 0.5h | 增长保护 |
| P3 | post_llm_call 异步后台队列 | 🟢 高但风险较高 | 2-3h | 架构优化 |

本轮执行范围：P0-1、P0-2、P0-3、P1-1、P1-2、P1-3。P2/P3 只保留设计，不在本轮改动。

---

## C — Criteria（验收标准）

1. LLM 提取根据输入长度选择策略：
   - 小输入单次调用。
   - 大输入按结构切分，最多 3 路并行。
   - 任一分块失败不导致全部失败。
2. 分块结果合并后做去重：
   - exact normalize 去重。
   - Jaccard 高相似去重。
   - 最终数量受动态 max_points 限制。
3. `place_new_knowledge_points()` 对每批新知识点只做一次初始 `batch_embed(point_texts)`。
4. dedup/conflict 不再对同一新知识点重复调用 embedding API。
5. 同一 parent 的 sibling、parent K vector、placement_count 在批次内只读取一次。
6. 父节点 K 向量在批次内存中连续 EMA，最终只调用一次 `update_k_vector()`。
7. sibling 候选包含 `k_vector` 字段，矛盾检测不因字段缺失被静默跳过。
8. 单元测试覆盖：
   - 大输入分块并行提取。
   - 分块提取结果去重。
   - embedding 复用，不重复调用 `batch_embed()`。
   - 父 K 向量批内只更新一次。
9. 运行测试命令通过：

```bash
cd /mnt/d/HermesProject/plugins/knowledge-tree-plugin
PYTHONPATH=/mnt/d/HermesProject/scripts/knowledge-tree-builder/src:/mnt/d/HermesProject/plugins/knowledge-tree-plugin/src pytest tests/test_extract_new.py tests/test_placement.py -q
```

---

## 执行计划

### 任务 1：重构 `extract_new.py`

文件：

```text
plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/extract_new.py
```

新增能力：

- `_build_dialog_text()`：动态预算，user 最多 800 字符，assistant 使用剩余预算并保留首尾。
- `_choose_extract_strategy()`：按字符数分 S/M/L/XL 档。
- `_split_text_chunks()`：优先按 Markdown 标题和段落切分。
- `_extract_one_chunk()`：单块 LLM 提取。
- `_dedup_extracted_points()`：normalize + Jaccard 去重。
- `extract_from_dialog()`：根据策略单次或并行提取。

### 任务 2：重构 `placement.py`

文件：

```text
plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/placement.py
```

新增能力：

- `PlacementContext` dataclass。
- `_dedup_before_insert_with_embedding()`。
- `_detect_conflict_with_embedding()`。
- `_next_k_vector()` / `_accumulate_parent_k_vector()`。
- 批次级 parent/sibling/leaf 缓存。
- 最终一次 `update_k_vector()`。

### 任务 3：补齐 sibling k_vector

文件：

```text
scripts/knowledge-tree-builder/src/knowledge_tree_builder/adapters/database.py
```

修改：

```sql
SELECT id, name, k_vector
FROM knowledge_tree
WHERE parent_id = %s AND node_type = 'knowledge_point'
```

### 任务 4：测试

文件：

```text
plugins/knowledge-tree-plugin/tests/test_extract_new.py
plugins/knowledge-tree-plugin/tests/test_placement.py
```

覆盖上述验收标准。

---

## 风险与降级

| 风险 | 应对 |
|---|---|
| 并行 LLM 触发上游限流 | 最大并发固定为 3；分块失败返回空并记录 warning |
| 分块导致重复知识点 | exact/Jaccard 去重 + 后续 embedding 去重 |
| 分块丢失跨段关系 | 每块带用户问题摘要；知识树只提取原子知识，跨段依赖不是主要目标 |
| 批量 K 更新改变数值轨迹 | 内存中按原逐条 EMA 顺序累计，最终只减少 DB 写次数 |
| 旧调用方依赖旧函数 | 保持 `extract_from_dialog()` 与 `place_new_knowledge_points()` 外部签名兼容 |

---

## 不在本轮做

- 不改 Hermes plugin hook 执行模型。
- 不做后台队列异步化。
- 不修改 Hindsight。
- 不修改 Hermes core。
- 不直接在生产库创建索引。
- 不部署，源码改完后先提审。
