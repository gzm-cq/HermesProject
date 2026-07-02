# 知识树管线改造 — 开发方案

> 基于 `knowledge-point-definition.md` v2.3 设计文档  
> 目标：从当前 HDBSCAN 聚类管线迁移到五分类 + claims_count + 准入去重 + 领域树定位管线  
> 预计工期：6 周（Phase A-C 核心管线，Phase D 纠错闭环）

---

## 一、现状盘点

### 保留文件（不改或微调）

| 文件 | 用途 | 改造方式 |
|------|------|---------|
| `llm/client.py` | LLM API 调用 | 不改 |
| `adapters/database.py` | PG 读写 | 新增 review_queue 表操作，其余不改 |
| `core/embeddings.py` | embedding + cosine 计算 | 不改 |
| `incremental.py` | 增量去重 + 矛盾检测 | 保留，新 `phase/admit.py` 调用其函数 |
| `config.py` | 配置加载 | 新增字段，不改现有逻辑 |

### 废弃文件（改造后不再使用）

| 文件 | 原因 |
|------|------|
| `core/clustering.py` | HDBSCAN 聚类被领域→科目匹配替代 |
| `core/validator.py` | 结构校验被阶段2 质量评估替代 |
| `core/namer.py` | LLM 命名被领域规则的科目创建替代 |

### 替换文件（保留壳，重写核心逻辑）

| 文件 | 改为 | 原因 |
|------|------|------|
| `core/extractor.py` | `phase/analyze.py` | 从 3-8 条无分类 → 五分类 + claims_count 结构化输出 |
| `core/admission.py` | `phase/admit.py` | 从简单规则过滤 → 兜底拦截 + 两段去重 + 矛盾检测 |
| `core/writer.py` | `place.py` | 从 DFS 树写入 → 领域匹配 + 科目匹配 + 树定位 |
| `core/consolidation.py` | `consolidate/` 包 | 从 HDBSCAN 拆分 → confidence 收敛 + review_queue |

---

## 二、新增文件清单

```
knowledge-tree-builder/src/knowledge_tree_builder/
├── models.py                          # [新] 共享类型、KNOWLEDGE_TYPES 枚举、claims_count 校验函数
├── phase/
│   ├── scan.py                        # [新] Pre-phase：文件扫描 + 过滤
│   ├── analyze.py                     # [新] 阶段1：五分类 + claims_count + 结构化输出
│   ├── split.py                       # [新] 阶段2：原子性检查 + 规则修正 + sum 校验 + 自解释
│   └── admit.py                       # [新] 阶段3：兜底拦截 + 两段去重 + 矛盾检测
├── place.py                           # [新] 阶段4：领域匹配 + 科目匹配 + 树写入
└── consolidate/
    ├── confidence.py                  # [新] confidence 衰减计算 + 阈值判定
    └── review.py                      # [新] review_queue CLI 操作（list/accept/reject）

tests/
├── test_phase_scan.py                 # [新]
├── test_phase_analyze.py              # [新]
├── test_phase_split.py                # [新]
├── test_phase_admit.py                # [新]
├── test_place.py                      # [新]
└── consolidate/
    ├── test_confidence.py             # [新]
    └── test_review.py                 # [新]
```

---

## 三、分阶段实施

### Phase A（第 1-2 周）— 核心质量

**目标**: 替换提取管线，实现五分类 + claims_count + 原子性检查 + 自解释  
**产出**: 可跑通的 `scan → analyze → split` 三阶段

#### A1. models.py — 共享数据结构

```python
# 知识点类型枚举
class KnowledgeType(Enum):
    PRINCIPLE = "principle"       # 原理
    FORMULA = "formula"           # 公式
    KEY_POINT = "key_point"       # 要点
    CONCLUSION = "conclusion"     # 结论
    METHOD = "method"             # 方法/流程

# claims_count 规则修正
def adjust_claims_count(text: str, llm_claimed: int) -> int
    - 连词检测 → max(llm_claimed, 2)
    - 分号检测 → max(llm_claimed, 分号数 + 1)

# 各阶段 JSON 产物的 TypedDict
class AdmittedFile(TypedDict) ...
class Candidate(TypedDict) ...
class AtomicKnowledge(TypedDict) ...
```

**测试**: `KNOWLEDGE_TYPES` 枚举完整性、`adjust_claims_count` 边界情况

#### A2. phase/scan.py — Pre-phase

