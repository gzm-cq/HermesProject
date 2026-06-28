# Hermes 数据飞轮优化报告

> 基于 2026-06-28 全量代码审阅 + 技术框架调研。
> 原则：不修改 Hermes Gateway 源码，所有优化在子项目/插件层面实施。

---

## 一、各项目现存问题

### 1.1 知识树构建器（knowledge-tree-builder）

#### 性能瓶颈

- **LLM 调用串行限速**：`call_llm()` 内有 `time.sleep(0.3)` 硬编码限速。100 篇文章 x 2 次（提取 + 领域判断）= 200 次 LLM，纯等待 60 秒起步。Phase 1+2 提取和 Phase 4 领域判断完全可以合并为一次 LLM 调用，直接砍半。
- **Phase 3 去重 O(N*M) 线性扫描**：`_dedup_single()` 对每条新知识点遍历全库已有向量，知识库到数千条后明显变慢。应下推到 pgvector 的 `<=>` HNSW 索引做近似最近邻。
- **Embedding 重复计算**：Phase 3 和 Phase 4 各自独立调 `batch_embed`，但 Phase 3 产出的 passed 列表已有 embedding，Phase 4 完全可以复用。

#### 质量风险

- **Phase 3 矛盾检测条件提取过于简单**：`_extract_condition()` 只匹配"在...上/下/时/后/中"的正则，大量条件句（"当...时"、"若...则"、"在...的情况下"）漏匹配，矛盾检测覆盖率低。
- **领域判断缓存以文章标题为 key**：两个同名 README.md 会互相覆盖。应改用文件路径或内容 hash。
- **`call_llm_json` 强制 `dict()` 包裹**：如果 LLM 返回数组会直接报错。

### 1.2 知识树在线插件（knowledge-tree-plugin）

#### 质量

- **分级提取策略的截断规则偏粗暴**：XL 输入（>12000 字）均匀采样切分，可能切断语义完整的段落。应优先按 markdown 标题/空行切分后再按预算截断。
- **准入门控的 `_guard_filter_points` 过滤配置/命令类**：但技术文档中常见的"配置参数说明"类知识点（如"Redis 默认端口 6379"）会被误过滤。

#### 架构

- **后台队列 maxsize=100 无重试机制**：如果消费速度跟不上（LLM 提取慢），新任务被静默丢弃（有 warning 日志 `kt_queue_full`，但无重试或备用队列）。

### 1.3 知识导航插件（knowledge-navigation）

- **Skill Matcher 每次全量扫描 skills 目录**：`ensure_index()` 每次调用遍历 `~/.hermes/skills/` 所有 SKILL.md，虽然缓存了 `_skill_index`，但首次调用和缓存失效时开销大。应加文件 mtime 检查，只扫描变更文件。
- **Skill Matcher 的 LLM 调用发送 ~345 个 skill 描述**：每次 pre_llm_call 都把所有 skill 的 name+description 拼给 LLM，prompt 很长。应做两级筛选：先 embedding 预过滤（< 20 个候选），再 LLM 精排。
- **跨域去重阈值 0.65 偏低**：知识树和 Hindsight 的"同义不同表述"很容易被判重删掉。应考虑只在 strict 模式（>0.80）下去重，或引入"降权"而非"删除"。

### 1.4 聚类分析（clustering-analysis-v3）

#### 质量

- **HDBSCAN 参数单一**：固定 `min_samples=3`，没有根据数据规模自适应。小数据集过度分簇，大数据集欠分簇。
- **max_group_size=20 硬截断**：大簇直接跳过 LLM 实体提取和因果链检测，覆盖率有硬上限。
- **因果链增强的采样盲区**：大实体（>50 成员）只采样 30 个旧成员，采样之外的旧-旧、旧-新关系完全遗漏。

#### 性能

- **dedup_memories O(n^2)**：batch_size=500 时内部两两 Jaccard 比较 ~125k 次。应用 MinHash（脚本里已有 `dedup_minhash.py` 但与主管线是独立的）。
- **embedding 更新阶段串行**：Phase 4 逐条更新 PG，可以用 batch UPDATE。

### 1.5 记忆清理（memory-cleanup）

#### 质量

- **compress 对 USER.md 质量规则过松**：关键词重叠阈值只有 10%（MEMORY 为 20%），且不检查关键实体保留率，用户偏好信息可能被过度压缩。
- **hindsight 迁移无内容准确性验证**：只检查有标签 + 长度 >= 20 字符，原文的核心事实是否保留没有校验。
- **merge 对 <=3 条批次用 max 关键词覆盖率**：两条无关条目可能被合并但只保留了其中一条的关键词就放行。
- **AUTO_REMOVE_PATTERNS 硬编码**："V6"、"方法论"等词可能误删合法内容（如"V6 版本引入了新架构"）。

