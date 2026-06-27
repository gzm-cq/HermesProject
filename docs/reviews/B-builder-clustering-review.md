# Agent B 审查报告：knowledge-tree-builder + clustering-analysis-v3

> **文档状态：历史审查报告 / 修复前问题发现**  
> 本文是 Agent B 的只读审查结果，问题是否已关闭请以 `03-post-fix-audit-2026-06-15.md` 和当前源码为准。


**日期**: 2026-06-15  
**审查人**: Agent B  
**源码根目录**: `/mnt/d/HermesProject`

---

## 一、审查范围与方法

### 范围
| 模块 | 源码路径 | 关键文件数 | 测试通过率 |
|------|---------|-----------|-----------|
| 知识树建树 | `scripts/knowledge-tree-builder` | 54 py | **265/265 (100%)** |
| 聚类分析 v3 | `scripts/clustering-analysis-v3` | 23 py | **68/71 (95.8%)** 3 failed |

### 方法
- 逐行阅读关键源码（place.py、database.py、admit.py、cli.py、核心聚类、DB 适配器、embedding）
- 检查测试覆盖率（test_place.py、test_review.py、test_database.py、test_clustering.py）
- 运行 builder dry-run 全管线（`--merged --dry-run`，成功 2 篇 19 条原子知识）
- 查询生产 DB schema + 索引 + vector 完整性（pgvector 已安装，1024 维）

---

## 二、疑点核实结果（逐一对应 Review Plan）

### 2.1 Builder：`_write_to_db()` 不是整体原子事务

**状态**: ⚠️ P1 确认  
**证据**:
- `place.py:237` 注释明示：`"find_or_create_subject 等内部方法自己 commit，无需外包装事务。所有操作都是幂等的"`
- `adapters/database.py:456`：`find_or_create_subject` 内新建节点后调用 `self.conn.commit()`
- `adapters/database.py:490`：`update_k_vector` 内每次调用 `self.conn.commit()`
- `place.py:280-303`：`_write_to_db` 中逐条 INSERT（with RETURNING id）→ executemany 插 point_texts → 逐条 update_k_vector，分散在 **多个隐式事务** 中

**风险**: 假如第 3 步（update_k_vector）部分失败，第 1-2 步已 commit 的节点会永久缺少 k_vector（zombie nodes）。  
**DB 验证**: 生产 DB 中 `knowledge_tree` 有 5649 个 knowledge_point，但只有 2402 个有 k_vector（`count(k_vector)=2402`）— 存在大量缺失 k_vector 的节点。

---

### 2.2 Builder：`source_articles.insert_article()` 幂等性

**状态**: ⚠️ P1 确认  
**证据**:
- `adapters/database.py:71-79`：`INSERT INTO source_articles ... VALUES ... RETURNING id` — 无 `ON CONFLICT`、无前置检查
- `adapters/database.py:269-277`：`insert_point_text` 同样无幂等保障

**风险**: 重复运行管线将产生重复的 source_articles 和 knowledge_point_texts 记录。  
**现有限制**: `_write_to_db()` 在 place.py:246-250 中通过 `SELECT text FROM knowledge_point_texts WHERE text = ANY(%s)` 做**文本级**去重，但对 source_articles 无保护。

---

### 2.3 Builder：`retrieval_confidence` 列缺失

**状态**: ⚠️ P2（schema mismatch，但生产列已存在）  
**证据**:
- `database.py:496-498` 引用 `UPDATE knowledge_tree SET retrieval_confidence = %s`
- `database.py:502-504` `get_all_nodes_with_confidence()` SELECT retrieval_confidence
- `cli.py:1960` 使用 `SET retrieval_confidence = CASE id ...`
- **但 `create_tables()` (database.py:302-376) 并未创建该列** — 建表 SQL 无 `retrieval_confidence FLOAT`
- 生产 DB 的 `information_schema.columns` 显示 `knowledge_tree.retrieval_confidence` 存在（`float8`）— 该列是手动或通过其他 migration 添加的

**问题**: `create_tables()` 首次部署时不会创建该列，导致新版 `consolidate` 命令在新环境报错。

---

### 2.4 Builder：review_queue status 不一致（`pending` vs `pending_review`）