从 CLI `_scan_articles` 提取文件扫描逻辑，增加排除规则：

```python
def scan_input_dir(input_dir: str) -> AdmittedFiles
    - 排除 index.md / moc.md / _bak/ / _archive/ / *.log
    - 排除空文件、二进制文件
    - 返回 admitted_files + skipped + empty_dir
```

**依赖**: 无（纯文件操作）  
**测试**: 空目录、混合文件、全部被排除

#### A3. phase/analyze.py — 阶段1

替换当前 `extractor.py`，LLM prompt 改为结构化输出：

```python
def analyze_article(article_text: str, title: str) -> AnalysisReport
    - LLM 调用，prompt 要求输出：
      {content_summary, candidates: [{text, type, claims_count, claim_list}]}
    - 不超过 K=15 条
    - 返回 AnalysisReport
```

**Prompt 核心结构**:
```
从文章中提取知识点，每个知识点属于以下五类之一：
【原理】因果/机制关系 → X通过Y实现Z
【公式】可计算的形式化表述 → attention = softmax(QK^T/√d)
【要点】事实/分类/结构 → X分为A/B/C三类
【结论】有条件对比 → X在条件下优于Y
【方法/流程】可复现步骤 → 部署分三步：A→B→C

对每个候选：列出包含的独立 claim（每行 claim: xxx），再输出 claims_count。
不超过 15 条。
```

**依赖**: `models.py`, `llm/client.py`  
**测试**: mock LLM 返回不同格式，验证解析鲁棒性

#### A4. phase/split.py — 阶段2

```python
def process_candidates(report: AnalysisReport) -> list[AtomicKnowledge]
    对每个 candidate:
      1. 规则级 claims_count 修正
      2. claims_count == 1 → 质量检查（格式/类型/自解释）→ 通过则保留
      3. claims_count > 1 → LLM 拆解（上限 2 轮）
         - 每轮后 sum 校验
         - 2 轮后残余 → review_queue（incomplete_split）
```

**自解释三项检查**:
```python
def check_self_explanatory(text: str) -> bool
    - 不含指代代词（该模型、这种方法、上述算法）
    - 不含元引用（如上所述、下文详述）
    - 不含依赖原文的省略或文章自创缩写
```

**依赖**: `models.py`, `llm/client.py`  
**测试**: 已原子候选直接通过、非原子候选拆解、2 轮未拆完入 review_queue、自解释拒绝

#### A5. config.yaml 新增字段

```yaml
# 新增
max_candidates_per_article: 15       # K
split_max_rounds: 2                  # R_max
self_explanatory_rules: true         # 启用自解释检查

# 废弃（旧 HDBSCAN 参数）
# min_cluster_size: 5                # 不再使用
# cluster_selection_method: "eom"    # 不再使用
# max_subcluster_depth: 5            # 不再使用
```

---

### Phase B（第 3 周）— 去重增强

**目标**: 替换准入逻辑，实现两段去重 + 矛盾检测  
**产出**: `scan → analyze → split → admit` 四阶段

#### B1. phase/admit.py — 阶段3

替换当前 `admission.py`，扩展为完整准入 + 去重 + 矛盾检测：

```python
def admit_knowledge(
    atomic_list: list[AtomicKnowledge],
    existing_vectors: list[dict],    # 库中已有的 k_vector
    embed_fn,
) -> AdmittedKnowledgeList
    1. 兜底拦截（类型/长度/提取失败/元信息开头）
    2. 两段去重：
       - cosine > 0.95 → 判重跳过
       - 0.90~0.95  → LLM 批量确认（5对/批）
       - < 0.90 → 通过
    3. 矛盾检测：
       - cosine > 0.80 + 条件相同 + 结论对立 → review_queue（contradiction）
       - 防重叠：已入矛盾的不再去重
    4. 冷启动：< 50 条时退化为纯文本去重
```

**依赖**: `models.py`, `embeddings.py`, `incremental.py`（复用 `dedup_before_insert`、`detect_conflict`）  
**测试**: 直接判重、LLM确认判重、矛盾检测条件对比、防重叠规则、冷启动退化

---

### Phase C（第 4-5 周）— 树定位 + 纠错

**目标**: 替换树写入逻辑，实现领域匹配 + 科目匹配 + review_queue 基础设施  
**产出**: 完整 4 阶段管线 + 基础纠错能力

#### C1. place.py — 阶段4

替换当前 `writer.py`：

