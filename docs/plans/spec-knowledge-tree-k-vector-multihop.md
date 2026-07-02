# SPEC: 知识树管线加固 — k_vector 插入时写入 + 多跳关联检索

## 一、现状分析

### 1.1 backfill-k-vectors

Phase 4 (`place.py` `_write_to_db`) 已经写了 k_vector，但有没有写成功依赖 `batch_embed` 的返回。当 embedding API 失败返回 `None` 时，节点 k_vector IS NULL，需要靠 `backfill-k-vectors` 命令修复。

### 1.2 redistribute

`redistribute` 命令将 domain="general" 的知识点重新分配到正确的 domain。根因是 Phase 4 的领域判断用 LLM 一次性判断，当 LLM 无法确定时就写了 "general"，需要后期修。

### 1.3 多跳能力

目前知识树只有父子层级（domain → subject → knowledge_point），没有 SAG 那种 entity-event 多跳遍历。知识点之间已有的链接（因果链 `memory_links`、实体 `unit_entities`）没有在检索中利用。

### 1.4 中文分词

知识树使用 `cosine_similarity(k_vector, query_embedding)` 做语义搜索，不涉及全文 BM25，所以中文分词当前不是问题。但如果后续要把知识点文本也做全文搜索，就需要考虑 zhparser。

## 二、改造方案

### Step 1: Phase 4 k_vector 写入加固（消灭 backfill-k-vectors）

**文件**：`place.py`

**改动**：
1. `_write_to_db()` 中 `batch_embed` 失败时加重试（最多 3 次退避）
2. 重试仍然失败时，用 `knowledge_text` 前 512 字符的平均值填充（降级策略），确保 `k_vector` 不为 NULL
3. 移除 `backfill-k-vectors` 命令（保留函数，删除 CLI 入口）

### Step 2: Phase 4 领域判断优化（消灭 redistribute）

**文件**：`cli.py` (Phase 4 中的 `_llm_domain`)

**改动**：
1. 为 LLM 领域判断 prompt 增加更明确的指令：不理解时不是写 "general"，而是抽取标题中最有区分度的关键词作为领域名
2. 增加兜底规则：如果 LLM 返回 "general" 或空，则用文章标题的前两个词（中文/英文）作为 domain 名

**不删除** `redistribute` CLI 入口（保留作为手动修复工具），只是让它不再被日常需要。

### Step 3: 多跳关联检索

**文件**：`knowledge-navigation` 插件（`pre_llm_call` hook）

在现有向量召回之后，增加一步 SQL 多跳遍历：

```sql
-- 从向量召回到的 knowledge_point 出发，沿 unit_entities → entities 做两跳展开
SELECT kp.* FROM knowledge_points kp WHERE kp.id IN (
  SELECT ue1.unit_id FROM unit_entities ue1 WHERE ue1.entity_id IN (
    SELECT ue2.entity_id FROM unit_entities ue2 WHERE ue2.unit_id IN (初始召回到的 knowledge_point IDs)
  )
)
LIMIT 5;
```

结果与向量结果分路输出（参考 SAG 的两路合并模式）。

### Step 4: 中文分词评估

当前不引入。理由：
- 知识树检索只走向量（cosine similarity），不走全文 BM25
- 查 Headroom 时需要的是语义匹配，不是关键词匹配
- 如果后续需要全文搜索，再加 zhparser

## 三、涉及文件

| 文件 | 改动 |
|------|------|
| `cli.py` | Phase 4 的 LLM domain prompt 优化；删除 backfill CLI 入口 |
| `place.py` | `_write_to_db` 中 k_vector 写入加固 |
| `knowledge-navigation` 插件 | pre_llm_call 加多跳 SQL |

## 四、验证方法

1. 跑完整管线（`--phase all`），检查 `knowledge_tree.k_vector IS NULL` 条数 = 0
2. 跑 `redistribute`，检查 domain="general" 条数不变（说明 Phase 4 已经正确分配）
3. 调知识导航插件，检查次返回节点数 > 向量召回数（多跳生效）

## 五、不做的事

- 不引入中文分词
- 不改变数据库 schema
- 不改变 CLI 参数接口
- 不修改 consolidate 逻辑