---

## 二、推荐引入的框架

经过调研，以下 1 个框架值得引入：

| 框架 | 解决什么问题 | 引入方式 | 额外依赖 | 成本 |
|------|-------------|---------|---------|------|
| **RAGAS** | 自动评估"注入的知识是否真的被 LLM 使用" | `pip install ragas`，作为 cron 子项目运行 | 无 GPU 依赖，通过 API 调 LLM 做评估 | ~$0.05/100次评估（用 GPT-4o-mini）或 $0（用本地 Qwen2.5-7B） |

### 不推荐的框架及理由

| 框架 | 不推荐理由 |
|------|-----------|
| semantic-router (aurelio-labs) | Hermes 已有 bge-m3 embedding 能力，345 个 skill 手写余弦预筛选比引入整库更轻量；semantic-router 擅长意图边界清晰的分类，skill 匹配更接近 RAG 检索 |
| vLLM Semantic Router | Go/Rust 为主，部署复杂度过高，适合基础设施层而非应用层 |
| RouteLLM | 已停滞（2024-08 后无更新），只支持强/弱两模型路由 |
| DeepEval | 与 RAGAS 功能重叠，没必要同时引入两个评估框架 |
| Phoenix / Langfuse | 需要 Docker + ClickHouse + Redis 等重量级基础设施，不适合 Hermes 轻量 cron 场景 |

---

## 三、核心优化项

### P0-1：Skill Matcher 改为 Embedding 两级筛选

**现状问题**：每次 pre_llm_call 把 ~345 个 skill 的 name+description 全部发给 LLM，prompt 很长，延迟 ~3s。

**技术依据**：行业共识 — OpenAI 官方推荐 Agent 工具数 < 20 个，超过后准确率显著下降。两阶段检索（Embedding 预筛选 + Reranker 精排）是当前标准方案。

**推荐方案**：不引入 semantic-router，用现有 BAAI/bge-m3 自行实现：

```
Stage 1: Embedding 余弦预筛选（~10ms，从 345 个筛到 Top-15）
  → 使用已有的 bge-m3（与知识树 recall 同模型，无需新增依赖）
  → 预计算所有 SKILL.md description 的 embedding，缓存在内存
  → 首次构建后用文件 mtime 增量更新

Stage 2: LLM 精排（~300ms，从 Top-15 选出 Top-3）
  → 只对 15 个候选发给 LLM，prompt 短很多
```

**引入代价**：零新依赖，~50 行代码。

**可行性注意**：
- `_batch_embed()` 当前是 hooks.py 的私有函数，Skill Matcher 无法直接 import。实施时需提取为 `adapters/embed.py` 公共模块（~20 行额外重构）。
- 依赖 `SILICONFLOW_API_KEY` 环境变量，需确保部署环境已配置（当前 cross_domain_dedup 已在用同一变量，通常已存在）。

**预期效果**：延迟从 ~3s 降到 ~400ms（降幅 ~87%），API 调用成本降低 ~90%。

### P0-2：去重下推 pgvector

**现状问题**：`knowledge-tree-builder` 的 `_dedup_single()` 对每条新知识点遍历全库已有向量（O(N*M)），知识库增长到数千条后明显变慢。

**技术依据**：pgvector HNSW 在 10K+ 向量时查询 ~8.6ms（vs 无索引 4146ms），加速 480 倍。余弦距离必须用 `<=>` 算子。

**具体实现**：

```sql
-- 1. 确认 embedding 维度，创建 HNSW 索引（10K+ 向量时）
SET maintenance_work_mem = '4GB';
CREATE INDEX ON knowledge_tree
  USING hnsw (k_vector vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- 2. 去重查询：精确去重（余弦相似度 > 0.95 即余弦距离 < 0.05）
SET LOCAL hnsw.ef_search = 200;
WITH nearest AS MATERIALIZED (
  SELECT id, k_vector <=> $1 AS dist
  FROM knowledge_tree
  WHERE k_vector IS NOT NULL AND node_type = 'knowledge_point'
  ORDER BY dist
  LIMIT 10
)
SELECT id, dist FROM nearest WHERE dist < 0.05;
```

**关键注意事项**：
- `<=>` 返回余弦距离（0=完全相同，2=完全相反），余弦相似度 = `1 - dist`
- 精确去重阈值 `dist < 0.05`（相似度 > 0.95），模糊去重 `dist < 0.10`
- `ef_search` 默认 40 太低，去重场景建议 100-200
- pgvector >= 0.8.0 支持 `iterative_scan`，带 WHERE 过滤时自动扩展扫描范围
- < 10K 向量不需要索引，顺序扫描足够快

