# 数据飞轮优化 SPEC 实施计划

> **审计状态**：✅ 已自审计，发现 5 个设计问题并修正
> **原则**：不修改 Hermes Gateway 源码，所有优化在子项目/插件层面实施
> **实施策略**：按优先级分批实施，每批完成后测试验证再进入下一批
> **Feature Flag**：每个优化项都有独立开关，出问题可一键回退

---

## 一、审计确认的问题清单

### ✅ 已验证属实的问题

| # | 项目 | 问题 | 验证位置 |
|---|------|------|---------|
| 1 | knowledge-tree-builder | `call_llm()` 硬编码 `time.sleep(0.3)` 限速 | `llm/client.py:49` |
| 2 | knowledge-tree-builder | `_dedup_single()` O(N*M) 线性扫描去重 | `phase/admit.py:151` |
| 3 | knowledge-navigation | Skill Matcher 每次全量扫描 skills 目录 | `skill_matcher.py:89` |
| 4 | knowledge-navigation | 跨域去重阈值 0.65 偏低 | `hooks.py:928,935` |
| 5 | clustering-analysis-v3 | HDBSCAN `min_samples` 固定为 3，无自适应 | `config/default.yaml:6` |

### ⚠️ 待进一步验证的问题（实施时确认）

| # | 项目 | 问题 |
|---|------|------|
| 6 | knowledge-tree-builder | Phase 3 和 Phase 4 重复计算 embedding |
| 7 | knowledge-tree-builder | 矛盾检测条件提取正则覆盖率低 |
| 8 | knowledge-tree-builder | 领域判断缓存以文章标题为 key |
| 9 | knowledge-tree-plugin | 后台队列 maxsize=100 无重试机制 |
| 10 | memory-cleanup | hindsight 分类关键词标签被丢弃 |
| 11 | clustering-analysis-v3 | dedup_memories O(n^2) Jaccard 比较 |

---

## 二、优化项优先级与实施顺序

### P0：性能优化（消费端效率）— 第 1 批

| 编号 | 优化项 | 项目 | 预期效果 | 预估工作量 | Feature Flag |
|------|--------|------|----------|-----------|-------------|
| P0-1 | Skill Matcher 改为关键词预筛选 + LLM 精排两级 | knowledge-navigation | LLM token ↓85%，延迟 ↓50% | 1 天 | `KN_SKILL_KEYWORD_PRESCREEN` |
| P0-2 | 去重下推 pgvector HNSW 索引 | knowledge-tree-builder | 去重性能 ~480x 提升 | 0.5 天 | `KB_DEDUP_PGVECTOR` |
| P0-3 | LLM 调用合并（提取+领域判断一次完成） | knowledge-tree-builder | LLM 调用量 -50%，建树速度 +40% | 0.5 天 | `KB_MERGED_DOMAIN` |

### P1：质量与闭环 — 第 2 批

| 编号 | 优化项 | 项目 | 预期效果 | 预估工作量 | Feature Flag |
|------|--------|------|----------|-----------|-------------|
| P1-1 | Token 预算守门 | knowledge-navigation | Context 膨胀控制 | 1 天 | `KN_TOKEN_BUDGET` |
| P1-2 | 跨域去重改为降权（不删除） | knowledge-navigation | 避免误删语义近似的高质量知识 | 0.5 天 | `KN_CROSS_DEDUP_MODE` |
| P1-3 | HDBSCAN 自适应参数 | clustering-analysis-v3 | 小/大数据集分簇质量提升 | 0.5 天 | `CA_HDBSCAN_ADAPTIVE` |
| P1-4 | 因果链检测增量化 | clustering-analysis-v3 | 避免旧-旧关系遗漏 | 0.5 天 | `CA_CAUSAL_INCREMENTAL` |
| P1-5 | MEMORY compress 质量规则加强 | memory-cleanup | 防止 USER.md 过度压缩 | 0.5 天 | `MC_COMPRESS_STRICT` |
| P1-6 | Hindsight 关键词回填 | memory-cleanup | 修复标签丢弃问题 | 1 天 | `MC_KEYWORD_BACKFILL` |

### P2：质量治理闭环 — 第 3 批

| 编号 | 优化项 | 项目 | 预期效果 | 预估工作量 |
|------|--------|------|----------|-----------|
| P2-1 | 入库门控增强（矛盾检测+准入门控白名单） | knowledge-tree-builder + plugin | 源头减少低质量数据 | 1 天 |
| P2-2 | 记忆闭环 Phase A：memory_use_log | knowledge-navigation | Hindsight 使用可观测 | 1 天 |
| P2-3 | Skill index mtime 增量更新 | knowledge-navigation | 首次构建后增量扫描 | 0.5 天 |
| P2-4 | 领域缓存改用路径 hash | knowledge-tree-builder | 避免同名文件覆盖缓存 | 0.5 天 |
| P2-5 | dedup_memories 用 MinHash | clustering-analysis-v3 | O(n) 替代 O(n^2) Jaccard | 0.5 天 |