```python
def place_knowledge(
    admitted_list: AdmittedKnowledgeList,
    domain_rules: list[DomainRule],
    existing_tree: TreeStructure,
    embed_fn,
    llm_fn,
) -> TreeInsertionRecords
    1. 领域匹配：先查规则表，未命中降级 LLM
    2. 科目匹配：cosine > 0.7 匹配已有科目，否则创建
    3. 冷启动：< 3 篇时直接建根科目
    4. 跨领域：只存一份，引用链接关联
    5. 写入 PG：每条知识带 quality_confidence + k_vector
```

**依赖**: `models.py`, `domain_rules.yaml`, `embeddings.py`, `adapters/database.py`  
**测试**: 规则表命中、LLM 降级、冷启动建科目、跨域去重

#### C2. domain_rules.yaml — 领域映射规则表

```yaml
# 初始至少 8 条，上线前补到 20+
rules:
  - keywords: [聚类, HDBSCAN, DBSCAN, 簇, 密度, 层次聚类]
    domain: mlops/clustering
  - keywords: [attention, transformer, 注意力, Q/K, 自注意力, 多头]
    domain: deep-learning/attention
  - keywords: [RAG, 记忆, memory, 召回, 检索, 向量库]
    domain: memory-system/rag
  - keywords: [评估, benchmark, 对比, 指标, 准确率, F1, 评测]
    domain: evaluation/benchmark
  - keywords: [自进化, self-evolution, Reflexion, Voyager, ExpeL, Agent Lightning]
    domain: self-evolution
  - keywords: [agent, 智能体, 工具调用, function calling, ReAct]
    domain: agent-architecture
  - keywords: [嵌入, embedding, 向量化, bge, text-embedding]
    domain: mlops/embeddings
  - keywords: [部署, deploy, 上线, 容器, docker, kubernetes]
    domain: operations/deployment
```

#### C3. consolidate/confidence.py

```python
def update_confidence(
    knowledge_id: int,
    recalled: bool,
    clicked: bool,
    user_negated: bool,
    days_since_last_event: float,
) -> float
    - 点击 → confidence = min(1.0, confidence + 0.05)
    - 召回未点击 → confidence × 0.99^days
    - 未召回 → confidence × 0.997^days
    - 用户否定 → confidence × 0.5
    - 阈值判断 → return (new_confidence, action)
```

**依赖**: `models.py`, `adapters/database.py`（读日志、写 confidence）  
**测试**: 衰减计算、阈值边界（0.3/0.5/0.95/0.1）、累积触发

#### C4. consolidate/review.py

```python
def list_reviews(review_type: str | None) -> list[ReviewItem]
def accept_review(review_id: int) -> None
def reject_review(review_id: int) -> None
def postpone_review(review_id: int) -> None
```

**依赖**: `models.py`, `adapters/database.py`（review_queue 表）  
**测试**: 状态机转换（pending → accept/reject）、超时默认动作

---

## 四、数据库变更

### 新增 review_queue 表

```sql
CREATE TABLE review_queue (
    id SERIAL PRIMARY KEY,
    type VARCHAR(32) NOT NULL,          -- consistency_warning | contradiction | orphan | obsolete | move_suggestion | incomplete_split
    target_knowledge_id INTEGER REFERENCES knowledge_tree(id),
    new_text TEXT,                        -- 矛盾/拆分残余的文本
    existing_text TEXT,                   -- 矛盾的现有点
    similarity FLOAT,
    condition_same BOOLEAN,
    status VARCHAR(16) DEFAULT 'pending_review',  -- pending_review | accepted | rejected | timeout
    created_at TIMESTAMP DEFAULT NOW(),
    timeout_at TIMESTAMP,                 -- 超时后执行默认动作
    resolved_at TIMESTAMP
);
CREATE INDEX idx_review_queue_status ON review_queue(status);
CREATE INDEX idx_review_queue_type ON review_queue(type);
```

### knowledge_tree 表变更

```sql
-- 新增字段
ALTER TABLE knowledge_tree ADD COLUMN quality_confidence FLOAT DEFAULT 0.85;  -- 初始质量信心
ALTER TABLE knowledge_tree ADD COLUMN retrieval_confidence FLOAT DEFAULT 1.0;  -- 动态检索信心

-- 弃用字段（HDBSCAN 相关，暂时保留）
-- placement_delta, k_vector_change, 等聚类字段不再写入
```

