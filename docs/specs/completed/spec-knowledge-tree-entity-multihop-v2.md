# SPEC: 知识树实体多跳优化 — 方案 A 完整实施计划

**目标**：在知识树中自建实体关联表，实现跨 subject 的实体多跳，同时清理数据问题
**前置**：1af71ee（k_vector 写入加固 + 领域判断兜底），7088f4b（subject-based 多跳），3a01f4d（MD5 1024d）
**来源**：审计发现（空 subject / 文本脱节 / 命名不一致 / 大 subject 平铺）
**实施状态**: ✅ 已实施（核心改造全部落地）
> **实施证据**：
> - P2.1 `kt_entity_links` 表已创建（8 个文件引用）
> - P2.2 实体提取在 `phase/merged.py` LLM prompt 中实现（非 admit.py，spec 表述偏差）
> - P2.3 `place.py` `_write_to_db()` 已写入 `kt_entity_links`
> - P2.4 `public_api.py` `multi_hop_recall()` 已改为 entity-based 多跳（Route B）
> - P2.5 `hooks.py` 两路输出已实现
> - P2.6 `backfill_entities.py` 回填脚本已创建

---

## 一、总览：两大工作线

```
           ┌─────────────────────────────────┐
           │    预备工作：数据清理（不可跳过）      │
           │ 1.1 清理 14 个空 subject          │
           │ 1.2 查 KP 文本与标题一致性          │
           │ 1.3 subject 命名去重               │
           │ 1.4 domain="general" 检查          │
           └──────────────┬──────────────────┘
                          ▼
           ┌─────────────────────────────────┐
           │    核心改造：实体多跳             │
           │ 2.1 新建 schema：kt_entity_links  │
           │ 2.2 入库提取实体（Phase 3 改造）    │
           │ 2.3 实体写入 KT（Phase 4 改造）    │
           │ 2.4 实体多跳召回（public_api 改造） │
           │ 2.5 两路输出（hooks 改造）         │
           │ 2.6 历史数据回填（迁移脚本）        │
           └─────────────────────────────────┘
```

## 二、数据清理（预备工作）

### 任务 1.1：清理空 subject

14 个空 subject 只占索引位，无法提供检索价值。

**改动**：直接 SQL 删除

```sql
DELETE FROM knowledge_tree 
WHERE id IN (8492, 6658, 5034, 6645, 6642, 6654, 6916, 5049, 8519, 7251, 6799, 8486, 8498, 8512)
  AND node_type = 'subject';
```

**验证**：再次查询空 subject = 0

### 任务 1.2：检查 KP 文本与标题一致性

从取样看，部分 KP 的 name 和 text 内容不一致（标题截断 + 文本来源不同）。

**不改代码**，先做一轮统计摸底：

```sql
SELECT kt.id, kt.name, kpt.text
FROM knowledge_tree kt
JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id
WHERE kt.node_type = 'knowledge_point'
  AND length(kpt.text) > 10
  AND position(lower(substr(kt.name, 1, 10)) in lower(kpt.text)) = 0
ORDER BY random()
LIMIT 20;
```

如果大面积不一致，需要审计 Phase 2 (split.py) 和 Phase 3 (analyze.py) 的 chunk→summarize 管线。如果只是极少数 edge case，标记即可。

### 任务 1.3：subject 命名去重

当前存在 `context-engineering/root` + `context-engineering/root/root`、`agent/root/root` + 没有 `agent/root` 等不一致命名。

**不改代码**，先统计再人工判断。不做自动合并以避免误删。

### 任务 1.4：domain="general" 检查

跑 `redistribute` 命令确认 LLM 领域判断优化是否有效：

```bash
cd /root/.hermes/scripts/knowledge-tree-builder
source venv/bin/activate
python3 -m knowledge_tree_builder.cli redistribute --dry-run
```

如果 general 数量为 0 或极低，说明 1af71ee 的 prompt 优化有效。

---

## 三、核心改造：实体多跳（代码修改）

### 任务 2.1：新建数据库表 `kt_entity_links`

```
CREATE TABLE kt_entity_links (
    kp_id   integer NOT NULL REFERENCES knowledge_tree(id) ON DELETE CASCADE,
    entity  text NOT NULL,
    PRIMARY KEY (kp_id, entity)
);
CREATE INDEX idx_kte_entity ON kt_entity_links(entity);
CREATE INDEX idx_kte_kp_id ON kt_entity_links(kp_id);
```