**引入代价**：零新依赖（pgvector 已在用），代码改动集中在 `admit.py` 的 `_dedup_single()` 和 `phase/admit.py` 的主去重循环（第 509-551 行）。

**可行性注意**：
- 当前 `admit.py` 的 `_dedup_single()` 接收 `existing_vectors: list[dict]` 参数（从 `db.get_leaf_nodes()` 预加载到内存），不持有 DB 连接。下推 SQL 需修改函数签名，将 `db: DatabaseAdapter` 传入 admit 模块。
- 或新增 `db.find_nearest_neighbors(embedding, threshold)` 方法封装 SQL，保持 `_dedup_single()` 接口不变。

### P0-3：LLM 调用合并（knowledge-tree-builder）

**现状问题**：Phase 1+2 提取 + Phase 4 领域判断 = 2N 次 LLM 调用（N=文章数），`call_llm()` 有 0.3s 限速。

**实现方案**：在 `merged.py::analyze_and_split` 的 prompt 中追加领域判断要求，一次调用输出 `{ analysis, atomic_knowledge, suggested_domain }`，然后 Phase 4 直接读取缓存，不再调 LLM。

**引入代价**：仅改 prompt 模板 + `run.py` 的领域判断逻辑，零新依赖。LLM 调用量直接减半。

---

## 四、自动反馈飞轮

### 推荐框架：RAGAS

选择理由：
1. `pip install ragas`，零服务端依赖
2. **Faithfulness 指标**直接回答"注入的知识是否被使用" — LLM-as-a-Judge 将回答拆分为独立声明，逐一验证是否可从注入上下文推导
3. 不需要 ground truth（除 Context Recall 外）
4. 可作为 Python 脚本在 cron 中运行
5. 社区最活跃（10k+ stars），Apache-2.0 许可

### 三阶段闭环设计

#### Phase A：数据采集（运行时，无感知）

```
post_llm_call hook 中新增记录逻辑：
  → 从 context_lines（pre_llm_call 返回的注入文本）中提取注入的知识来源标记
  → 记录 (user_query, assistant_response, context_lines)
  → 写入 trace.log（已有机制，扩展字段即可）
  → 零额外延迟（纯字符串提取 + 日志写入）
```

#### Phase B：离线评估（cron 每日/每周）

```
新建 scripts/recall-eval/ 子项目：
  → 从 trace.log 采样最近 N 次调用
  → 调用 RAGAS evaluate():
    - faithfulness: 注入知识是否被 LLM 使用
    - answer_relevancy: 回答是否切题
    - AspectCritic: 自定义（因果链关键实体是否被引用）
  → 输出评估报告 + 写入审计日志
  → 如果 faithfulness < 0.6，标记对应知识树分支质量低
```

#### Phase C：自动改进（cron 触发）

```
基于评估报告的自动化动作：
  1. Router prompt 微调：
     → 收集 "H=1 但 faithfulness 低" 的样本
     → 自动生成 few-shot 反例加入 source_defs examples
     → 注：当前 `build_router_prompt()` 未渲染 examples 字段（只使用 name/domain/description），实施时需同步修改 prompt 构建逻辑
  2. 知识树质量标记：
     → 某分支 faithfulness 持续 < 0.6
     → 自动标记 [标记: 待验证]，触发 consolidate review
  3. 召回参数自适应：
     → 如果 KT 路 faithfulness 持续低
     → 自动降低 recall_min_score 阈值
```

### 引入代价评估

| 维度 | 成本 |
|------|------|
| 新增依赖 | `ragas`（pip install），无其他依赖 |
| 运行时延迟 | Phase A 零延迟（只是多写几行日志） |
| API 成本 | 每日评估 100 条 ~$0.05（GPT-4o-mini），月 ~$1.5；或用本地 Qwen2.5-7B 零成本 |
| 开发投入 | Phase A: 半天；Phase B: 1-2 天（新子项目）；Phase C: 2-3 天 |
| 存储开销 | trace.log 增加几个字段，可忽略 |

---

## 五、记忆闭环（Hindsight L3 RAG 层生命周期设计）

### 现状诊断

对比知识树已有的闭环，Hindsight 记忆缺失以下关键环节：