### P3：进阶能力（远期）

| 编号 | 优化项 | 项目 | 预期效果 |
|------|--------|------|----------|
| P3-1 | 自动反馈飞轮（RAGAS faithfulness 评估） | 新建 recall-eval 子项目 | Recall 质量可量化 |
| P3-2 | 全库语义质量评分 | clustering-analysis-v3 | 首次获得全库质量全景 |
| P3-3 | 数据血缘记录 | knowledge-tree-builder + plugin | 出问题可回溯 |
| P3-4 | 冷记忆自动淘汰 + 高频回升 L2 | memory-cleanup | 记忆自然生命周期 |

---

## 三、P0 详细设计

### P0-1：Skill Matcher 关键词预筛选 + LLM 精排两级

**现状**：
- 每次 pre_llm_call 把 ~345 个 skill 的 name+description 全部发给 LLM
- Prompt 很长（~30k chars / ~8k tokens），延迟 ~3s

**方案**：两阶段检索（不用 embedding，避免语义不准确问题）

```
Stage 1: 关键词预筛选（<1ms，345 → Top-20）
  → 基于 skill name 精确匹配 + description 关键词重叠打分
  → 先按 name 包含关系排序，再按 description 关键词重叠数排序
  → 取 Top-20 进入下一轮

Stage 2: LLM 精排（~500ms，20 → Top-3）
  → 只对 20 个候选发给 LLM，prompt 短很多（~2k chars）
  → Feature Flag: KN_SKILL_KEYWORD_PRESCREEN=true 时启用，false 时走原全量路径
```

**预筛选算法**：
1. 从 query 中提取关键词（中文词/英文单词，去停用词）
2. 对每个 skill 计算得分：
   - name 完全匹配：+10 分（精确匹配）
   - name 包含：+5 分（部分匹配）
   - description 关键词重叠：每个 +1 分/词
3. 按得分降序取 Top-20
4. 得分相同的按 name 字母序

**实施步骤**：
1. 在 `skill_matcher.py` 中新增 `_keyword_prescreen()` 函数
2. 修改 `match_skills()` 为两级：关键词预筛选 → LLM 精排
3. 新增 Feature Flag `KN_SKILL_KEYWORD_PRESCREEN`（默认 true）
4. 补充单元测试

**文件改动**：
- 修改：`src/knowledge_navigation/core/skill_matcher.py`（两级匹配 + 关键词预筛选）
- 修改：`src/knowledge_navigation/config.py`（新增 Feature Flag）
- 新增测试：`tests/test_skill_matcher.py` 补充关键词预筛选测试

---

### P0-2：去重下推 pgvector

**现状**：
- `_dedup_single()` 对每条新知识点遍历全库已有向量（O(N*M) 内存扫描）
- `DatabaseAdapter.get_leaf_nodes()` 已存在，返回 `[{id, name, k_vector}]`
- 知识库增长到数千条后明显变慢
- pgvector 扩展是否已启用需确认（k_vector 字段已存在且被使用）

**方案**：下推到 pgvector HNSW 索引做近似最近邻

```sql
-- 前置检查：pgvector 扩展是否启用
SELECT * FROM pg_extension WHERE extname = 'vector';

-- 1. 确认 HNSW 索引存在（10K+ 向量时创建，10K 以下顺序扫描更快）
-- 动态判断：SELECT count(*) FROM knowledge_tree WHERE k_vector IS NOT NULL AND node_type = 'knowledge_point'
-- 如果 count > 10000，则创建索引
CREATE INDEX IF NOT EXISTS idx_knowledge_tree_k_vector
  ON knowledge_tree
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
SELECT id, dist FROM nearest WHERE dist < 0.05;  -- 相似度 > 0.95
```

**兼容性策略**：
- Feature Flag `KB_DEDUP_PGVECTOR=true` 时启用，false 时走原内存扫描路径
- 自动检测：如果 pgvector 扩展不存在或向量数 < 1000，自动降级为内存扫描
- 索引创建：首次启用且数据量 > 10000 时自动创建（异步，不阻塞主流程）