**执行方式**：直接在 hindsight DB 执行。不是建树管线的一部分，所以不需要在 Phase 中自动创建。

### 任务 2.2：入库时提取实体（Phase 3 改造）

**文件**：`scripts/knowledge-tree-builder/src/knowledge_tree_builder/phase/admit.py`
（或 merged pipeline 中负责 LLM 知识提取的部分）

**现状**：Phase 3 调用 LLM 对每个 chunk 输出 `{type, knowledge_point, text, ...}`

**改动**：在 LLM prompt 中追加一个 `entities` 字段

```python
# 在现有 prompt 的 JSON output 示例中追加
"entities": ["实体A", "实体B", "实体C"]
```

**prompt 示例追加内容**：
```
额外输出要求：
- "entities"：从知识点中提取3-10个命名实体（名词性关键概念），
  中英文均可，每个实体2-6个字为宜。
  示例：{"entities": ["HDBSCAN", "余弦相似度", "聚类分析", "知识树"]}
```

**成本**：零额外 LLM 调用，只在现有输出 JSON schema 中多一个字段。每 chunk 多输出 ~20 tokens。

### 任务 2.3：实体写入知识树（Phase 4 改造）

**文件**：`scripts/knowledge-tree-builder/src/knowledge_tree_builder/place.py`

**现状**：`place_knowledge()` 读取 admitted_list（含 text/type/knowledge_point），调用 embed_fn 计算 k_vector，然后 `_write_to_db()` 写入 knowledge_tree + knowledge_point_texts。

**改动**：在 `_write_to_db()` 中新增实体写入步骤

```python
def _write_entities(cursor, kp_id: int, entities: list[str]):
    """批量 upsert kt_entity_links"""
    if not entities:
        return
    values = [(kp_id, e.strip()) for e in entities if e.strip()]
    if not values:
        return
    psycopg2.extras.execute_values(
        cursor,
        "INSERT INTO kt_entity_links (kp_id, entity) VALUES %s ON CONFLICT DO NOTHING",
        values,
    )
```

在 `_write_to_db()` 的 knowledge_point 写入之后调用：

```python
if atomic.get("entities"):
    _write_entities(cursor, kp_id, atomic["entities"])
```

**admitted_list schema 扩展**：Phase 3 返回的 dict 增加 `entities: list[str]` 字段。如果 field missing 或为 None，跳过写入（兼容历史数据）。

### 任务 2.4：实体多跳召回（public_api 改造）

**文件**：`plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/public_api.py`

**现状**：`multi_hop_recall()` subject-based（parent_id SQL）

**改后**：

```python
def multi_hop_recall(
    seed_kp_ids: list[int],
    cfg: PluginConfig | None = None,
    adapter: PluginDatabaseAdapter | None = None,
    top_k: int = 5,
    max_hops: int = 2,
) -> list[dict[str, Any]]:
    """实体多跳关联召回：从种子 KP 出发，沿共享实体跨 subject 展开。
    
    策略（参考 SAG multi 模式）：
      第 1 跳：种子 KPs → kt_entity_links → 实体名集合
      第 2 跳：实体名 → kt_entity_links → 其他 KPs（排除自身）
      排序：按共享实体数量降序（coarse rank）
    """
    ...
```

**SQL 逻辑**：

```sql
-- 第 1 跳：种子 KPs → 实体名
SELECT DISTINCT entity FROM kt_entity_links WHERE kp_id = ANY(%s);

-- 第 2 跳：实体名 → 关联 KPs（按共享实体数排序）
SELECT kt.id, kt.name, kpt.text, COUNT(kel.entity) as shared_entities
FROM kt_entity_links kel
JOIN knowledge_tree kt ON kt.id = kel.kp_id
JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id
WHERE kel.entity = ANY(%s)
  AND kt.id != ALL(%s)
  AND kt.node_type = 'knowledge_point'
GROUP BY kt.id, kt.name, kpt.text
ORDER BY shared_entities DESC
LIMIT %s;
```

**退化策略**：如果 `kt_entity_links` 为空（新部署/历史数据未回填），或只找到 0 条实体关联，退回当前的 subject-based 多跳。

### 任务 2.5：两路输出（hooks 改造）

**文件**：`plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py`

**现状**：参考 SAG 两路架构。