| 环节 | 知识树 | Hindsight 记忆 |
|------|--------|---------------|
| Recall 使用记录 | `knowledge_use_log` 表，recall 后自动写入 | **不存在** |
| Recall 频率 → 质量信号 | `recall_count_decayed` 用于 consolidation 合并/淘汰决策 | **不存在** |
| LLM 是否实际引用 | 不存在 | 不存在 |
| 使用频率 → L2 回升 | 不存在 | **不存在**（L2→L3 单向降级，无反向通道） |
| 离线标记 | N/A | mark_memory.py 5 种标记 + 在线排除 |
| 质量报告 | N/A | memory_quality_report.py 4 指标（仅静态存储质量） |
| LLM 分类关键词 | N/A | memory-cleanup 的 hindsight 分类时生成的关键词标签**被丢弃**（只传 content） |

**核心问题**：Hindsight 记忆是"只进不出"的单向管道。写入后没有使用频率追踪，没有质量反馈，没有自然淘汰机制，没有向 L2 回升的通道。

### 三阶段记忆闭环设计

#### Phase A：Recall 使用日志（运行时，无感知）

**改动位置**：knowledge-navigation hooks.py 的 pre_llm_call 注入完成后

**现有基础**：trace.log 已记录 `recalled_ids`（第 1049 行），但没有持久化到数据库。

**设计**：

```
pre_llm_call 注入完成后，将 recalled_ids 写入 memory_use_log 表：
  → 表结构: (memory_id UUID, session_id, query_trunc text, injected_at timestamp)
  → 用 INSERT ... ON CONFLICT DO NOTHING（单 session 内同一记忆只记一次）
  → 与 trace.log 写入并行，不增加额外延迟
  → 复用已有的 DB 连接或直接写 PG（Hindsight 的 PG）
```

**引入代价**：约 20 行代码，knowledge-navigation 需要能访问 Hindsight 的 PG（当前只有 HTTP API 访问，需要新增 PG 直连或新增一个 Hindsight API endpoint）。

**替代方案**：不走 PG，先写本地文件（类似 trace.log 的 memory_use.jsonl），后续由 cron 聚合写入。零改动 knowledge-navigation 的数据库依赖。

#### Phase B：离线聚合 + 质量评估（cron）

**改动位置**：新建 cron 脚本或集成到 clustering-analysis-v3

**设计**：

```
1. Recall 频率聚合（每日）：
   → 从 memory_use_log 统计每条 memory_unit 的 recall_count / unique_sessions / last_recalled_at
   → 写回 memory_units 表的新字段（recall_count, last_recalled_at）
   → 或写入独立的 memory_stats 表（不改 Hindsight schema）

2. LLM 引用检测（RAGAS，与知识树反馈共用 Phase B）：
   → 从 trace.log 采样 (user_query, assistant_response, contexts)
   → contexts 中区分 source="hindsight" 的条目
   → RAGAS faithfulness 评估"注入的记忆是否被 LLM 实际使用"
   → 输出 per-memory_id 的 used/unused 标记

3. 冷记忆识别：
   → recall_count = 0 且 created_at > 30 天 → "从未被召回"
   → recall_count > 0 但 faithfulness 持续 < 0.3 → "召回但未被有效使用"
   → 两者都标记为 [标记: 待验证]
```

#### Phase C：自动生命周期管理（cron 触发）

**改动位置**：memory-cleanup 或新建 memory-lifecycle 脚本

**设计**：

```
1. 冷记忆淘汰（自动）：
   → created_at > 90 天 且 recall_count = 0 且 非标记保留 → [标记: 待验证]
   → 超过 180 天 且 recall_count = 0 → [标记: 作废]（在线排除生效）
   → 可配置时间阈值，保守起见只标记不删除

2. 高频记忆回升（L3 → L2）：
   → recall_count_decayed（指数衰减）持续 > 阈值（如每周被召回 5+ 次）
   → 且 faithfulness 持续 > 0.8（被 LLM 实际有效使用）
   → 自动 retain 到 MEMORY.md（与 memory-cleanup 的 hindsight 降级方向相反）
   → 这条通道当前完全不存在，是最大的架构缺口

3. Hindsight 关键词回填（修复 memory-cleanup 的丢弃问题，与 Phase A 同优先级）：
   → memory-cleanup 的 hindsight 分类时，LLM 生成的关键词标签被丢弃（只传 content）
   → 对已有 memory_units 做批量关键词标注（用 LLM 从 text 中提取）
   → 提升 Hindsight recall 的精确度
   → 注：此任务优先级为 P1（独立于 Phase C 的其他子项），因为它修复的是既有 bug 而非新功能

4. 质量报告扩展：
   → memory_quality_report.py 新增 recall 维度指标：
     - 零召回记忆占比（recall_count = 0 的比例）
     - 中位 recall_count
     - faithfulness 趋势（周环比）
```