### knowledge_use_log 表（已有，确认结构）

```sql
-- 确认已有: knowledge_id, query, recalled, clicked, user_feedback, timestamp
-- 12.3 confidence 计算依赖此表
```

---

## 五、CLI 命令变更

### 新增命令

```bash
# 分阶段运行
knowledge-tree-builder run                  # 全流程（scan → analyze → split → admit → place）
knowledge-tree-builder run --phase scan     # 只跑 Pre-phase
knowledge-tree-builder run --phase analyze  # 只跑阶段1（依赖 scan 产物）
knowledge-tree-builder run --phase split    # 只跑阶段2（依赖 analyze 产物）
knowledge-tree-builder run --phase admit    # 只跑阶段3（依赖 split 产物）
knowledge-tree-builder run --phase place    # 只跑阶段4（依赖 admit 产物）

# 审查队列
knowledge-tree-builder review list [--type contradiction|orphan|move_suggestion]
knowledge-tree-builder review accept <id>
knowledge-tree-builder review reject <id>

# 迁移辅助
knowledge-tree-builder preflight            # 确认源文件可访问
knowledge-tree-builder diff                 # 新旧树对比
```

### 保留命令

```bash
knowledge-tree-builder ingest               # 单文件管线（适配新逻辑）
knowledge-tree-builder find <query>         # 语义搜索（适配 k_vector）
knowledge-tree-builder tree                 # 查看树结构（适配新 schema）
knowledge-tree-builder add                  # 增量添加知识点（适配新 pipeline）
knowledge-tree-builder edit <id>            # 修正（保留）
knowledge-tree-builder remove <id>          # 删除（保留）
knowledge-tree-builder merge <keep> <del>   # 合并（保留）
knowledge-tree-builder move <id> --to <n>   # 移动（保留）
```

### 废弃命令

```bash
# cluster 命令 → 删除（HDBSCAN 聚类不再使用）
# report 命令 → 删除（建树报告由 admit 产出替代）
# validate 命令 → 删除（结构校验由 split 内置）
```

---

## 六、测试计划

| 模块 | 测试策略 | 预估用例数 |
|------|---------|-----------|
| `models.py` | 纯逻辑，单元测试 | 10 |
| `phase/scan.py` | mock 文件系统，`tmp_path` fixture | 8 |
| `phase/analyze.py` | mock LLM 返回各种 JSON 格式 | 12 |
| `phase/split.py` | mock LLM，规则修正逻辑单独测 | 15 |
| `phase/admit.py` | mock embedding + LLM，集成 `incremental.py` | 15 |
| `place.py` | mock embedding + LLM + DB | 12 |
| `consolidate/confidence.py` | 纯逻辑，单元测试 | 10 |
| `consolidate/review.py` | mock DB，状态机测试 | 8 |
| `config.py` | 新增字段加载测试 | 4 |
| CLI | `CliRunner` mock 各 phase | 10 |

**累计**: ~104 个测试用例，目标覆盖率 80%+

---

## 七、依赖关系与关键路径

```
Phase A ──────────────────────┐
  A1 models.py                │
  A2 phase/scan.py            │
  A3 phase/analyze.py ────┐   │
  A4 phase/split.py  ─────┤   │
                          │   │
Phase B ──────────────────┤   │
  B1 phase/admit.py       │   │
                          │   │
Phase C ──────────────────┼───┘
  C1 place.py             │
  C2 domain_rules.yaml    │
  C3 consolidate/conf.py  │
  C4 consolidate/review.py│
```

**关键路径**: A1 → A3 → A4 → B1 → C1（核心管线 5 个模块，串联依赖）  
**并行任务**: A2（独立）、C2（独立）、C3+C4（依赖于 A4，但不阻塞管线）

---

## 八、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LLM 五分类输出不稳定 | 中 | 高 | Phase A 先 mock 测试解析逻辑，上线前人工校准 prompt |
| 规则级 claims_count 低估隐式并列 | 低 | 中 | 规则只向上修正不降低，宁高勿低 |
| domain_rules.yaml 覆盖率不足 | 中 | 中 | 上线前扫描历史文章关键词分布，补到 20+ 规则 |
| review_queue 表迁移冲突 | 低 | 低 | 全量重建时清空旧表，ID 变更后旧数据可丢 |
| 旧 CLI 命令与新 phase 命令冲突 | 低 | 中 | `cluster`/`report`/`validate` 命令标记 deprecation 后删除 |
