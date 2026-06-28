# Knowledge Tree Builder — 知识分域建树管线

从精读笔记/技术文章中自动提取知识点，按五分类建树入库。

## Quick Start

```bash
# 1. 初始化数据库（首次运行）
knowledge-tree-builder init-db

# 2. 从文章目录提取知识点并写入知识树
knowledge-tree-builder run \
  --input-dir ./articles \
  --merged

# 预览模式（不写入 PG）
knowledge-tree-builder run \
  --input-dir ./articles \
  --merged \
  --dry-run

# 3. 查看知识树
knowledge-tree-builder tree

# 4. 搜索知识点
knowledge-tree-builder find <关键词>
```

## 管线架构

```
[文章目录]
    │
    ▼
Pre-phase: 文件扫描
    │  排除 index.md、_bak/、二进制文件等
    ▼
Phase 1+2: 分析 + 拆解（合并模式，单次 LLM 调用）
    │  五分类知识提取 + 原子性检查 + 自解释校验
    │  ┌─ principle（原理）    ─ 因果/机制关系
    │  ├─ formula（公式）      ─ 可计算形式化表述
    │  ├─ key_point（要点）    ─ 事实/分类/结构
    │  ├─ conclusion（结论）   ─ 有条件对比
    │  └─ method（方法/流程）  ─ 可复现步骤/规范
    ▼
Phase 3: 准入 + 去重
    │  规则兜底拦截 → 两段式 cosine 去重 → 矛盾检测
    ▼
Phase 4: 树定位 → PG
    │  LLM 判断领域 → 科目匹配 → 写入知识树
    ▼
[知识树] ← 纠错回路（confidence 收敛 + review_queue）
```

## 安装

### 依赖

- Python 3.10+
- PostgreSQL (建议 14+)
- LLM API（兼容 OpenAI 格式）

### 部署

```bash
# 使用项目统一部署工具
./deploy/deploy.sh deploy knowledge-tree-builder --yes

# 设置环境变量
export LITELLM_MASTER_KEY=sk-xxx        # LLM API 密钥
export KT_DB_URL=postgresql://user@host:5432/dbname  # 数据库
```

## 详细用法

### 全量重建

首次运行或需要重建知识树时：

```bash
# 清空旧树 + 全量重建
psql $KT_DB_URL -c "TRUNCATE knowledge_tree CASCADE;"
knowledge-tree-builder run \
  --input-dir /path/to/articles \
  --merged
```

### 增量添加

已有知识树的基础上添加新文章：

```bash
knowledge-tree-builder run \
  --input-dir /path/to/new-articles \
  --merged
```

## 断点续传

管线支持三级断点续传：

| 层级 | 缓存文件 | 作用 |
|------|----------|------|
| L1 提取 | `.kb_manifest_<目录>.json` + `_atomics/` | Phase 1+2 逐篇进度，已提取跳过 LLM |
| L2 Embedding | `.kb_embed_cache.json` | embedding 结果缓存，续传免重算 |
| L3 树定位 | `.kb_phase4_<目录>.json` | Phase 4 逐篇领域判断和 placement 缓存 |

- **提取中断**：重跑自动检测清单，跳过已提取的，只处理待处理和失败的
- **写入中断**：atomics 已存盘，重跑跳过提取直接 admit + 写入
- **单篇失败**：跳过失败篇，继续处理后续，最后列出失败清单
- **树定位中断**：领域判断结果和 placement records 已缓存，重跑自动跳过已定位文章，最后批量写入 PG

```bash
# 首次运行
knowledge-tree-builder run --input-dir ./articles --merged

# 中断后续传（自动检测各级缓存）
knowledge-tree-builder run --input-dir ./articles --merged
```

### 仅预览（不写入）

```bash
knowledge-tree-builder run \
  --input-dir ./articles \
  --merged \
  --dry-run
```

### 分阶段运行

```bash
knowledge-tree-builder run --phase scan     # 只看扫描结果
knowledge-tree-builder run --phase admit    # 只跑准入去重
knowledge-tree-builder run --phase place    # 只跑树定位（需要 Phase 3 结果）
```

> 注意：`--phase place` 单独运行时需要先有 Phase 3 的产出（`_admit_result`），
> 否则会提示跳过。

### 独立模式（含 claims_count 校验）

不推荐，除非需要调试 claims_count 原子性检查：

```bash
knowledge-tree-builder run --input-dir ./articles  # 默认独立模式
```

## 审查队列

知识点管线运行中，以下情况会自动进入审查队列：

| 类型 | 来源 | 说明 |
|------|------|------|
| `contradiction` | 阶段3 | 条件相同 + 结论对立的知识 |
| `incomplete_split` | 阶段2 | 2 轮拆解后仍有残余 |
| `orphan` | 阶段4 | 无法归入现有树的知识点 |
| `consistency_warning` | 阶段2 | claims_count sum 校验异常 |