### 记忆闭环 vs 知识闭环对比

```
知识树闭环（已有）:
  recall → knowledge_use_log → consolidation (recall_count_decayed) → 合并/淘汰/提权

记忆闭环（待建）:
  recall → memory_use_log → 聚合统计 → RAGAS faithfulness → 标记/淘汰/回升(L2)
                                                    ↓
                                           memory-cleanup 标记 → Hindsight 在线排除
```

### 引入代价评估

| 维度 | 成本 |
|------|------|
| 新增依赖 | RAGAS（与知识树反馈共用，不额外引入） |
| Phase A 运行时延迟 | 零（本地文件写入或异步 PG INSERT） |
| Phase B API 成本 | 与知识树反馈共用 RAGAS 评估，不额外增加 |
| 开发投入 | Phase A: 1 天；Phase B: 1-2 天（可与知识树 Phase B 合并）；Phase C: 2-3 天 |
| DB 改动 | Hindsight schema 需新增 memory_use_log 表（或用本地文件替代） |

### 优先级

| 优先级 | 环节 | 投入 | 价值 |
|--------|------|------|------|
| **P1** | Phase A: memory_use_log | 1 天 | 打通"使用可观测"基础 |
| **P1** | Phase B: 冷记忆识别 + RAGAS 评估 | 1-2 天 | 记忆质量可量化 |
| **P1** | Hindsight 关键词回填 | 半天 | 修复 memory-cleanup 的标签丢弃问题 |
| **P2** | Phase C: 冷记忆自动淘汰 | 1 天 | 记忆自然生命周期 |
| **P2** | Phase C: 高频记忆回升 L2 | 1-2 天 | L2↔L3 双向通道 |

---

## 六、数据质量治理闭环

前三章关注的是"消费端优化"（召回效率、记忆生命周期），本章关注"数据端治理"——既包含生产端的源头质量控制，也包含已入库数据的持续质量维护。

### 现状诊断

**已有治理手段（分散、各自为政）**：

| 治理手段 | 覆盖范围 | 局限 |
|---------|---------|------|
| knowledge-tree-builder 准入 (`_guard_filter`) | 新知识点入库前 | 只在入库时做一次，入库后不再检查 |
| knowledge-tree-builder 矛盾检测 (`_detect_conflicts`) | 新 vs 已有 | 条件提取正则覆盖率约 40%，大量矛盾漏检 |
| knowledge-tree-plugin placement 去重 | 在线增量入库 | 余弦 0.95 精确去重，但对语义近似的低质量知识点无能为力 |
| clustering mark_memory | 已有 memory_units | 基于关键词规则，不基于语义质量；只在聚类时触发 |
| memory_quality_report | 已有 memory_units | 4 条静态存储指标，不涉及内容准确性和跨系统一致性 |
| memory-cleanup classifier | L2 MEMORY.md/USER.md | 不覆盖 L3（Hindsight），且 compress/merge 规则对 USER.md 过松 |

**完全缺失的治理能力**：

| 缺失环节 | 影响 |
|---------|------|
| **跨系统一致性** | 同一条事实在知识树、Hindsight、MEMORY.md 三处可能表述矛盾，互不影响 |
| **入库后质量退化检测** | 知识点入库时正确，但随着环境变化变得过时或错误（如版本升级后的旧配置），没有主动检测 |
| **质量信号向上游传播** | RAGAS 检测到 faithfulness 低，不知道是提取质量问题还是召回匹配问题 |
| **数据血缘追踪** | 知识点从哪篇文档提取、哪次对话沉淀、经过几次 consolidate 修改 — 无追踪，出问题时无法回溯 |
| **Embedding 新鲜度** | clustering enriched_texts 后更新 embedding，但知识树的 embedding 可能与最新 text 不一致 |
| **批量质量扫描** | 没有对全库知识做周期性的语义质量评分（如自解释性、事实性、时效性） |

### 四阶段治理设计

#### Phase A：入库质量门控增强（生产端）

**改动位置**：knowledge-tree-builder（`admit.py`）+ knowledge-tree-plugin（`extract_new.py`）

**设计**：

