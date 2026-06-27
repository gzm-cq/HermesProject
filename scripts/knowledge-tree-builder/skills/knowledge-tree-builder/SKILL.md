---
name: knowledge-tree-builder
description: 知识分域建树管线 — 从文章自动提取知识点、五分类、原子性拆解、准入去重、领域树定位、纠错回路
version: 0.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [knowledge-tree, knowledge-point, extraction, clustering, nlp, pipeline]
    related_skills: [deep-research, audit-methodology, explore]
    categories: [knowledge-management, data-processing]
---

# Knowledge Tree Builder Skill

将文章（精读笔记、审计报告、技术文档）自动转化为结构化的知识树。
分四阶段：知识点提取与拆解 → 准入去重 → 树定位 → 纠错回路。

## 前置条件

- LLM 服务运行中（`http://127.0.0.1:4142`）
- PG 数据库可访问（`localhost:5434/hindsight`）
- 环境变量 `LITELLM_MASTER_KEY` 已设置
- 环境变量 `KT_DB_URL` 已设置（默认 `postgresql://postgres@127.0.0.1:5434/hindsight`）

## 快速使用

```bash
# 初始化数据库（首次运行）
knowledge-tree-builder init-db

# 完整管线：提取 → 拆解 → 准入 → 树定位 → 写入 PG
knowledge-tree-builder run \
  --input-dir /path/to/articles \
  --merged

# 预览模式
knowledge-tree-builder run \
  --input-dir /path/to/articles \
  --merged \
  --dry-run
```

## 五类知识点

系统自动从文章中提取以下五类知识点：

| 类型 | 定义 | 示例 |
|------|------|------|
| principle | 因果/机制关系 | "Q/K 分离使 query 不进 K 空间" |
| formula | 可计算形式化表述 | "attention = softmax(Q×K^T/√d)" |
| key_point | 事实/分类/结构 | "自进化Agent 分为三大范式" |
| conclusion | 有条件对比 | "HDBSCAN 在非均匀密度上优于 DBSCAN" |
| method | 可复现步骤/规范 | "部署流程分三步：备份→同步→验证" |

## 管线阶段

```
文件系统 → Pre-phase 扫描排除 → 阶段1 分析提取（+ claims_count）
  → 阶段2 拆解校验（原子性 + 自解释 + sum 校验）
  → 阶段3 准入去重（兜底拦截 + 两段式去重 + 矛盾检测）
  → 阶段4 树定位（领域匹配 + 科目匹配 + 写入 PG）
  → 纠错回路（confidence 收敛 + review_queue）
```

### Pre-phase：输入扫描

扫描输入目录，排除 `index.md`、`moc.md`、`_bak/`、二进制文件、空文件。

### 阶段1+2（合并模式，推荐）

单次 LLM 调用完成分析 + 拆解，跳过 claims_count 校验，节省约 80% tokens。

```bash
knowledge-tree-builder run --input-dir <目录> --merged
```

### 阶段3：准入去重

- **兜底拦截**：长度 < 10 字、元信息开头、提取失败信号 → 丢弃
- **两段式去重**：cosine > 0.95 直接判重；0.90~0.95 LLM 确认
- **矛盾检测**：条件相同 + 结论对立 → 入 review_queue

### 阶段4：树定位

LLM 判断文章所属领域，有则入、无则新建。知识点只存一份，跨域引用。

## 并发提取

默认串行处理（一篇等一篇）。多篇文章可用 `-j` 并发加速：

```bash
# 3 路并发（推荐）
knowledge-tree-builder run --input-dir <目录> --merged -j 3

# 5 路并发（注意 API 限流）
knowledge-tree-builder run --input-dir <目录> --merged -j 5
```

并发数不影响提取质量，只影响吞吐。建议起始 `-j 3`。

## 断点续传

管线支持三级断点续传：

| 层级 | 缓存文件 | 作用 |
|------|----------|------|
| L1 提取 | `.kb_manifest_<目录>.json` + `_atomics/` | Phase 1+2 逐篇进度 |
| L2 Embedding | `.kb_embed_cache.json` | embedding 结果缓存 |
| L3 树定位 | `.kb_phase4_<目录>.json` | Phase 4 领域 + placement 缓存 |

