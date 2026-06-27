# 知识树实体多跳 + 数据清理 实施计划

**目标**：知识树自建实体关联表 + 实体多跳召回 + 数据清理
**架构**：Phase 3 入库时 LLM 提实体 → Phase 4 写入 `kt_entity_links` → public_api 实体多跳 → hooks 两路输出
**Tech Stack**：PostgreSQL, Hermes Plugin, knowledge-tree-builder
**总工作量**：~6h（数据清理 1h + 核心代码 4h + 回填验证 1h）
**执行方式**：串行执行，每步完成后验证再继续

---

### 阶段划分总览

```
数据清理 (P1)
  └── P1.1 删除空 subject
  └── P1.2 KP 文本一致性检查
  └── P1.3 subject 命名检查
  └── P1.4 redistribute

建表+核心代码 (P2)
  └── P2.1 CREATE TABLE kt_entity_links
  └── P2.2 Phase 3 prompt 改（admit.py）
  └── P2.3 Phase 4 写实体（place.py）
  ├── P2.4 backfill 脚本
  └── P2.5 public_api 实体多跳
  └── P2.6 hooks 两路输出

验证 (P3)
  └── P3.1 deploy + 多跳测试
```

---

## P1: 数据清理

### P1.1 删除 14 个空 subject

**文件**：无（直接 SQL）
**验证**：`SELECT count(*) FROM knowledge_tree WHERE node_type='subject' AND id NOT IN (SELECT parent_id FROM knowledge_tree WHERE parent_id IS NOT NULL);` → 0

### P1.2 KP 文本一致性检查

**文件**：无（SQL 采样检查）
**验证**：随机 20 条，检查 name 前 10 字是否出现在 text 中

### P1.3 subject 命名检查

**文件**：无（SQL 统计）
**验证**：列出所有含 `/root/root` 的 subject 命名

### P1.4 redistribute --dry-run

**命令**：`python3 -m knowledge_tree_builder.cli redistribute --dry-run`
**验证**：确认 domain="general" 数量

---

## P2: 建表 + 核心代码

### P2.1 创建 `kt_entity_links` 表

**SQL**：见下文
**验证**：`\d kt_entity_links`

### P2.2 Phase 3 — admit.py 追加 entities 字段

**文件**：`scripts/knowledge-tree-builder/src/knowledge_tree_builder/phase/admit.py`
**改动**：LLM prompt 追加 entities 输出
**验证**：`python3 -m py_compile`

### P2.3 Phase 4 — place.py 写实体

**文件**：`scripts/knowledge-tree-builder/src/knowledge_tree_builder/place.py`
**改动**：`_write_to_db()` 中 upsert 实体
**验证**：`python3 -m py_compile`

### P2.4 backfill 脚本

**文件**：`scripts/knowledge-tree-builder/scripts/backfill_entities.py`
**改动**：新建
**验证**：`python3 -m py_compile`

### P2.5 public_api.py — 实体多跳

**文件**：`plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/public_api.py`
**改动**：重写 `multi_hop_recall()` 为实体 SQL
**验证**：`python3 -m py_compile`

### P2.6 hooks.py — 两路输出

**文件**：`plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py`
**改动**：向量/多跳输出用标签分隔
**验证**：`python3 -m py_compile`

---

## P3: 验证

- P3.1 deploy + 多跳测试 SQL