```
1. 矛盾检测条件提取升级（admit.py）：
   → 现有正则只覆盖"在...上/下/时/后/中"
   → 扩展覆盖"当...时/若...则/一旦/倘若/在...情况下/除非/只有...才"
   → 对 0.80 < cosine < 0.90 灰度区间，改用 LLM 判断矛盾（而非纯正则）
   → 预期：矛盾检测覆盖率从 ~40% 提升到 ~80%

2. 准入门控白名单化（extract_new.py）：
   → 现有 _GUARD_CONFIG_PATTERNS 黑名单模式会误过滤技术参数类知识
   → 改为：黑名单过滤 + 白名单豁免（如匹配到"默认端口|配置项|参数说明"等模式时放行）
   → 对"Redis 默认端口 6379"这类事实性参数知识不应被过滤

3. 入库时打质量标签（admit.py + extract_new.py）：
   → 每条知识入库时附加 metadata 字段：
     - source_type: "document" | "dialog"（区分文档提取 vs 对话沉淀）
     - extraction_confidence: "high" | "medium" | "low"（基于 LLM 提取时的原始分数或规则判断）
     - has_condition: bool（是否含条件从句，影响时效性）
   → 供后续治理扫描使用，不改 recall 逻辑
```

**引入代价**：~1 天，零新依赖。

#### Phase B：全库质量扫描（入库后，周期性）

**改动位置**：新建 cron 子项目或集成到 clustering-analysis-v3

**设计**：

```
1. 语义质量评分（每周，对全库/抽样执行）：
   → 用 LLM 对每条知识做 3 维评分（可批量处理）：
     - accuracy: 内容是否事实正确（基于知识本身的自洽性）
     - specificity: 是否自解释（不含指代代词"该系统""上述方法"）
     - currency: 是否包含可能过时的信息（版本号、日期、环境依赖）
   → 评分写入 quality_score 表：(memory_id, accuracy, specificity, currency, scored_at)
   → cost: ~3000 条 x 3 维 x ~200 tokens/维 = ~1.8M tokens ≈ $0.5/次（GPT-4o-mini）

2. 跨系统一致性检查（每周）：
   → 对知识树和 Hindsight 的重叠区域做交叉检查：
     - 同一实体在不同系统中描述是否矛盾
     - 方法：按实体 → 聚合相关知识 → LLM 判断一致性
   → 矛盾对标记为 [标记: 待验证]，写入 review_queue

3. Embedding 新鲜度检查（每月）：
   → 对比 knowledge_tree.k_vector 与当前 text 的 embedding：
     - 如果 text 在 consolidate 后被修改，但 k_vector 未更新
     - cosine(text_new_emb, k_vector) < 0.95 → 标记需要重新 embed
   → 批量更新不一致的 embedding

4. 数据血缘记录（入库时写入）：
   → knowledge_tree 表新增字段：
     - source_file: 来源文件路径（文档提取时）
     - source_session_id: 来源 session（对话沉淀时）
     - extraction_method: "llm_batch" | "llm_single" | "dialog_post_llm"
     - consolidate_count: 经历过几次 consolidate 修改
   → knowledge-tree-plugin 的 post_llm_call 入库时记录 source_session_id
   → 出问题时可追溯来源
```

**引入代价**：Phase B1（语义评分）~2 天；Phase B2（一致性）~2 天；Phase B3（embedding 新鲜度）~1 天；Phase B4（血缘）~1 天。

#### Phase C：质量驱动的自动修复（cron 触发）

**改动位置**：clustering-analysis-v3 + knowledge-tree-builder consolidate

**设计**：

```
1. 低质量自动处理（基于 Phase B 评分）：
   → accuracy < 0.5 → [标记: 可疑]（人工复核）
   → specificity < 0.3 → 自动改写（用 LLM 重写为自解释版本，保留核心事实）
   → currency < 0.5 且含版本号 → [标记: 待验证]（可能过时）

2. 重复知识合并（跨系统）：
   → Phase B2 发现的跨系统重复 → 自动合并：
     - 保留更完整/更新的版本
     - 在另一方标记 [标记: 已合并到 knowledge_tree_id]
   → 合并前用 LLM 确认语义等价

3. 质量信号向上游传播：
   → 从 RAGAS faithfulness 数据反查 source_type：
     - 如果 source_type="document" 的知识点 faithfulness 持续低 → 标记来源文档需要重新审阅
     - 如果 source_type="dialog" 的记忆 faithfulness 持续低 → 可能是对话提取时的幻觉
   → 输出"质量热点文档/会话"列表，供人工审阅或触发重新提取

4. consolidate 质量阈值联动：
   → knowledge-tree-builder 的 consolidate 算法当前用 confidence 衰减
   → 新增：consolidate 时检查 quality_score，accuracy 低的条目优先进入 review_queue
   → 而不是等 consolidate 自动合并（可能把低质量知识合并进去）
```

### 数据质量治理开源框架调研