- 提取中断 → 重跑自动续传，跳过已提取的
- 写入中断 → atomics 已存盘，重跑直接 admit + 写入
- 树定位中断 → 领域判断和 placement records 已缓存，重跑自动跳过已定位文章
- 单篇失败 → 跳过继续，最后列出失败清单

## 审查队列

```bash
# 查看待处理项
knowledge-tree-builder review list

# 按类型筛选
knowledge-tree-builder review list --type contradiction

# 接受/拒绝
knowledge-tree-builder review accept <id>
knowledge-tree-builder review reject <id>
```

## 维护命令

### 回填 k_vector

修复历史数据中缺失的 embedding 向量（如 k_vector 全部为 NULL 时）。注意：只需要回填 `knowledge_point.k_vector`；`subject.k_vector` 是可选/派生字段，召回和 consolidation 会从子 knowledge_point 实时计算 centroid，不要浪费 embedding API 回填 subject。

```bash
# 预览待回填数
knowledge-tree-builder backfill-k-vectors --dry-run

# 执行回填
knowledge-tree-builder backfill-k-vectors
```

### k_vector 维护 cron

低频兜底脚本：

```bash
/root/.hermes/scripts/knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh
```

默认行为：
- 只统计 `knowledge_point` 缺失量，忽略 `subject` 缺失量
- `K_VECTOR_BACKFILL_THRESHOLD` 默认 100；低于阈值静默退出
- 高于阈值才执行幂等 `backfill-k-vectors`
- 设置 `K_VECTOR_BACKFILL_DRY_RUN=1` 可强制只预览，不写 DB
- 使用 `/tmp/knowledge-tree-kvector-maintenance.lock` 防并发运行

### 领域重分类

将错误归入 `general` 的知识点重新分配到正确领域。
3 级漏斗：关键词规则 → 语义 cosine → LLM 批量判断：

```bash
# 预览迁移计划
knowledge-tree-builder redistribute --dry-run

# 执行迁移
knowledge-tree-builder redistribute
```

## 纠错回路（一键维护）

单条命令自动完成 3 件事：

```bash
# 默认开启全部： domain 合并 + confidence 更新 + 子科目拆分
knowledge-tree-builder consolidate run

# 仅做 confidence 更新（跳过 domain 合并）
knowledge-tree-builder consolidate run --no-merge-domains

# 处理超时审查项
knowledge-tree-builder consolidate process-timeouts
```

`consolidate run` 自动执行：

| 阶段 | 动作 | 条件 |
|------|------|------|
| 1 | 碎片 domain 合并 | 子节点 < 5 的 domain 合并到最近的大 domain |
| 2 | 子科目拆分（HDBSCAN聚类） | 子节点 > 50 |
| 3 | confidence 更新 | 有使用日志 |
| 4 | 跨科建边 | 共现率 > 80% |
| 5 | 超时审查项处理 | review_queue 有超时 |

## 查看知识树

```bash
knowledge-tree-builder tree
knowledge-tree-builder find <关键词>
```

## Pipelne 输出格式

阶段间通过 JSON 产物传递：

```
admitted_files.json → analysis_report.json → atomic_knowledge_list.json
→ admitted_knowledge_list.json → tree_insertion_records.json → PG
```

## 实现模块

| 模块 | 职责 |
|------|------|
| `phase/scan.py` | 文件扫描与过滤 |
| `phase/merged.py` | 分析+拆解合并（推荐路径） |
| `phase/analyze.py` | 独立分析（非合并模式） |
| `phase/split.py` | 独立拆解（非合并模式） |
| `phase/admit.py` | 准入去重 + 矛盾检测 |
| `place.py` | 树定位 + PG 写入（含 k_vector 补写） |
| `scripts/backfill_k_vectors.py` | k_vector 批量回填（CLI: backfill-k-vectors） |
| `scripts/redistribute_general.py` | 领域重分类（CLI: redistribute） |
| `consolidate/confidence.py` | confidence 衰减 |
| `consolidate/review.py` | review_queue 操作 |
| `adapters/database.py` | PG 适配器 |
| `models.py` | 共享数据结构 |