**状态**: 🚨 **P0 确认**  
**证据**:
- `database.py:166`：`INSERT ... VALUES (..., 'pending')` — 插入默认值为 `'pending'`
- `database.py:537`：`list_review_queue(..., status: str = "pending_review")` — 查询默认值为 `'pending_review'`
- `consolidate/review.py:60`：`list_reviews(..., status: str = "pending_review")` — 同样用 `pending_review`
- `consolidate/review.py:178`：`insert_review_item` 设置 `"status": "pending_review"` — **不同于** database.py insert 的 `'pending'`
- `consolidate/review.py:109`：`process_timeouts` 查询 `list_review_queue(status="pending_review")`

**DB 验证结果**: `SELECT status, count(*) FROM knowledge_review_queue GROUP BY status;` → 返回 `pending: 8`, `pending_review: 0`  
✅ **DB 中全部 8 条记录为 'pending'，没有任何一条能被 'pending_review' 查询命中**

**影响**: 
- `knowledge-tree-builder review list` 不显示任何待审项
- `consolidate process-timeouts` 无法处理超时项
- 已有 8 条 pending 记录被静默忽略

---

### 2.5 Builder：Phase4 subject k_vector 缺失 → 持续创建"新科目"

**状态**: ⚠️ P1 确认  
**证据**:
- `place.py:94-99`：`_match_or_create_subject` 中若 subject 无 `k_vector`，循环 `for subj in existing_subjects` 中子句 `if subj.get("k_vector"):` 跳过——完全不参与相似度匹配
- `place.py:101`：无匹配时返回 `("新科目", True)`
- DB 验证: 54 个 subject 中 only 33 有 k_vector（21 个缺少）

**风险**: 约 39% 的 subject 没有 k_vector，无法参与余弦匹配。挂靠该 subject 下知识点的匹配会失败，持续创建"新科目"或归入"其他"。

---

### 2.6 Clustering：HDBSCAN 未显式 L2 normalize

**状态**: ⚠️ P1 确认  
**证据**:
- `clustering.py:494-499`：`run_hdbscan_clustering` 中配置 `metric="euclidean"`，但**未对输入 embeddings 做 L2 normalize**
- `cli.py:346`：仅 `embeddings = np.array(embeddings_list)`，无 normalize
- 同一文件中 `compute_semantic_similarity` (clustering.py:332-334) 有 `normalized = embeddings / norm` — 但 HDBSCAN 分支不走此路径

**风险**: bge-m3 返回的非归一化向量，使用 euclidean 距离进行语义聚类，不同长度的向量簇间距离失真，可能导致聚类质量下降。

---

### 2.7 Clustering：`batch_embed()` 部分失败 → zip 静默少更新

**状态**: ⚠️ P1 确认  
**证据**:
- `embeddings.py:250-251`：`batch_embed` 在第三次重试失败后 `return all_embeddings`—**可能返回部分结果**（少于输入条数）
- `cli.py:768-772`：Phase 4 embedding 更新中：
  ```python
  new_embeddings = _embed_fn(texts_to_embed)
  if new_embeddings:
      updates = [(emb, uid) for uid, emb in zip(id_order, new_embeddings)]
  ```
  — **如果 new_embeddings 长度 < id_order 长度，zip 静默截断**，部分 uid 未更新

**影响**: 部分富化后的 memory_units 的 embedding 未被更新，导致后续聚类基于过期向量。

---

### 2.8 Clustering：`memory_links ON CONFLICT` 索引一致性

**状态**: ✅ **索引与代码一致，无问题**  
**证据**:
- 代码 `database.py:358-366`：ON CONFLICT `(from_unit_id, to_unit_id, link_type, COALESCE(entity_id, '00000000-0000-0000-0000-000000000000'::uuid))`
- DB 索引：`CREATE UNIQUE INDEX idx_memory_links_hindsight ON memory_links USING btree (from_unit_id, to_unit_id, link_type, COALESCE(entity_id, '00000000-0000-0000-0000-000000000000'::uuid))`
- **完全匹配**

---

### 2.9 Clustering：`entities.mention_count += 1` 非幂等

**状态**: ⚠️ P1 确认  
**证据**:
- `database.py:212`：`ON CONFLICT ... DO UPDATE SET ..., mention_count = entities.mention_count + 1`
- 每次 apply 遇到相同 canonical_name 的实体，mention_count 都会 +1，即使实体信息没有实质变化