经过调研，以下框架在数据质量治理领域有一定参考价值，但针对 Hermes 的轻量 cron 场景，均不建议引入：

| 框架 | 定位 | 不推荐理由 |
|------|------|----------|
| **Great Expectations** | 数据校验与文档生成 | 主要面向结构化数据（DB/CSV），对非结构化文本的语义质量（准确性、自解释性）无能为力；依赖重（需要 pandas 等），不适合轻量 cron |
| **Soda** | 数据质量检查 | 与 Great Expectations 类似，侧重于 SQL/表级规则校验，不覆盖 LLM 生成的文本质量评估 |
| **deepeval** | LLM 评估 | 虽然能做文本评估，但已在第二章作为 RAGAS 的替代方案被否决（功能重叠，没必要同时引入两个评估框架） |
| **Cleanlab** | 数据清洗/标注质量 | 专注于监督学习的标签噪声清洗，不适用于知识库文本的时效性/一致性治理 |

**结论**：Hermes 的数据质量治理核心是“非结构化文本的语义质量评估与跨系统一致性检查”，这超出了传统数据质量框架的能力边界。继续沿用 LLM-as-a-Judge（如 Phase B 中的 LiteLLM 调用）+ RAGAS 是最贴合现状的轻量方案。

### 治理闭环 vs 其他闭环的关系

```
数据质量治理闭环（第六章，本章）:
  生产端门控 → 全库扫描 → 自动修复 → 反哺飞轮
       ↑                    ↓
       │            质量信号（评分/标记）
       │                    ↓
       └──── 反哺 ──── 知识反馈闭环（第四章）
                         → RAGAS faithfulness 低时，触发本章 Phase B 复扫
                       记忆闭环（第五章）
                         → 冷记忆淘汰时，检查是否为低质量记忆（加速淘汰）
```

### 引入代价评估

| 维度 | 成本 |
|------|------|
| 新增依赖 | 不引入新框架（LLM 评分用已有 LiteLLM） |
| API 成本 | 全库扫描 ~$0.5/次（GPT-4o-mini，3000 条 x 3 维），月 ~$2 |
| 开发投入 | Phase A: 1 天；Phase B: 5-6 天；Phase C: 3-4 天 |
| 存储开销 | quality_score 表（~3000 行 x 4 字段），数据血缘字段（已有表加列） |

### 优先级

| 优先级 | 环节 | 投入 | 价值 |
|--------|------|------|------|
| **P2** | Phase A: 入库门控增强 | 1 天 | 从源头减少低质量数据进入 |
| **P2** | Phase B1: 全库语义质量评分 | 2 天 | 首次获得全库质量全景 |
| **P2** | Phase B4: 数据血缘记录 | 1 天 | 出问题可回溯 |
| **P2** | Phase C1: 低质量自动处理 | 2 天 | 自动修复可修复的质量问题 |
| **P3** | Phase B2: 跨系统一致性检查 | 2 天 | 消除跨系统矛盾 |
| **P3** | Phase B3: Embedding 新鲜度 | 1 天 | 保证 recall 精度 |
| **P3** | Phase C2-4: 合并/传播/联动 | 2-3 天 | 治理闭环自动化 |

---

## 七、其他优化项（中低优先级）

| 优先级 | 优化 | 项目 | 做法 |
|--------|------|------|------|
| P1 | Token 预算守门 | knowledge-navigation | 新增 `inject_token_budget`（默认 2000 token），后处理排序后逐条累计，超出裁切低分条目 |
| P1 | 跨域去重改为降权 | knowledge-navigation | `cross_domain_dedup` 不删除 KT 结果，改为 `final_score *= 0.3` 降权 |
| P1 | HDBSCAN 自适应参数 | clustering-analysis-v3 | 根据 N 动态调整 `min_cluster_size = max(3, N//50)` |
| P1 | 因果链检测增量化 | clustering-analysis-v3 | 只对新增成员间的 pair 做检测，跳过旧-旧 pair |
| P1 | MEMORY compress 质量规则加强 | memory-cleanup | USER.md compress 也检查关键实体保留率，关键词重叠提升到 15% |
| P2 | 知识图谱增强 | 新建子项目 | 从 memory_units 中 LLM 抽取 (subject, relation, object) 三元组，写入独立表 |
| P2 | 时态感知增强 | knowledge-tree-plugin | 知识点写入时打 `temporal_tag`（版本相关/永恒知识），recall 时动态调整时态融合权重 |
| P2 | 领域缓存改用路径 hash | knowledge-tree-builder | `_p4_domains` key 从 title 改为 `{path_hash}` |
| P2 | Skill index mtime 增量 | knowledge-navigation | `ensure_index` 用文件 mtime 检查只扫描变更文件（P0-1 已覆盖此需求，实施时合并） |
| P2 | dedup_memories 用 MinHash 替代 Jaccard | clustering-analysis-v3 | 将已有 `dedup_minhash.py` 整合进主管线 |
| P2 | 缓存文件统一管理 | knowledge-tree-builder | 3 类缓存统一到 `.kb_cache/` 目录 |