```bash
# 查看待审查项
knowledge-tree-builder review list

# 按类型查看
knowledge-tree-builder review list --type contradiction

# 接受/拒绝
knowledge-tree-builder review accept 42
knowledge-tree-builder review reject 42
```

## 维护命令

### 回填 k_vector

修复历史数据中缺失的 embedding 向量（k_vector 全部为 NULL）：

```bash
# 预览待回填数
knowledge-tree-builder backfill-k-vectors --dry-run

# 执行回填
knowledge-tree-builder backfill-k-vectors
```

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

# 仅处理超时的审查项
knowledge-tree-builder consolidate process-timeouts
```

`consolidate run` 自动执行（带 7 步骤进度标记，可精确定位卡住步骤）：

| 步骤 | 动作 | 条件 |
|------|------|------|
| 1/7 | 加载使用日志 | — |
| 2/7 | 加载 confidence 记录 | — |
| 3/7 | 批量更新 confidence | 有使用日志 |
| 4/7 | 碎片 domain 合并 | 子节点 < 配置阈值 |
| 5/7 | 子科目拆分（HDBSCAN 聚类，60s 超时保护） | 子节点 > 50 |
| 6/7 | 处理超时审查项 | review_queue 有超时 |
| 7/7 | 构建 KP 级关联边 | 默认开启 |

## 配置

默认配置文件 `config/default.yaml`：

```yaml
# LLM
llm_api_url: "http://127.0.0.1:4142/v1/chat/completions"
llm_model: "s-deepseek-v4-flash"

# 阶段1：知识点提取
max_candidates_per_article: 15        # 每篇候选上限 (K)
article_max_chars: 12000              # 文章截断长度

# 阶段2：拆解与校验
split_max_rounds: 2                   # 拆解轮数上限
self_explanatory_rules: true          # 启用自解释检查

# 阶段3：准入与去重
dedup_threshold_direct: 0.95          # 直接判重阈值
dedup_threshold_llm: 0.90             # LLM 确认区间下界
conflict_threshold: 0.80              # 矛盾检测阈值

# 阶段4：树定位
subject_match_threshold: 0.70         # 科目匹配阈值
```

## 项目结构

```
knowledge-tree-builder/
├── src/knowledge_tree_builder/
│   ├── cli.py                     # CLI 入口
│   ├── config.py                  # 配置加载
│   ├── models.py                  # 共享数据结构（AtomicKnowledge 含 source_title）
│   ├── manifest.py                # 批处理清单 + 断点续传
│   ├── place.py                   # 阶段4：树定位 + 批量写入（含 k_vector 补写）
├── scripts/
│   ├── backfill_k_vectors.py  # k_vector 批量回填（CLI: backfill-k-vectors）
│   └── redistribute_general.py# 领域重分类（CLI: redistribute）
│   ├── phase/
│   │   ├── scan.py                # Pre-phase：文件扫描 + YAML 前置元数据跳过
│   │   ├── analyze.py             # 阶段1：分析（独立模式）
│   │   ├── merged.py              # 阶段1+2：合并模式（推荐）
│   │   ├── split.py               # 阶段2：拆解（独立模式）
│   │   └── admit.py               # 阶段3：准入去重
│   ├── consolidate/
│   │   ├── confidence.py          # confidence 衰减计算
│   │   └── review.py              # review_queue 操作
│   ├── adapters/database.py       # PG 适配器
│   ├── llm/client.py              # LLM API 调用
│   └── core/
│       ├── embeddings.py          # Embedding API（带 None 占位防御）
│       └── incremental.py         # 增量去重
├── config/default.yaml            # 默认配置
├── deploy/                        # 部署脚本
├── tests/                         # 测试
└── skills/                        # Hermes Skill 文件
```

## 迁移说明

旧管线（HDBSCAN 聚类）已废弃。新管线使用领域匹配替代聚类，质量保障更强：

| 维度 | 旧管线 | 新管线 |
|------|--------|--------|
| 知识点类型 | 无分类 | 五分类（原理/公式/要点/结论/方法）|
| 提取量 | 3-8 条/篇 | K=15 条/篇 |
| 原子性 | 无检查 | claims_count + sum 校验 |
| 自解释 | 无 | 代词/元引用/省略 三项检查 |
| 去重 | embedding 单阈值 | 两段式 + LLM 确认 + 矛盾检测 |
| 树结构 | HDBSCAN 聚类 | 领域→科目→知识点 层级树 |
| 纠错 | 无 | confidence 收敛 + review_queue |

旧 CLI 命令（`extract`/`cluster`/`validate`/`name`/`write`/`report`）已标记废弃，将重定向到新管线。