**影响**: 重复运行聚类分析会在 DB 中不断 inflate mention_count，影响 navigation 中的权重／排序逻辑。

---

### 2.10 Clustering：`dedup_minhash --apply` 物理删除安全

**状态**: ⚠️ P1 确认（需独立 gate）  
**证据**:
- `dedup_minhash.py:185-192`：`delete_loser()` 直接执行 `DELETE FROM memory_links ... DELETE FROM unit_entities ... DELETE FROM memory_units WHERE id = %s`
- `cli.py:246`：`dedup_memories` 命令直接 `UPDATE memory_units SET text = '[redundant]'` — 不是软删除但也不是级联删除

**问题**: `dedup_minhash --apply` 是硬删除，无回滚/软删除/备份机制。且 dedup 决策中的 `resolve_winner()`（行 170-182）仅根据长度+创建时间判断，无语义去重保障。

---

## 三、P0/P1/P2 问题汇总表

| ID | 优先级 | 模块 | 问题 | 证据文件/行号 | 影响 |
|----|--------|------|------|--------------|------|
| B-01 | **P0** | Builder | review_queue status 不一致：insert 用 `'pending'`，query 用 `'pending_review'` | database.py:166 vs database.py:537, review.py:60 | 所有 8 条待审项不可见（DB 已确认 pending=8, pending_review=0） |
| B-02 | P1 | Builder | `_write_to_db()` 跨多个隐式事务，非原子 | place.py:237, database.py:456/490 | 中断后产生 zombie nodes（缺 k_vector），DB 已确认大量存在 |
| B-03 | P1 | Builder | subject k_vector 缺失（21/54 无 k_vector），匹配失效 | place.py:94-99, DB count(with_vec)=33/54 | 持续创建"新科目"、匹配错误 |
| B-04 | P1 | Builder | `insert_article()` / `insert_point_text()` 无幂等保护 | database.py:71-79, 269-277 | 重复运行产生重复记录 |
| B-05 | P1 | Clustering | HDBSCAN 未 L2 normalize 输入向量 | clustering.py:494-499, cli.py:346 | Euclidean 距离失真，聚类质量下降 |
| B-06 | P1 | Clustering | `batch_embed` 部分失败后 zip 静默截断 | embeddings.py:250-251, cli.py:768-772 | embedding 更新静默少写 |
| B-07 | P1 | Clustering | `entities.mention_count += 1` 非幂等 | database.py:212 | 重复 runs 不断 inflate mention_count |
| B-08 | P1 | Clustering | `dedup_minhash --apply` 硬删除无回滚 | dedup_minhash.py:185-192 | 误删不可恢复 |
| B-09 | P2 | Builder | `create_tables()` 未创建 `retrieval_confidence` 列（但生产已存在） | database.py:302-376 vs DB schema | 首次部署新版时会失败 |
| B-10 | P2 | Builder | `backfill_k_vectors` 可能处理大量积压节点 | backfill_k_vectors.py:48-57, DB count | 生产积压 3200+ 节点需回填，分批处理耗时长 |
| B-11 | P2 | Clustering | test_conformance：3 个 `convert_llm_causal_pairs` 测试因置信度逻辑变更失败 | test_clustering.py:243,265,292 | 测试与实现不一致 |

---

## 四、DB / Schema 检查建议

### 4.1 已确认正常的
| 检查项 | 结果 |
|--------|------|
| pgvector 扩展 | ✅ 已安装 |
| vector 维度 | ✅ knowledge_tree.k_vector 为 VECTOR(1024)，与 bge-m3 一致 |
| memory_links 唯一索引 | ✅ `idx_memory_links_hindsight` 与 ON CONFLICT 完全匹配 |
| unit_entities 唯一索引 | ✅ `pk_unit_entities` on (unit_id, entity_id) 支持 ON CONFLICT |
| entities 唯一索引 | ✅ `idx_entities_bank_lower_name` on (bank_id, lower(canonical_name)) 与 ON CONFLICT 一致 |
| knowledge_tree_edges 唯一索引 | ✅ (from_node_id, to_node_id, relation_type) |
| source_articles 表 | ✅ 结构正常 |