---

## 八、优先级排序总表

| 优先级 | 项目 | 引入框架 | 投入 | 预期效果 |
|--------|------|---------|------|----------|
| **P0** | Skill Matcher 两级筛选 | 不引入（用现有 bge-m3） | 半天 | 延迟 ~87%，API 成本 ~90% |
| **P0** | 去重下推 pgvector | 不引入（改 SQL） | 半天 | 去重性能 ~480x 提升 |
| **P0** | LLM 调用合并 | 不引入 | 半天 | 建树速度 +40% |
| **P1** | 自动反馈飞轮 Phase A | 不引入 | 半天 | 零成本，为后续评估铺数据 |
| **P1** | 自动反馈飞轮 Phase B | **RAGAS** | 1-2 天 | Recall 质量可量化 |
| **P1** | 记忆闭环 Phase A (memory_use_log) | 不引入 | 1 天 | Hindsight 使用可观测 |
| **P1** | 记忆闭环 Phase B (冷记忆+RAGAS) | **RAGAS**（共用） | 1-2 天 | 记忆质量可量化 |
| **P1** | Hindsight 关键词回填 | 不引入 | 半天 | 修复标签丢弃问题 |
| **P1** | Token 预算守门 | 不引入 | 1 天 | Context 膨胀控制 |
| **P1** | 自动反馈飞轮 Phase C | 不引入 | 2-3 天 | Router 微调 + 参数自适应 + 知识树质量标记 |
| **P1** | 跨域去重改为降权 | 不引入 | 半天 | 避免误删语义近似的高质量知识 |
| **P1** | HDBSCAN 自适应参数 | 不引入 | 半天 | 小/大数据集分簇质量提升 |
| **P1** | 因果链检测增量化 | 不引入 | 半天 | 避免旧-旧关系遗漏 |
| **P1** | MEMORY compress 质量规则加强 | 不引入 | 半天 | 防止 USER.md 过度压缩 |
| **P2** | 记忆闭环 Phase C (冷记忆淘汰) | 不引入 | 1 天 | 记忆自然生命周期 |
| **P2** | 记忆闭环 Phase C (高频回升 L2) | 不引入 | 1-2 天 | L2↔L3 双向通道 |
| **P2** | 质量治理 Phase A (入库门控) | 不引入 | 1 天 | 源头减少低质量数据 |
| **P2** | 质量治理 Phase B1 (语义评分) | 不引入 | 2 天 | 全库质量全景 |
| **P2** | 质量治理 Phase B4 (数据血缘) | 不引入 | 1 天 | 出问题可回溯 |
| **P2** | 质量治理 Phase C1 (自动修复) | 不引入 | 2 天 | 自动修复可修复问题 |
| **P3** | 质量治理 Phase B2 (跨系统一致性) | 不引入 | 2 天 | 消除跨系统矛盾 |
| **P3** | 质量治理 Phase B3 (Embedding 新鲜度) | 不引入 | 1 天 | 保证 recall 精度 |
| **P3** | 质量治理 Phase C2-4 | 不引入 | 2-3 天 | 治理自动化 |
| **P3** | 知识图谱增强 | 不引入 | 3-5 天 | 跨域关系召回 |
| **P3** | 时态感知增强 | 不引入 | 1-2 天 | 召回时效性提升 |
| **P2** | 领域缓存改用路径 hash | 不引入 | 半天 | 避免同名文件覆盖缓存 |
| **P2** | dedup_memories 用 MinHash | 不引入 | 半天 | O(n) 替代 O(n^2) Jaccard |
| **P2** | 缓存文件统一管理 | 不引入 | 半天 | 3 类缓存统一到 `.kb_cache/` |

**总结**：只推荐引入 RAGAS（1 个新依赖，知识反馈 + 记忆反馈 + 质量治理共用）。整体架构从四个层面闭环运转：

```
P0: 性能优化（消费端效率）
P1: 知识反馈闭环（召回质量可量化） + 记忆生命周期闭环（使用可观测）
P2: 数据质量治理闭环（生产端门控 + 已入库数据持续治理）
P3: 进阶能力（跨系统一致性、知识图谱、时态感知）
```