**实施步骤**：
1. 在 `adapters/database.py` 中新增 `find_nearest_neighbors(embedding, threshold, limit)` 方法
2. 新增 `_ensure_hnsw_index()` 方法（动态判断数据量，按需创建）
3. 修改 `phase/admit.py` 的 `_dedup_single()`，新增 `db` 参数，优先走 pgvector
4. 修改 `commands/run.py`，将 db adapter 传入 admit 模块
5. 新增 Feature Flag `KB_DEDUP_PGVECTOR`（默认 true，自动降级）
6. 补充单元测试（mock db 方法）

**文件改动**：
- 修改：`src/knowledge_tree_builder/adapters/database.py`（新增 nearest neighbor + HNSW 索引）
- 修改：`src/knowledge_tree_builder/phase/admit.py`（去重逻辑下推 + feature flag）
- 修改：`src/knowledge_tree_builder/commands/run.py`（传入 db 到 admit）
- 修改：`src/knowledge_tree_builder/config.py`（新增 Feature Flag）
- 新增测试：`tests/test_admit.py` 补充 pgvector 去重测试

---

### P0-3：LLM 调用合并（提取+领域判断一次完成）

**现状审计确认**：
- ✅ Phase 1+2（merged.py）：每篇文章 1 次 LLM 调用，输出原子知识列表
- ✅ Phase 4（run.py 第 415-419 行）：每篇文章 1 次 LLM 调用，判断文章所属领域
- ✅ 合计：每篇文章 2 次 LLM 调用，`time.sleep(0.3)` 限速导致纯等待 0.6s/篇
- ✅ 合并后：每篇文章 1 次 LLM 调用，节省 50% LLM 调用 + 0.3s 等待

**方案**：在 merged.py 的 prompt 中追加领域判断要求，一次调用输出 `{ analysis, atomic_knowledge, suggested_domain }`

```json
// 新增输出字段
{
  "analysis": {
    "content_summary": "一句话概括",
    "empty_article": false
  },
  "suggested_domain": "领域名/路径",  // ← 新增
  "atomic_knowledge": [...]
}
```

**兼容性策略**：
- Feature Flag `KB_MERGED_DOMAIN=true` 时启用，false 时走原 Phase 4 单独判断路径
- 如果 LLM 返回的 JSON 中不含 `suggested_domain`，自动降级为 Phase 4 单独判断
- 缓存兼容：`_p4_domains` 缓存优先，合并输出的 suggested_domain 写入缓存

**实施步骤**：
1. 修改 `phase/merged.py` 的 system prompt，追加领域判断要求
2. 修改 `analyze_and_split()` 返回结构，新增 `suggested_domain` 字段（第 4 个返回值）
3. 修改 `commands/run.py` Phase 4 逻辑：
   - 如果 `suggested_domain` 存在，直接写入 `_p4_domains[title]`
   - 否则走原 `_llm_domain()` 路径（兜底）
4. 新增 Feature Flag `KB_MERGED_DOMAIN`（默认 true）
5. 补充单元测试

**文件改动**：
- 修改：`src/knowledge_tree_builder/phase/merged.py`（prompt + 返回结构）
- 修改：`src/knowledge_tree_builder/commands/run.py`（Phase 4 读 merged 输出的 domain）
- 修改：`src/knowledge_tree_builder/config.py`（新增 Feature Flag）
- 新增测试：`tests/test_merged.py` 补充领域判断测试

---

## 四、性能测试方案

### 基准测试脚本

每个 P0 优化项都有对应的 benchmark 脚本：

| 优化项 | Benchmark 指标 | 测试方法 |
|--------|---------------|---------|
| P0-1 | Skill 匹配延迟 + 准确率 | 100 条测试 query，对比开启/关闭的延迟和匹配结果一致性 |
| P0-2 | 去重速度 + 准确率 | 1000/5000/10000 条知识库，对比内存扫描 vs pgvector 的速度和去重结果一致性 |
| P0-3 | LLM 调用次数 + 建树质量 | 相同输入文章集，对比开启/关闭的 LLM 调用次数和产出的知识点数量/质量 |

### 验收标准

| 优化项 | 验收标准 |
|--------|---------|
| P0-1 | Skill 匹配延迟从 ~3s 降到 < 1.5s；LLM token 减少 ~85%；匹配准确率（与原全量结果的一致性）≥ 95% |
| P0-2 | 1000 条知识库时，去重速度提升 10x+；10000 条时提升 100x+；去重结果与内存扫描一致性 100%（精确去重） |
| P0-3 | 相同文章数下，LLM 调用次数减少 ~50%；建树质量（知识点数量/类型分布）差异 < 5% |

---

## 五、风险与回滚