**改动**：

```python
# 现有代码已经做了第一步：multi_hop 结果合并到 kt_raw_results
# 需要改为两路输出：

# 在 format_context_lines() 或注入阶段，标注 source
knowledge_context = format_context_lines(kt_raw_results, mark_source=True)

# 输出格式：
# <知识树结果（向量匹配）>
# - point A...
# - point B...
# </知识树结果>
# <知识树结果（实体多跳展开）>
# - point C... (关联自: 实体A, 实体B)
# - point D... (关联自: 实体C)
# </知识树结果>
```

**关键**：向量结果和多跳结果用标签分隔，让 LLM 知道哪些是精确匹配、哪些是关联展开。

### 任务 2.6：历史数据回填实体

对 8023 个已存在的 KPs，需要一个一次性回填脚本。

**文件**：`scripts/knowledge-tree-builder/scripts/backfill_entities.py`

**逻辑**：

```python
def backfill_entities(batch_size=50):
    """对现有 KPs 中 kt_entity_links 为空的做 LLM 实体提取"""
    while True:
        kps = cursor.execute("""
            SELECT kt.id, kp.text
            FROM knowledge_tree kt
            JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id
            WHERE kt.node_type = 'knowledge_point'
              AND NOT EXISTS (
                SELECT 1 FROM kt_entity_links WHERE kp_id = kt.id
              )
            LIMIT %s
        """, (batch_size,)).fetchall()
        if not kps:
            break
        
        for kp_id, text in kps:
            entities = llm_extract_entities(text)  # 调用 LiteLLM
            if entities:
                write_entities(cursor, kp_id, entities)
        
        conn.commit()
```

**LLM 调用**：走 LiteLLM 网关（`http://127.0.0.1:4142/v1`），模型 `s-deepseek-v4-flash`，每 KB 文本约 50 tokens。8023 KPs 平均 <100 字符 → 约 8000 LLM 调用（按 batch 50 一批，约 160 批次）。

**预计耗时**：~30 分钟（每批 ~10 秒）

---

## 四、实施路线图（阶段 + 依赖）

```
第 1 阶段：数据清理（无代码改动，直接执行 SQL/命令）
  1.1 删空 subject      [10 min]
  1.2 查文本一致性       [30 min]
  1.3 检查命名           [15 min]
  1.4 run redistribute   [5 min]
  ─────────────────────────────────
  合计：~1h，零代码风险

第 2 阶段：建表 + 核心代码
  2.1 CREATE TABLE      [5 min]  ← 独立
  2.2 Phase 3 改 prompt  [30 min] ← 独立
  2.3 Phase 4 写实体     [30 min] ← 依赖 2.1
  2.4 public_api 多跳     [1h]     ← 依赖 2.1
  2.5 hooks 两路输出     [1h]     ← 依赖 2.4
  2.6 回填脚本           [1h]     ← 依赖 2.1+2.2
  ─────────────────────────────────
  合计：~4h，含 deploy + 验证

第 3 阶段：验证 + 回填
  2.6 跑回填脚本         [~30 min]
  查询测试（6 类）       [30 min]
  对比效果确认           [20 min]
```

## 五、不在此方案中的事

| 事项 | 原因 |
|------|------|
| 引入中文分词 (zhparser) | 实体匹配是 NER + 向量/名称匹配，不依赖分词 |
| 大 subject 加子科目细分 | 投入大（3-5 天），且实体多跳后跨 subject 关联可缓解 |
| 主题合并 (consolidate) | 独立流程，不受实体表影响 |
| SAG 替换 KT 检索层 | 方案 B，本方案选择方案 A |
| 修改 DB schema 已有 key 约束 | 只新建表，不改旧表 |
| 修改 CLI 参数接口 | 不涉及 |

## 六、验证方法

```
1. 建表后查询空 kt_entity_links — 应存在但为空
2. 新注入一篇文档（--phase merged），检查 kt_entity_links 有对应实体
3. 查询种子 KP 的实体：SELECT * FROM kt_entity_links WHERE kp_id = X
4. 跑回填后：SELECT count(*) FROM kt_entity_links — 应有 8000+ 行
5. 多跳测试：multi_hop_recall([KP_A]) 应返回共享实体的其他 KPs（跨 subject）
6. hooks 输出检查：trace.log 应看到 vector (N) + multi-hop (M) 的标注
```