### 4.2 建议修正
| 建议 | 原因 |
|------|------|
| 统一 review_queue status 为 `'pending'` 或 `'pending_review'` | 当前不一致导致查询全空 |
| `create_tables()` 补充 `retrieval_confidence FLOAT DEFAULT 1.0` | 新部署兼容 |
| 批量回填缺 k_vector 的节点（3247 个 knowledge_point + 21 个 subject 缺） | 恢复 subject 匹配能力 |
| 清理已标记 `[redundant]` 的记忆（如有） | 减少噪声 |
| `_write_to_db()` 改为整体事务或增加断点续写 | 防 zombie nodes |

### 4.3 Vector 完整性
```sql
-- 已执行结果
node_type       | total | with_vec
----------------|-------|---------
knowledge_point | 5649  | 2402    ← 3247 个节点缺 k_vector
subject         | 54    | 33      ← 21 个科目缺 k_vector
```

---

## 五、测试执行结果

### 5.1 Builder 测试
```bash
cd /mnt/d/HermesProject/scripts/knowledge-tree-builder
PYTHONPATH=src python3 -m pytest tests -q
```
**结果**: 265 passed (100%) ✅

### 5.2 Clustering 测试
```bash
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3
PYTHONPATH=src python3 -m pytest tests -q
```
**结果**: 68 passed, **3 failed** ❌

失败明细：
| 测试 | 原因 |
|------|------|
| `test_basic_conversion` | 期望 weight=0.85，实际返回 0.7（置信度解析逻辑变更） |
| `test_out_of_bounds_indices_skipped` | 因果触发词（CAUSAL_TRIGGERS）过滤掉了测试字符串 |
| `test_non_dict_pair_skipped` | 同上 |

均为 `convert_llm_causal_pairs` 的因果连词守卫（CAUSAL_TRIGGERS）新增后测试未同步更新。

### 5.3 Builder Dry-run 管线
```bash
cd /mnt/d/HermesProject/scripts/knowledge-tree-builder
PYTHONPATH=src python3 -m knowledge_tree_builder.cli run --input-dir test_articles --merged --dry-run
```
**结果**: 2 篇 → 19 条原子知识 → 准入通过 19 条 → 定位 19 条，2 个科目 ✅

---

## 六、验证命令（只读/Dry-run）

```bash
# Builder 单元测试
cd /mnt/d/HermesProject/scripts/knowledge-tree-builder
PYTHONPATH=src python3 -m pytest tests -q

# Clustering 单元测试
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3
PYTHONPATH=src python3 -m pytest tests -q

# Builder dry-run 全管线
cd /mnt/d/HermesProject/scripts/knowledge-tree-builder
PYTHONPATH=src python3 -m knowledge_tree_builder.cli run --input-dir test_articles --merged --dry-run

# 检查 review_queue status 不一致
psql "$CLUSTERING_DB_URL" -c "SELECT status, count(*) FROM knowledge_review_queue GROUP BY status;"

# 检查 k_vector 完整性
psql "$KT_DB_URL" -c "SELECT node_type, count(*) total, count(k_vector) with_vec FROM knowledge_tree GROUP BY node_type;"

# 检查 vector 扩展
psql "$KT_DB_URL" -c "SELECT extname FROM pg_extension WHERE extname='vector';"

# Dedup dry-run（安全预览）
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3
CLUSTERING_DB_URL="$CLUSTERING_DB_URL" python3 scripts/dedup_minhash.py --threshold 0.85 --limit 5000
```

---

## 七、总结

### 最紧急（P0）
1. **B-01**: review_queue status `pending` vs `pending_review` 不匹配 → 8 条现有记录全部不可见，`consolidate process-timeouts` 等命令全部失效

### 高风险（P1）
2. **B-02/B-03**: 原子性缺失 + k_vector 大量缺失（3247 个节点缺向量）是共生问题
3. **B-05**: HDBSCAN 未 normalize 影响聚类质量
4. **B-06**: batch_embed 部分失败导致更新静默丢失
5. **B-07**: mention_count 非幂等
6. **B-08**: dedup_minhash --apply 无软删除保护

### 低风险（P2）
7. **B-09**: create_tables 缺 retrieval_confidence 列
8. **B-11**: 3 个测试因逻辑变更未同步

### 建议修复顺序
1. review_queue status 统一 → 2. `_write_to_db()` 原子化 → 3. 回填 k_vector → 4. HDBSCAN normalize → 5. batch_embed 失败处理加固 → 6. mention_count 幂等 → 7. dedup gate → 8. 测试更新