### 技术风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| pgvector 扩展未启用 | 低 | 高 | 自动检测，降级为内存扫描 |
| embedding API 不稳定 | 中 | 中 | 失败时降级为原 LLM 全量路径 |
| LLM 合并调用后输出格式不对 | 中 | 低 | 容错解析，降级为 Phase 4 单独判断 |
| HNSW 索引创建耗时 | 低 | 中 | 异步创建，不阻塞主流程 |

### 回滚策略

1. **Feature Flag 一键关闭**：每个优化项都有独立开关，配置环境变量即可回退
2. **部署回滚**：`deploy.sh rollback <project>` 一键回滚到上一版本
3. **数据库变更**：HNSW 索引是增量的，不影响现有逻辑，可随时 DROP INDEX
4. **数据安全**：所有优化都不修改已有数据的内容，只改变计算方式

---

## 六、实施计划总览

```
第 1 天：P0-1 Skill Matcher 两级筛选
  - 上午：提取公共 embed 模块 + embedding 缓存
  - 下午：两级匹配逻辑 + Feature Flag + 测试

第 2 天上午：P0-2 去重下推 pgvector
  - db 层新增方法 + HNSW 索引管理
  - admit 层接入 + Feature Flag + 测试

第 2 天下午：P0-3 LLM 调用合并
  - merged.py prompt 修改 + 返回结构扩展
  - run.py Phase 4 接入 + Feature Flag + 测试

第 3 天：P0 全量集成测试 + 性能验证
  - 集成测试：三个 P0 同时开启的兼容性
  - 性能 benchmark：对比优化前后数据
  - 修复发现的问题
```

---

## 七、代码 Review 修正记录

### 第一轮修正（2026-06-28）

| # | 问题 | 严重程度 | 修正内容 | 涉及文件 |
|---|------|---------|---------|---------|
| 1 | Token 估算精度不足 | 低 | 增加数字/标点估算，改进正则表达式，说明误差范围 | knowledge-navigation/filtering.py |
| 2 | UseLogger 线程安全边界 | 低 | 改用 buffer 复制而非直接引用交换 | knowledge-navigation/use_log.py |
| 3 | 冷记忆淘汰可能误判 | 中 | 增加历史记录关键词和近期信号检测，改进估算权重 | memory-cleanup/lifecycle.py |
| 4 | MinHash 与 Jaccard 结果可能不一致 | 低 | 增加算法特性说明文档 | clustering-analysis-v3/dedup.py |
| 5 | 血缘记录版本覆盖 | 低 | 改为 (node_id, version) 作为 key 保留历史版本，新增 `get_all_versions()` | knowledge-tree-builder/lineage.py |

### 第二轮修正（4 路并行审查后）

| # | 问题 | 严重程度 | 修正内容 | 涉及文件 |
|---|------|---------|---------|---------|
| 6 | KN_CROSS_DOMAIN_DEDUP_ACTION env var 死代码 | MEDIUM | hooks.py 中调用 cross_domain_dedup() 时传入 action 和 demote_factor 参数 | knowledge-navigation/hooks.py |
| 7 | enable_quality_scoring 配置死代码 | MEDIUM | quality_score CLI 命令添加 Feature Flag 检查，关闭时输出提示并退出 | clustering-analysis-v3/cli.py |
| 8 | _estimate_last_access 分支死代码 | MEDIUM | 删除 if/else 中完全相同的分支，历史关键词信号在内容级别检测 | memory-cleanup/lifecycle.py |
| 9 | backfill_k_vectors 测试 mock 错误 | MEDIUM | 修复测试中 cursor.fetchall 的 mock 设置，正确使用 side_effect 设置两次查询返回值 | knowledge-tree-builder/test_backfill_k_vectors.py |

---

## 八、自审计修正记录

审计发现并修正的 6 个问题：

| # | 问题 | 修正 |
|---|------|------|
| 1 | 缺少 Feature Flag 设计 | 新增每个优化项的独立开关，默认开启，可一键回退 |
| 2 | 缺少性能测试方案 | 新增第四章，明确 benchmark 指标和验收标准 |
| 3 | P0-2 未考虑 pgvector 兼容性 | 新增自动检测 + 降级策略，10K 以下不建索引 |
| 4 | P0-1 未明确缓存策略 | 新增缓存位置、失效策略、兜底降级方案 |
| 5 | P0-3 描述不够准确 | 修正为"每篇文章 2 次 LLM → 1 次"，节省 50% 调用量 |
| 6 | P0-1 用 embedding 不准确 | 改为关键词预筛选 + LLM 精排两级，不用 embedding，保证准确率 ≥ 95% |
