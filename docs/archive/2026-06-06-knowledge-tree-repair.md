# 知识树质量问题修复计划

> **文档状态：历史计划 / 归档**  
> 本文保留早期知识树修复思路，当前状态以源码、deploy 配置和后续审计文档为准。


> 日期: 2026-06-06
> 状态: 待审批
> 涉及项目: knowledge-tree-builder, knowledge-tree-plugin

## 问题概述

| # | 问题 | 严重度 | 影响范围 |
|---|------|--------|---------|
| 1 | k_vector 全部 NULL (1483/1483) | 🔴 | 语义搜索失效，去重失效，consolidation 跑不了 |
| 2 | 66% 知识点堆积在 general/root (976/1483) | 🔴 | 领域分类完全失效 |
| 3 | 无跨科链接 (0 edges) | 🔴 | 知识关联缺失 |
| 4 | 建议/意见型知识点混入 (73 条) | 🟡 | 知识纯度下降 |
| 5 | 树结构扁平，无子科目细分 | 🟡 | 检索精度有限 |

## 现有设施复用

`core/consolidation.py` 的 `ConsolidationEngine` 已实现：
- **科目拆分** (`_split_subject`)：子节点 > 50 时 HDBSCAN 聚类拆子科
- **跨科建边** (`check_merge`)：共现率 > 80% 自动 `insert_edge`
- **Confidence 衰减**：使用日志驱动升降权

这些模块只需要 k_vector 就绪 + use_log 有数据就能跑。本计划的任务就是补上这两个前提，然后跑一轮 consolidation 让它自动完成子科目细分和跨科建边。

## 任务分解（6 步）

---

### Step 1: 批量回填 k_vector

**文件**: `scripts/knowledge-tree-builder/src/knowledge_tree_builder/scripts/backfill_k_vectors.py`

**改动**:
新增独立脚本 + CLI 子命令 `knowledge-tree-builder backfill-k-vectors`：
1. 查出 `k_vector IS NULL` 的所有叶子节点
2. 读出 `knowledge_point_texts.text` 作为输入
3. 调用 `batch_embed` 批量计算 embedding（batch_size=20）
4. 调用 `update_k_vector(node_id, k_vector, placement_count=0)` 写入

**验证**:
```bash
knowledge-tree-builder backfill-k-vectors --dry-run  # 预览条数
knowledge-tree-builder backfill-k-vectors
psql -c "SELECT COUNT(*) FROM knowledge_tree WHERE k_vector IS NULL AND node_type='knowledge_point'"
# 预期: 0
```

---

### Step 2: 修复离线管线 `_write_to_db` 补写 k_vector

**文件**: `scripts/knowledge-tree-builder/src/knowledge_tree_builder/place.py`

**改动**:
`_write_to_db()` 在 `executemany` 插入 knowledge_point 后，拿到返回的 `node_ids`，对每个新节点调用 `adapter.update_k_vector(node_id, k_vector, placement_count=1)`。

**验证**:
```bash
knowledge-tree-builder run --input-dir ... --skip-existing
psql -c "SELECT COUNT(*) FROM knowledge_tree WHERE k_vector IS NULL AND node_type='knowledge_point'"
# 新插入的节点应有 k_vector
```

---

### Step 3: 重新分类 general/root 下 976 条

**文件**: `scripts/knowledge-tree-builder/src/knowledge_tree_builder/scripts/redistribute_general.py`

**改动**:
新增独立脚本 + CLI 子命令 `knowledge-tree-builder redistribute`。

3 级漏斗策略：

| 级别 | 方法 | 预期覆盖率 | API 成本 |
|------|------|-----------|---------|
| L1 | 关键词规则（"Atlas"→repowiki, "rtk-hermes"→runtime 等） | ~20% | 0 |
| L2 | 语义 cosine 匹配已有 domain centroid | ~50% | 1 次 batch_embed |
| L3 | LLM 批量判断残差（每组 20 条，temperature=0） | ~30% | ~15 次 LLM 调用 |

每个知识点迁移到正确的 `domain/subject` 节点（更新 `parent_id`）。

**验证**:
```bash
knowledge-tree-builder redistribute --dry-run  # 预览迁移计划
knowledge-tree-builder redistribute
psql -c "SELECT name, COUNT(*) FROM knowledge_tree WHERE node_type='subject' GROUP BY name"
# general 应降至合理范围
```

---

### Step 4: 验证在线插件增量放置路径

**文件**: `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/placement.py`

**改动**:
C1 修复后 `update_k_vector` 已自带 commit，本步只验证。检查 `_update_k_vector` 调用后 k_updated_at 时间戳正确更新。

**验证**:
```bash
# 手动触发一次 post_llm_call，查 PG 日志
psql -c "SELECT id, name, k_updated_at FROM knowledge_tree WHERE k_updated_at IS NOT NULL LIMIT 5"
```

---

### Step 5: 跑 Consolidation（自动完成子科目细分 + 跨科建边 + confidence）

**文件**: 无新增。复用已有 `cli.py` 的 `consolidate` 子命令。

**前提**: Step 1 已经回填 k_vector，Step 3 已经让节点归位。

**执行**:
```bash
knowledge-tree-builder consolidate run --dry-run  # 预览拆分和建边
knowledge-tree-builder consolidate run             # 执行
```

ConsolidationEngine 自动做三件事：
1. **科目拆分**（子节点 > 50 的 domain 做 HDBSCAN 聚类 → 拆子科）
2. **跨科建边**（共现率 > 80% 的科目对自动 `insert_edge`）
3. **Confidence 衰减**（初始化所有节点 confidence）

此步骤替代了原计划中独立的 `build_edges` 和 `tree_rebalance` 脚本。

**验证**:
```bash
psql -c "SELECT node_type, COUNT(*) FROM knowledge_tree GROUP BY node_type"
# 预期出现细分 subject

psql -c "SELECT COUNT(*) FROM knowledge_tree_edges"
# 预期 > 0
```

---

### Step 6: 增加建议/意见过滤规则

**文件**: `scripts/knowledge-tree-builder/src/knowledge_tree_builder/phase/admit.py`

**改动**:
在 `_guard_filter()` 中添加：
```python
_SUGGESTION_PATTERNS = [
    re.compile(r"^建议"),
    re.compile(r"^改进建议"),
    re.compile(r"^[Nn]ote[:：]"),
]
```

**验证**:
```python
def test_guard_filter_drops_suggestion(self):
    assert _guard_filter({"text": "建议增加日志记录", "type": "method"})[0] is False
```

---

## 执行顺序

```
Step 1 (回填 k_vector) ──→ Step 3 (重分类 general) ──→ Step 5 (跑 consolidation)
                                                          ├─ 子科目拆分
                                                          ├─ 跨科建边
                                                          └─ confidence 初始化
Step 2 (修复写入) ──→ Step 4 (验证插件)
Step 6 (过滤规则)  ← 独立，可与前 5 步并行
```

## 风险

| 风险 | 概率 | 缓解 |
|------|------|------|
| embedding API 限流 | 低 | batch_size=20，内置重试 3 次 |
| Step 3 LLM 重新分类不准 | 中 | 3 级漏斗 + dry-run 预览 |
| consolidation 拆科命名差 | 低 | 使用现有 consolidation 模块（已投产） |
