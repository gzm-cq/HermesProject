# 第2层：数据反馈层 — 详细设计

> **文档版本**: v2.1  
> **创建时间**: 2026-05-30  
> **更新说明**: v2.0 → v2.1 修复"聚类组不影响 recall"错误——实体的确影响实体图遍历路径；恢复聚类→实体→recall 关联说明。  
> **状态**: 设计完成，待实施  
> **作者**: Hermes Agent  
> **参考来源**: 第1层三大算子实现 · 实际环境验证分析 (2026-05-30) · clustering-analysis-v3 管线（最新版） · Hindsight 三路检索机制

---

## 一、设计目标

第2层（数据反馈层）的核心目标是：**让 Hindsight 记忆库从"被动存储"变为"主动进化"**。

Hindsight 当前的四大事实约束：

| 约束 | 现状 | 对设计的影响 |
|:---|:---|:---|
| `access_count` 始终为 0 | Hindsight 不追踪使用频率 | 不能按"使用频率"做权重或遗忘 |
| `tags` 始终为空 | `hindsight_retain` 不写 tags | 不能按 tags 筛选或优先 |
| 无 weight 字段 | `memory_units` 表无 weight 列 | 权重只能通过 `memory_links.weight` + recall 后重排实现 |
| 无内置遗忘 | 没有 TTL/retention/prune 机制 | 删除只能走 SQL DELETE |

在这些约束下，第2层的四大模块：

| 模块 | 定位 | 输入 | 输出 |
|:---:|:---|:---|:---|
| **失败模式库** | 失败记录的唯一事实来源 | Revision 诊断结果 | `evolution.failure_records` 表 |
| **因果链增强** | 补充记忆间的因果关联 | 失败模式库高频记录 | `hindsight.memory_links` (causes/caused_by) |
| **权重注入** | recall 后多信号融合重排 | Layer 1 算子质量信号 | 注入 top-K 到 user message |
| **遗忘清理** | 低价值记忆手动清理 | 时间阈值 + 替代记录 | SQL DELETE |

---

## 二、三层架构中的定位

```
┌──────────────────────────────────────────────────────────────┐
│                    三层进化闭环                               │
├──────────────────────────────────────────────────────────────┤
│  第 3 层（调度层）：Kanban 任务板                             │
│  作用：根据 worker 表现动态调整任务分配                       │
│  接收第2层：worker_performance_profile                       │
├──────────────────────────────────────────────────────────────┤
│  第 2 层（数据反馈层）：Hindsight 记忆库 ← 本文档            │
│  作用：记录失败模式 → 增强因果链 → 按质量重排 recall → 清理  │
│  接收第1层：revision_output (诊断结果)                       │
│  输出：failure_pattern_db → causal_links → recall_weights    │
├──────────────────────────────────────────────────────────────┤
│  第 1 层（工具层）：三大进化算子                             │
│  作用：Revision / Recombination / Refinement 算子            │
│  输出：诊断结果、替代方案、风险评分                           │
└──────────────────────────────────────────────────────────────┘
```

### Hindsight recall 与第2层的关系

```
当前 recall 管线（Hindsight 原生）：
┌──────────┐   ┌──────────┐   ┌──────────┐
│ 语义搜索 │   │  实体图  │   │  因果链  │ ← 当前仅 0.44% 的数据
│embedding │   │ entities │   │ causes   │
│cosine    │   │JOIN 展开 │   │caused_by │
└────┬─────┘   └────┬─────┘   └────┬─────┘
     └──────┬───────┴───────┬──────┘
            ▼
       RRF 融合
            ▼
    Cross-encoder 精排
            ▼
       rerank_score  + trace
            ▼
    pre_llm_call 插件（知识导航）
            ▼
     多信号融合重排 ← 第2层扩展点
            ▼
     注入 top-K → user message
```

**三条路径当前状态**（2026-05-30 实测）：

| 路径 | 状态 | 数据量 | 占比 |
|:---:|:---|:---:|:---:|
| 语义搜索 | ✅ 完全可用 | 24,865 条全部有 embedding | 100% |
| 实体图展开 | ✅ 完全可用 | 7,876 实体, 59,011 关联 | 实体图已成熟 |
| 因果链遍历 | ⚠️ 数据稀疏 | 5,085 条 | 0.44% |

第2层要做的是：**补充因果链数据**（从失败模式库派生），让因果链那一路不再是空跑。

---

## 三、一次对话的全链路逻辑

本节展示 Phase 1 全部就绪后，一次典型对话从用户发消息到后台闭环的完整流程。

### 3.1 阶段说明

| 阶段 | 覆盖范围 | 能否独立运行 |
|:---:|:---|:---:|
| **当前** | 第2层未接入，只有 pre_llm_call 基本 recall | ✅ 当前状态 |
| **Phase 0** | 基础设施就绪（建表 + 日志扩展 + 聚类调整） | ⚠️ 表空运行 |
| **Phase 1** | 全链路就绪（标记 + 重排 + 第1层联动 + 因果链） | ✅ 全部可用 |

以下为 Phase 1 后的完整流程。

### 3.2 对话流程

```
用户：""帮我看看这个 bug""
   │
   ╞══ 步骤 1：pre_llm_call 插件（每次 LLM 调用前自动执行）
   ║   │
   ║   ├── 1a. recall Hindsight（已有）
   ║   │      调用 daemon /memories/recall → 返回 results[]
   ║   │
   ║   ├── 1b. 日志记录 recalled_ids（Phase 0 新增）
   ║   │      extra.recalled_ids = [r.id for r in raw_results[:20]]
   ║   │      改造量：1 行代码
   ║   │
   ║   ├── 1c. 剔除被标记的记忆（Phase 1 新增）
   ║   │      for r in results:
   ║   │          if "[标记:" in r.text: continue
   ║   │      纯字符串匹配，零 I/O，~0.1ms
   ║   │
   ║   ├── 1d. 多信号融合评分（Phase 1 新增）
   ║   │      score = 0.6 × rerank_score
   ║   │             + 0.2 × quality_score   ← 查 failure_records 频率
   ║   │             + 0.2 × causal_score    ← 查 memory_links 权重
   ║   │      一次 PG 查询，~5ms
   ║   │
   ║   └── 1e. 取 top 3 注入（Phase 1 改为固定 3 条）
   ║          kept = filter_by_score(..., max_results=3)
   ║
   ├── 2. LLM 处理用户消息 + 注入的 3 条记忆
   │
   ├── 3. 工具调用过程中出现失败
   │
   ╞══ 步骤 4：捕获失败轨迹（Phase 1 新增）
   ║   │
   ║   ├── 4a. 失败来源
   ║   │      不限于特定的触发点。任何场景下出现以下情况都触发：
   ║   │      ├── 工具调用失败（invalid_tool_call, argument_mismatch, ...）
   ║   │      ├── 执行结果与预期不符（response_mismatch）
   ║   │      ├── 状态不一致（state_mismatch）
   ║   │      ├── 对话中用户指出的逻辑错误
   ║   │      ├── Kanban worker 执行失败
   ║   │      └── 任何 finish_reason != "stop" 的 LLM 响应
   ║   │
   ║   ├── 4b. 收集失败上下文
   ║   │      failed_content = 导致失败的代码/参数
   ║   │      context = 任务描述 + 约束
   ║   │      trajectory_id = 当前轨迹 ID
   ║   │
   ║   ├── 4c. 调 Revision 算子
   ║   │      revision = RevisionOperator()
   ║   │      result = revision.execute(failed_content, context)
   ║   │
   ║   ╞══ 步骤 5：Revision 算子内部（Phase 1 新增）
   ║   ║   │
   ║   ║   ├── 5a. 调 LLM（单次 API 调用）
   ║   ║   │      prompt = 失败内容 + 上下文 → LLM 输出 JSON
   ║   ║   │      {"failure_type": "..", "direct_cause": "..",
   ║   ║   │       "root_cause": "..", "direct_fix": "..",
   ║   ║   │       "orthogonal_fix": "..", "conservative_fix": "..",
   ║   ║   │       "confidence": 0.85}
   ║   ║   │      一次 API 调用，~3-8s
   ║   ║   │
   ║   ║   ├── 5b. 写入 failure_records（第2层写入）
   ║   ║   │      FailurePatternDB().record(FailureRecord(
   ║   ║   │          failure_type, signature, task_type,
   ║   ║   │          direct_cause, root_cause, confidence,
   ║   ║   │          mitigation_strategy, trajectory_id, ...
   ║   ║   │      ))
   ║   ║   │      ~3ms
   ║   ║   │
   ║   ║   └── 5c. 可疑标记反推（形成闭环）
   ║   ║          conn = get_db_connection()
   ║   ║          # 查 pre_llm_call 日志 → recalled_ids
   ║   ║          for id in recalled_ids:
   ║   ║              if has_mark(text): continue
   ║   ║              if overlap(root_cause, text) > 0.7:
   ║   ║                  mark_memory(id, "可疑", "Layer 1 反推")
   ║   ║          ~5ms
   ║   │
   ║   ├── 4c. 返回修正方案给用户
   ║   │      "我发现了问题：[direct_cause]。提供了 3 个修复方案..."
   ║   │
   ║   └── 4d. 用户确认修正方案 → 执行
   │
   │   ← 对话当前回合结束，LLM 返回最终回复 →
   │
   ╞══ 步骤 6：后台定时任务（不阻塞对话）
   ║
   ║   └── generate_causal_links.py（定时，如每 6h）
   ║           │
   ║           ├── 查 evolution.failure_records
   ║           │    WHERE frequency >= 3
   ║           │    AND last_seen > now() - 30d
   ║           │
   ║           ├── 规则 A：同 task_type 同 failure_type 高频 → causes
   ║           ├── 规则 B：同 trajectory 连续不同 failure → causes
   ║           └── 规则 C：fix_success=True → resolves
   ║
   ║           → INSERT INTO hindsight.memory_links (causes/caused_by)
   ║              ON CONFLICT weight = GREATEST（已有幂等机制）
   ║
   ║   ← memory_links 更新后，下一次 recall 自动使用因果链 →
   │
   └── 后续对话：用户发新消息
           │
           ├── 之前的错误记忆 → 已被排除（[标记: 可疑]）
           ├── 失败模式 → 已写入 failure_records
           ├── 因果链 → 已补充到 memory_links
           └── 同类型错误 → 下次 recall 有更高相关度
```

### 3.3 每步的延迟预算

| 步骤 | 动作 | 归属 | 延迟 | 用户感知 |
|:---:|:---|:---:|:---:|:---:|
| 1a | recall Hindsight | pre_llm_call 插件 | ~2-3s（已有） | 等待 LLM 时已并行 |
| 1b | 日志加 recalled_ids | pre_llm_call 插件 | ~0ms（内存操作） | 无感知 |
| 1c | 剔除标记记忆 | pre_llm_call 插件 | ~0.1ms | 无感知 |
| 1d | 多信号融合 | pre_llm_call 插件 | ~5ms（1 次 PG 查询） | 无感知 |
| 1e | top 3 注入 | pre_llm_call 插件 | ~0ms | 无感知 |
| 2 | LLM 处理上下文 | Hermes 主流程 | ~3-10s（API 延迟） | 正常等待 |
| 3 | 工具失败 | Hermes 主流程 | 正常 | 正常 |
| 4-5 | Revision + 写入 + 反推 | 失败捕获点触发 | ~3-8s（1 次 LLM API） | 用户等待修复方案 |
| 6 | 因果链后台写入 | 定时脚本 | 不阻塞 | 看不到 |

**关键结论**：对用户的所有感知延迟来自 1a（recall，已存在）和 4-5（Revision LLM，等待修复方案是合理的）。其他步骤都在毫秒级，用户无感知。

### 3.4 与当前流程的差异对比

| 步骤 | 当前 | Phase 1 后 | 增量 |
|:---:|:---|:---|:---:|
| 1a | recall Hindsight | 不变 | 0 |
| 1b | 日志有 total_results | 加 recalled_ids 列表 | +1 行代码 |
| 1c | 不剔除 | 剔除含 `[标记:` 的记忆 | +10 行代码 |
| 1d | 单信号 | 多信号融合 | +30 行代码 |
| 1e | 动态 top-K | 固定 top 3 | +1 个参数 |
| 3-4 | 失败由用户手修 | 自动调 Revision | + 新链路 |
| 5 | 无写入 | 写入 failure_records | + 新链路 |
| 6 | 无后台 | 定时因果链生成 | + 新脚本 |

## 四、失败模式库（Failure Pattern DB）

### 4.1 核心思路

**替代关键词因果链**。不靠正则匹配从文本猜"谁导致了谁"，而是记录真实执行的失败事件，然后从这些记录中派生因果关系。

```
当前聚类脚本的因果链（关键词正则）：
  聚类 → 组内 n² 扫描 → detect_causal_pairs() 正则匹配
  → 假阳性高（已验证结论：80% 是假阳性）
  → 无法验证

失败模式库方案：
  Revision 算子执行 → 诊断结果 → 写入 failure_records
                          ↓
  定期查询：相同 task_type 的相同 failure_type frequency ≥ 3
                          ↓
  → 写入 memory_links (causes/caused_by)
  → 后续执行可验证因果是否成立（fix_success 字段）
```

### 4.2 表结构

在 shared-postgres 中新建独立 schema：

```sql
CREATE SCHEMA IF NOT EXISTS evolution;

CREATE TABLE evolution.failure_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- 失败标识
    failure_type TEXT NOT NULL,      -- FailureType 枚举值
    signature TEXT NOT NULL,         -- 失败特征签名（函数名 + 错误消息摘要）
    task_type TEXT NOT NULL,         -- 任务类型（"code_review", "document_audit" 等）
    task_context TEXT,               -- 任务上下文摘要
    
    -- 失败内容
    failed_content TEXT,             -- 导致失败的内容（截断至 1000 字符）
    error_message TEXT,              -- 错误消息
    tool_name TEXT,                  -- 涉及的工具名（若适用）
    
    -- 诊断结果（来自 Revision 算子）
    direct_cause TEXT,               -- 直接原因
    root_cause TEXT,                 -- 根本原因
    diagnosis_confidence FLOAT,      -- 诊断置信度 [0,1]
    
    -- 修复信息
    mitigation_strategy TEXT,        -- 缓解策略
    fix_type TEXT,                   -- 修复类型
    fix_success BOOLEAN DEFAULT NULL, -- 修复是否成功（后续验证后更新）
    
    -- 统计
    frequency INT DEFAULT 1,         -- 出现次数
    first_seen TIMESTAMPTZ DEFAULT now(),
    last_seen TIMESTAMPTZ DEFAULT now(),
    
    -- 元数据
    source_trajectory_id TEXT,       -- 来源轨迹 ID
    source_session_id TEXT,          -- 来源对话 ID
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_failure_task_type ON evolution.failure_records (task_type);
CREATE INDEX idx_failure_type ON evolution.failure_records (failure_type);
CREATE INDEX idx_frequency ON evolution.failure_records (frequency DESC);
CREATE INDEX idx_last_seen ON evolution.failure_records (last_seen DESC);
```

### 4.3 与 Revision 算子的集成

在 `revision.py` 的 `execute()` 方法末尾集成：

```python
# 第2层集成：Revision 完成时写入失败记录
if hasattr(self, 'layer2_integration') and self.layer2_integration:
    db = FailurePatternDB()
    db.record(FailureRecord(
        failure_type=diagnosis.failure_type.value,
        signature=self._extract_signature(failed_content, diagnosis),
        task_type=context.get("task_type", "general"),
        ...
    ))
```

写入延迟预期：< 5ms（单条 INSERT，有索引）。对算子执行时间无实质影响。

### 4.4 CRUD 接口

```python
class FailurePatternDB:
    def record(self, record: FailureRecord) -> UUID
    def query(self, task_type, failure_type, min_frequency, limit) -> List[FailureRecord]
    def get_high_frequency(self, threshold=3) -> List[FailureRecord]
    def mark_fixed(self, record_id, success=True) -> None
```

---

## 五、因果链增强

### 5.1 替代方案

**废弃聚类脚本中的 `_detect_causal_in_group()` 关键词正则**，改为从 `failure_records` 派生因果链。

```
规则 A：同一 task_type 的相同 failure_type 反复出现 ≥ 3 次
  → task_type → causes → failure_type
  → weight = min(1.0, 0.5 + frequency × 0.15)
  → 写入 memory_links

规则 B：同一 trajectory 中连续出现不同 failure_type
  → failure_A → causes → failure_B（按时间序）
  → weight = 0.7

规则 C：fix_success = True 的记录
  → mitigation_strategy → resolves → failure_type
  → weight = 0.9
```

### 5.2 与聚类脚本的关系

聚类脚本 `clustering-analysis-v3` 当前功能划分：

| 功能 | 状态 | 与第2层的关系 |
|:---|:---:|:---|
| DBSCAN 聚类 | ✅ 保留 | 聚类仍是实体提取的前置步骤 |
| 实体提取→写入 entities+unit_entities | ✅ 保留 | 影响实体图遍历路径 |
| 文本回写 `[聚类实体:]` | ✅ 保留 | 增强语义检索命中率 |
| 文本富化 `[因果来源/结果:]` | ✅ 保留 | 增强因果链文本的语义检索 |
| `_detect_causal_in_group()` 关键词正则 | ❌ **移除** | 由 failure_records 派生方案替代 |

**不修改聚类脚本代码**，而是在聚类完成后 **外部调用** `FailurePatternDB` 生成因果链并写入 `memory_links`。

### 5.3 实施建议

1. **保留**聚类脚本已写入的 ~5,085 条因果链（不删）
2. **从聚类脚本移除** `_detect_causal_in_group()` 的调用
3. **新增** `generate_causal_links.py` 脚本，定期从 `failure_records` 生成新因果链
4. 人工确认后写入 `memory_links`（ON CONFLICT weight = GREATEST 的幂等机制已就绪）

### 5.4 因果链与实体图的关系

| 路径 | 数据来源 | 写入方式 | 作用 |
|:---|:---|:---:|:---|
| 实体图 | 聚类脚本 → entities + unit_entities | 批量写入 | 按实体展开关联记忆 |
| 因果链 | failure_records → memory_links | 连续积累 | 按因果关系关联记忆 |

两者独立不冲突。当前实体图已成熟（7,876 实体），因果链需要第2层补充。

---

## 六、权重注入（pre_llm_call 扩展）

### 6.1 架构

在已有知识导航插件（`pre_llm_call`）中扩展，不新增 Hook：

```
当前插件流程：
  recall Hindsight → 提取 rerank_score → MIN_SCORE 过滤 → 注入 top-K

扩展后（第2层增强）：
  recall Hindsight → 提取 rerank_score
                    → 直查 PG evolution.failure_records（质量信号）
                    → 直查 PG hindsight.memory_links（因果链权重）
                    → 多信号融合评分（w_rerank × 0.6 + w_quality × 0.2 + w_causal × 0.2）
                    → MIN_SCORE 过滤 → 注入 top-K
```

### 6.2 多信号融合评分

```python
def calculate_final_score(recall_text, rerank_score, query, conn,
                          created_at=None):
    """
    多信号融合评分（含时态衰减）。
    
    四个信号：
    1. rerank_score — Hindsight cross-encoder 评分（来源：recall trace）
    2. quality_score — 该记忆关联的失败记录质量（来源：failure_records）
    3. causal_score — 该记忆的因果链权重（来源：memory_links）
    4. time_score — 时态衰减（来源：memory_units.created_at）
    """
    w_rerank = 0.5      # cross-encoder
    w_quality = 0.15    # 失败记录质量信号
    w_causal = 0.15     # 因果链关联
    w_time = 0.20       # 时态衰减信号 ← 新增
    
    import math
    from datetime import datetime, timezone
    
    # 信号 2：查询该记忆所属 task_type 的失败记录频率
    quality_score = 0.5
    cur = conn.cursor()
    cur.execute("""
        SELECT AVG(f.frequency)::float
        FROM evolution.failure_records f
        WHERE f.task_type LIKE %s
          AND f.last_seen > now() - INTERVAL '30 days'
    """, (f"%{query[:30]}%",))
    row = cur.fetchone()
    if row and row[0]:
        quality_score = min(1.0, row[0] / 10.0)
    
    # 信号 3：查询该记忆的因果链权重
    causal_score = 0.5
    unit_id = recall_text.get("id")
    if unit_id:
        cur.execute("""
            SELECT AVG(ml.weight)::float
            FROM hindsight.memory_links ml
            WHERE ml.link_type IN ('causes', 'caused_by')
              AND (ml.from_unit_id = %s OR ml.to_unit_id = %s)
        """, (unit_id, unit_id))
        row = cur.fetchone()
        if row and row[0]:
            causal_score = row[0]
    
    # 信号 4：时态衰减（新增）
    # 基于 created_at 的指数衰减
    # 30 天内 = 1.0, 90 天 = 0.5, 180 天 = 0.2
    # λ = 0.008（半衰期约 87 天）
    time_score = 1.0
    if created_at:
        days = (datetime.now(timezone.utc) - created_at).days
        time_score = math.exp(-0.008 * days)
    
    cur.close()
    return (w_rerank * rerank_score 
            + w_quality * quality_score 
            + w_causal * causal_score
            + w_time * time_score)

# time_score 确保近期记忆比半年前的记忆更高权重
# 但不会完全排除旧记忆——即使 1 年前的记忆 time_score=0.05，
# 如果 rerank_score=0.9，最终分仍可达 0.45，仍可进入 top 3
```

**权重合理性**：rerank 权重从 0.6 降到 0.5，将 0.2 分给 time_score。原因：Mem0 的基准测试显示时态感知是提升最大的单维度（+29.6 分），但 Hindsight 当前完全忽略时间。0.5/0.15/0.15/0.20 的比例确保旧的高质量记忆不会消失，但近期的同质量记忆更有优势。

---

## 七、记忆标记（文本标记方案，替代物理删除）

### 7.1 核心思路

**在 `memory_units.text` 末尾追加标记字符串，不改表结构，不新建外部表。**

与聚类脚本追加 `[聚类实体: ...]` 和 `[因果来源：...]` 完全相同的模式——都是文本富化。

```
标记（写入时）：
  UPDATE memory_units SET text = text || '\n[标记: 错误] 正确信息：...'
  └── 不改表结构，不新建 schema
  └── 幂等保护：f"[标记:" in text → 跳过

排除（召回时，pre_llm_call 插件中）：
  1. recall Hindsight
  2. 文本关键词匹配 "[标记:" → 剔除被标记的记忆
  3. 取 rerank_score 最高的 3 条
  4. 注入上下文

双重防线：
  第一道：pre_llm_call 插件关键词过滤
  第二道：即使漏了（如 Hermes 直接调用了 hindsight_recall 而非走插件），
          LLM 看到文本中的 "[标记: 错误]" 也知道这条内容不可信
```

### 7.2 标记类型

| 标记类型 | 文本格式 | 触发方式 | 说明 |
|:---:|:---|:---|:---|
| **错误** | `[标记: 错误] 正确信息：...` | 人工确认后写入 | 内容有误，附正确信息 |
| **作废** | `[标记: 作废] 替代记忆：<id>` | 被新记忆替代后自动标记 | 有更新版本 |
| **可疑** | `[标记: 可疑] 可能误导任务执行` | 第1层失败反推自动标记 | Revision 失败时反推标记 |
| **已解决** | `[标记: 已解决] 修复方案：...` | 失败模式修复成功后标记 | 失败记忆保留但标记为已解决 |
| **待验证** | `[标记: 待验证]` | 新写入记忆自动标记 | 首次使用后确认价值才转为可信 |

**待验证标记说明**：`hindsight_retain` 写入的数据默认没有质量校验。新记忆标记为 `[待验证]` 后，在 pre_llm_call 中不会被排除（仍参与 recall），但多信号融合时 quality_score 降权 0.5。定时脚本检查：如果 created_at > 30 天且从未被 recall 命中 → 转为 `[可疑]`；如果被 recall ≥ 3 次 → 自动移除 `[待验证]` 标记。

### 7.3 标记写入逻辑

```python
# 所有标记操作走同一个函数，统一幂等保护

MARK_PREFIXES = {"[标记: 错误]", "[标记: 作废]", "[标记: 可疑]", "[标记: 已解决]", "[标记: 待验证]"}

def has_mark(text: str) -> bool:
    """检查是否已有标记（幂等保护）。"""
    return any(p in text for p in MARK_PREFIXES)

def mark_memory(unit_id: str, mark_type: str, note: str, conn):
    """标记一条记忆，幂等。"""
    cur = conn.cursor()
    cur.execute("SELECT text FROM memory_units WHERE id = %s", (unit_id,))
    row = cur.fetchone()
    if not row:
        return
    
    text = row[0]
    if has_mark(text):   # 已有标记，跳过
        return
    
    new_text = text + f"\n[标记: {mark_type}] {note}"
    cur.execute("UPDATE memory_units SET text = %s WHERE id = %s", (new_text, unit_id))
    conn.commit()
    cur.close()


def unmark_memory(unit_id: str, conn):
    """去除标记（可逆恢复）。"""
    cur = conn.cursor()
    cur.execute("SELECT text FROM memory_units WHERE id = %s", (unit_id,))
    row = cur.fetchone()
    if not row:
        return
    
    text = row[0]
    if not has_mark(text):
        return
    
    import re
    cleaned = re.sub(r'\n\[标记: [^\]]+\] [^\n]*', '', text)
    cur.execute("UPDATE memory_units SET text = %s WHERE id = %s", (cleaned, unit_id))
    conn.commit()
    cur.close()
```

### 7.4 排除逻辑（pre_llm_call 插件扩展）

```python
# pre_llm_call 中，recall 之后、注入之前
# 不需要连数据库——直接做字符串匹配

def exclude_marked(results: List[dict]) -> List[dict]:
    """从 recall 结果中剔除被标记的记忆。"""
    filtered = []
    for r in results:
        text = r.get("text") or ""
        if "[标记:" in text:      # 关键词匹配，无需查库
            continue
        filtered.append(r)
    return filtered


def pre_llm_call(session_id, user_message, **kwargs) -> str | None:
    # 1. recall（已有）
    client = HindsightClient()
    result = client.recall(user_message)
    
    # 2. 提取 rerank 分数（已有）
    rerank_map = extract_rerank_scores(result.get("trace", {}))
    
    # 3. 剔除被标记的记忆（新增，纯字符串匹配，零 I/O）
    cleaned = exclude_marked(result.get("results", []))
    
    # 4. 取 top 3 注入
    kept = filter_by_score(
        cleaned,
        rerank_map,
        min_score=CONFIG.min_score,
        max_results=3,  # 固定 3 条
    )
    
    # 5. 注入
    context_lines = format_context_lines(kept, CONFIG.max_text_length)
    return "\n".join(context_lines) if context_lines else None
```

### 7.5 与现有文本富化的关系

| 追加内容 | 用途 | 写入方式 | embedding 影响 |
|:---|:---|:---|---:|
| `[聚类实体: ...]` | 语义搜索可命中实体名 | 聚类脚本 | 1024 维中包含实体信息 |
| `[因果来源：...]` | 因果链文本可供语义检索 | 聚类脚本 | 包含因果关系关键词 |
| `[标记: 错误/作废/可疑]` | 标记有问题的记忆 | 第2层标记机制 | 标记文本被编码，LLM 可读 |

三者都是追加到 `memory_units.text`，**完全相同的模式**。标记文本虽然会被 embedding，但因为硅基流动 bge-m3 支持 8192 tokens，一小段标记对整体质量影响忽略不计。

### 7.6 与第1层的联动：失败轨迹反推

```
第1层 Revision 算子执行失败
    ↓
诊断出 failure_type + root_cause
    ↓
第2层查询：这次任务 recall 了哪些记忆
    ↓
这些记忆中是否有与失败 root_cause 相关的内容
    ↓
是 → UPDATE memory_units SET text = text || '\n[标记: 可疑] ...'
     → 下次 recall 关键词匹配剔除
     → 不再踩同一个坑
否 → 写入 failure_records（与标记无关）
```

```python
# 在 Revision 算子 execute() 末尾

def _try_mark_suspicious_memory(self, failure, recall_results, conn):
    """失败时反推是否有可疑记忆导致错误。"""
    root_cause = failure.root_cause.lower()
    
    for r in recall_results:
        text = (r.get("text") or "").lower()
        if has_mark(text):
            continue  # 已标记过，跳过
        
        if self._semantic_overlap(root_cause, text) > 0.7:
            mark_memory(
                r["id"],
                "可疑",
                f"Layer 1 失败反推: {failure.root_cause[:200]}",
                conn,
            )
```

### 7.8 四种触发方式与可行性

#### 7.8.1 错误标记（手动）

| 维度 | 值 |
|:---|:---|
| 自动程度 | **手动** — 人工发现错误后触发 |
| 触发条件 | 用户/Agent 发现某条记忆内容与事实矛盾 |
| 触发时机 | 随时，发现即标记 |
| 可行性 | **✅ 完全可行，零依赖** |
| 实现 | `mark_memory(unit_id, "错误", "正确信息：...")` |

**触发场景**：
- 文档审计发现记忆与事实矛盾
- 聚类组内一致性校验发现偏差（语义相似度低于聚类组平均值 0.5 个标准差）
- 人工巡检时直接定位

---

#### 7.8.2 可疑标记（自动 — 第1层失败反推）

| 维度 | 值 |
|:---|:---|
| 自动程度 | **全自动** — Revision 算子执行失败时触发 |
| 触发条件 | 第1层 Revision 算子输出 `failure_type` + `root_cause` |
| 触发时机 | Revision 算子 `execute()` 执行完毕时 |
| 可行性 | **⚠️ 需先补齐两个前置条件** |

**前置条件**：

| 条件 | 要求 | 当前状态 |
|:---|:---|---:|
| ① Revision 算子接入 LLM | 产生真实的 `root_cause` 而非占位符 | ❌ **骨架**（`# To be filled by LLM`） |
| ② 插件日志记录 recall 的 memory_unit_id | 知道"这次 recall 了哪些 ID" | ❌ **未追踪**（日志只有计数，无 ID 列表） |

**实现逻辑**（两个前置就绪后）：

```python
# standby: 等待 Revision 接入 LLM + 插件日志扩展后启用
def _try_mark_suspicious(self, failure, recall_results, conn):
    for r in recall_results:
        if has_mark(r.get("text", "")):
            continue
        if semantic_overlap(failure.root_cause, r.get("text", "")) > 0.7:
            mark_memory(r["id"], "可疑",
                f"Layer 1 失败反推: {failure.root_cause[:200]}", conn)
```

**日志扩展**（插件 `pre_llm_call` 改造量）：

```python
# 现有 hooks.py 中 pre_llm_call 的日志 extra 字段增加：
extra={
    "recalled_ids": [r["id"] for r in raw_results[:20]],  # 新增
    # ... 已有字段 ...
}
```

改造量：约 2 行代码，零风险。

---

#### 7.8.3 已解决标记（半自动 — 失败模式库驱动）

| 维度 | 值 |
|:---|:---|
| 自动程度 | **半自动** — `failure_records.fix_success=True` 时触发 |
| 触发条件 | 某条 failure_record 被确认修复成功 |
| 触发时机 | `FailurePatternDB.mark_fixed()` 调用时 |
| 可行性 | **❌ 需先创建 `evolution.failure_records` 表** |

**前置条件**：

| 条件 | 要求 | 当前状态 |
|:---|:---|---:|
| ① `evolution.failure_records` 表存在 | 存储失败记录 | ❌ **不存在**（无 evolution schema） |
| ② 表中已有 `fix_success` 为 True 的记录 | 有可标记的失败 | ❌ 表不存在，记录自然也没有 |

**实现逻辑**（表就绪后）：

```python
def mark_fixed(self, record_id, success=True):
    # 更新 fix_success（已有逻辑）
    # ...
    if success:
        related = find_memories_by_trajectory(record.source_trajectory_id)
        for unit_id in related:
            mark_memory(unit_id, "已解决",
                f"修复方案：{record.mitigation_strategy}")
```

---

#### 7.8.4 作废标记（自动 — 聚类替代检测）

| 维度 | 值 |
|:---|:---|
| 自动程度 | **全自动** — 聚类脚本发现新旧替代时触发 |
| 触发条件 | 新旧记忆语义相似度 > 0.95 且时间跨度 > 7 天且旧记忆无 causal 关联 |
| 触发时机 | 聚类脚本执行聚类后 |
| 可行性 | **❌ 需额外开发** |

**前置条件**：

| 条件 | 要求 | 当前状态 |
|:---|:---|---:|
| ① 聚类脚本有"替代检测"逻辑 | 判断两条记忆是否互为替代 | ❌ **未实现**（相似度函数已 deprecated） |
| ② 聚类脚本能获取 `created_at` | 判断时间跨度 | ❌ 当前不读该字段 |

**不急于实现的原因**：`hindsight_retain` 本身会避免重复写入，作废场景实际上是"同一知识点在不同时间重复记录"，而非真正相互替代。这种场景用聚类+时间排序即可——新版本自动排在前面，不用标记旧版本。

---

#### 7.8.5 触发方式总结

| 标记类型 | 触发方式 | 可行性 | 依赖 | 优先级 |
|:---:|:---|:---:|:---|:---:|
| **错误** | 手动 | ✅ **现在可做** | 无 | P0 |
| **可疑** | 全自动 | ⚠️ 需补齐两前置 | Revision 接入 LLM + 插件日志扩展 | P0 |
| **已解决** | 半自动 | ❌ 需先建表 | `failure_records` 表 + CRUD | P1 |
| **作废** | 全自动 | ❌ 需额外开发 | 聚类脚本新增替代检测逻辑 | P2 |

**实施路线**：

```
Phase A（现在可做）：
  手动错误标记 — 发现即标记，零依赖

Phase B（第3节 failure_records 表就绪后）：
  已解决标记 — 表存在即可启用

Phase C（第1层 Revision 接入 LLM 后）：
  可疑标记 — 依赖 Revision 真实 root_cause + 插件日志扩展

Phase D（聚类脚本改造后）：
  作废标记 — 优先级最低，可暂缓
```

---

### 7.9 与不标记的方式对比

| 维度 | 建独立表 `evolution.marked_memories` | **文本标记 `[标记: ...]`** |
|:---|:---:|:---:|
| 侵入性 | 新建 schema + 表 | **零侵入**，只更新已有 text 字段 |
| 排除方式 | 查 JOIN → 剔除 | **文本关键词匹配**，零 I/O |
| LLM 感知 | ❌ 不可见 | ✅ **LLM 直接看到标记**，双重防线 |
| 可逆性 | DELETE 记录 | 正则替换移除标记 |
| 幂等性 | ON CONFLICT | `has_mark()` 检查 |
| 与现有模式 | 不一致 | **与聚类脚本的 `[聚类实体:]` 完全一致** |

---

## 八、第3层接口

为 Kanban 调度提供 worker 能力画像：

```sql
CREATE TABLE evolution.worker_performance (
    worker_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    success_count INT DEFAULT 0,
    failure_count INT DEFAULT 0,
    avg_duration_ms INT,
    last_success TIMESTAMPTZ,
    last_failure TIMESTAMPTZ,
    frequent_failure_type TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

第3层从该表获取 `success_rate` 做任务分配权重。

---

## 九、可行性评估（当前环境对照）

### 9.1 基础设施状态

| 组件 | 状态 | 说明 |
|:---|:---:|:---|
| Hindsight daemon | ✅ 运行中 | port 9177, bank "hermes" |
| PostgreSQL (shared-postgres) | ✅ 运行中 | port 5434, pgvector 可用 |
| 已有 schedma | `hindsight` 数据库 | memory_units(24,865) + memory_links(1,140,081) + entities(7,876) + unit_entities(59,011) |
| 知识导航插件 | ✅ 已上线运行 | pre_llm_call recall + MIN_SCORE 过滤 |
| 聚类脚本 | ✅ 完整管线 | DBSCAN + 实体提取 + 因果链+文本富化。`apply_to_db` 有完整实现 |
| 第1层算子 | ✅ 骨架完成 | 待接入 LLM 生成实际修正内容 |
| Hermes 版本 | v0.15.1 | 可通过系统 Python 安装 psycopg2 等依赖 |

### 9.2 模块可行性

| 模块 | 可行性 | 关键依赖 | 风险 |
|:---:|:---:|:---|:---:|
| **失败模式库** | ✅ 完全可行 | PG 连接（已有 CLUSTERING_DB_URL） | 低——纯新增表，不影响现有数据 |
| **因果链增强** | ✅ 完全可行 | 失败模式库先就绪 | 中——需要验证 CAUSAL_THRESHOLD=3 是否合理 |
| **权重注入** | ✅ 完全可行 | 知识导航插件已有，扩展其 `pre_llm_call` | 低——不影响现有召回流程 |
| **遗忘清理** | ⚠️ 可行但需谨慎 | 无特殊依赖 | 中——误删后无法恢复 |
| **第3层接口** | ✅ 完全可行 | 纯新增表 | 低——Kanban 尚未接入 |

### 9.3 关键差距

| 当前状态 | 需要完成 | 难度 |
|:---|:---|:---:|
| clustering 脚本的因果链是关键词正则 | 改为从 failure_records 派生 | 低（改 cli.py 几行代码） |
| Revision 算子只有骨架 | 接入 LLM 生成实际诊断 | 高（需要 LLM API 集成） |
| 知识导航插件只做了 recall → 过滤 | 扩展为多信号融合 | 低（加几十行代码） |
| 没有演化权重表 | 新建 evolution.failure_records + worker_performance | 低 |

### 9.4 不可行的方案（已排除）

| 方案 | 不可行原因 |
|:---|:---|
| 写 `memory_units.weight` | 表无此列，也不应改 Hindsight schema |
| 算子完成自动回调 | Hindsight 无事件系统 |
| 自动遗忘按权重删 | access_count = 0，无法计算使用频率 |
| 聚类组 avg_score 影响 recall | Hindsight 召回不用聚类组评分 |

---

## 十、最终效果预测

### 10.1 预期量化效果

| 指标 | 当前 | 第2层实施后 | 改善 |
|:---|:---:|:---:|:---:|
| 因果链数据量 | 5,085 条 | 持续增长（每周 ~50-200 条） | 稳定提升 |
| 因果链占比 | 0.44% | 6 个月后 ~2-3% | 从稀缺到显著 |
| recall 命中准确率 | 32.4%（技术相关 35%） | ↑ 5-10% | 因果链补充后实体图 + 因果链双路更准 |
| 知识导航注入率 | 当前已稳定 | ↑ 不明显 | 质量信号修正不是数量信号 |
| 遗忘误删风险 | 无机制 | < 1%（人工确认） | 安全可控 |

### 10.2 效果来源分析

```
第2层的改善来自三个独立贡献：

1. 因果链增强（来自 failure_records）
   → memory_links 中 causal 数据增多
   → Hindsight 的 link_expansion 引擎在 graph 路径里能用因果链展开
   → 但受 fact_type 同类型约束 + RRF 融合时 single-path 排名偏低的限制
   → 实际收益：中等。因果链不是 recall 主角，实体图才是

2. 权重注入（pre_llm_call 扩展）
   → 多信号融合 rerank_score（60%）+ quality_score（20%）+ causal_score（20%）
   → 相同主题的高频失败记录的记忆排在前面
   → 实际收益：稳定。不需要改任何底层架构

3. 聚类脚本的实体提取（已有，非第2层新增）
   → 已写入 7,876 实体 + 59,011 unit_entities
   → 实体图遍历有效，recall 时 JOIN 展开
   → 这是当前 recall 的三大支柱之一
```

### 10.3 效果上限

```
最佳情况（6 个月后）：
  因果链从 5,085 → ~20,000 条
  recall 准确率从 35% → ~42%
  知识导航每次注入 ~5-7 条相关记忆

最差情况（6 个月后）：
  因果链增长缓慢（〜8,000 条）
  recall 准确率无显著变化
  但失败模式库本身成为知识资产（可查、可分析、可靠）

上限取决于：
  1. Revision 算子的实际使用频率（写入 failure_records 的频率）
  2. 用户是否持续使用 Kanban/进化特性
  3. 因果链在 RRF 中的实际排名改善幅度
  ```

  ---

## 十一、评估基线

### 11.1 为什么需要

当前所有改进的效果判断靠"我感觉"。行业标准（LoCoMo、LongMemEval、BEAM）已非常成熟，不需要完整复现 1,540 题的 LoCoMo。

只需要 50 条覆盖典型使用场景的查询，每次改动前后对比基线。

### 11.2 评估查询定义

覆盖 5 个维度，每个维度 10 条：

| 维度 | 说明 | 示例 |
|:---|:---|:---|
| **语义召回** | 同类话题的记忆是否被命中 | "LiteLLM 配置相关的经验" |
| **实体关联** | 实体图展开的记忆是否被命中 | "pgvector 相关的技术选型" |
| **因果链** | 因果关系记忆是否被命中 | "gateway 崩溃的原因是什么" |
| **时态** | 近期记忆 vs 旧记忆的排序 | "上个月的性能优化方案" |
| **冲突** | 新旧矛盾记忆是否正确处理 | "老方法 vs 新方案的对比" |

### 11.3 评估指标

每条查询记录：

| 指标 | 说明 |
|:---|:---|
| top-3 正确率 | 前 3 条注入结果中是否包含目标记忆 |
| avg_rerank_score | 召回结果的 rerank 平均值 |
| avg_final_score | 多信号融合后的平均得分 |
| 延迟 | pre_llm_call 端到端耗时（ms） |
| 标记排除数 | 被 `[标记:]` 排除的记忆数量 |

### 11.4 评估流程

```
Phase 0（建立基线）：
  定义 50 条查询 → 在 pre_llm_call 插件日志中记录每次 recall 结果
  跑 5 次取平均 → 记录基线

Phase 1（改动后）：
  改代码 → 跑同样的 50 条查询 → 记录结果 → 对比基线

不依赖 Hindsight API 的特殊模式——在知识导航插件日志中已经有 `total_results/kept_results/score_stats` 字段，再加一个 `eval_query` 字段记录查询 ID 即可。
```

### 11.5 与现有日志的关系

改造量极低——hooks.py 的日志 extra 字段加一个 `eval_query_id`：

```python
extra = {
    "eval_query_id": EVAL_QUERY.get(user_message, None),  # 新增
    "total_results": len(raw_results),
    "kept_results": len(kept),
    "score_stats": score_stats,
    "latency_ms": latency_ms,
}
```

50 条查询的 ID 列表在 `config/layer2.yaml` 中定义。

---

## 十二、实施路径

### 12.1 依赖总图

```
L1-A (Revision 骨架) ──→ L1-B (LLM 集成) ──→ L2-K (时态衰减) ──┐
                          │                                        │
                          ├──→ L1-C (注册工具) ← Phase C 暂缓      │
                          ├──→ L1-D (通用失败捕获) ← Phase B       │
                          └──→ L2-E (可疑标记反推) ← Phase C 暂缓   │
                                                                     │
L2-A (建失败模式库) ──→ L2-B (因果链派生) ← Phase C 暂缓            │
    │                                                                 │
    └──→ L2-F (已解决标记) ← Phase C 暂缓                            │
    └──→ L2-H (worker_performance) ← Phase C 暂缓                     │
                                                                     │
L2-D (记忆标记) ──→ L2-K (时态衰减) ──→ L2-I (多信号融合) ← Phase B │
    │                                          │                    │
    └──→ L2-M (评估基线) ──────────────────────→ 决策门              │
         → Phase A 完 → 基线数据 → 决定是否进 Phase B ←─────────────┘

L2-C (插件日志扩展) ──→ L2-E (可疑标记反推) ← Phase C 暂缓
```

### 12.2 决策门

**Phase A 做完后，必须用评估基线数据回答 3 个问题**，全部通过才能进入 Phase B：

| 问题 | 通过标准 | 数据来源 |
|:---|:---:|:---|
| 标记排除后注入质量是否提升？ | kept 有效数量不下降，排除数量 > 0 | 评估基线 T5 输出 |
| 时态衰减后排序是否合理？ | 近期记忆在 top-3 中的占比提升 | 对比 T5 前后排序 |
| baseline 本身是否稳定？ | 3 次运行命中率波动 < 5% | 基线重复运行结果 |

**任意一项不通过 → 暂停 Phase B，先排查原因。**

### 12.3 阶段划分

| 阶段 | 定位 | 任务数 | 工时 | 效果贡献 |
|:---:|:---|:---:|:---:|:---:|
| **Phase A** | 核心价值，效果最确定 | 3 | **3.5h** | **~80%** |
| Phase B | 有条件，等数据决策 | 5 | 5h | ~15% |
| Phase C | 暂缓，回报不确定 | 10 | 10h | ~5% |
| **总计** | | **18** | **18.5h** | |

### 12.4 核心链路（Phase A：3.5h）

```
L2-D ──── 记忆标记：标记写入 + 排除        1.5h
  └── 在 PG 中标记 memory_unit 为错误/可疑/作废
  └── pre_llm_call 注入前用 "[标记:" 字符串匹配剔除
  └── 验证：T1 标记 1 条 → recall 确认排除

L2-K ──── 时态衰减评分                     0.5h
  └── 多信号融合增加第 4 信号 time_score
  └── λ=0.008 指数衰减（半衰期约 87 天）
  └── 验证：对比加上衰减前后的 top-3 排序

L2-M ──── 评估基线                         1.5h
  └── 定义 50 条覆盖 5 维度查询
  └── hooks.py 日志加 eval_query_id
  └── 首次运行记录 baseline 数据
```

三个模块无依赖冲突，可并行执行。

### 12.5 Phase B：5h（条件执行）

| 任务 | 工时 | 启动条件 |
|:---|:---:|:---|
| L2-A：失败模式库建表 + CRUD | 1h | 确定需要记录失败数据 |
| L2-C：插件日志扩展（recalled_ids） | 0.5h | 确定需要反推可疑记忆 |
| L1-B：Revision 接入 LLM | 1h | Phase A 基线提升 ≥5% |
| L2-I：多信号融合（quality+causal） | 1h | Phase A 基线稳定 |
| L1-D：通用失败捕获（6种来源） | 1.5h | Revision LLM 集成完成 |

### 12.6 Phase C：10h（暂缓）

| 任务 | 工时 | 暂缓理由 |
|:---|:---:|:---|
| L2-B：因果链生成脚本 | 1.5h | RRF 单路径上限低，边际收益已耗尽 |
| L2-E：可疑标记反推 | 1h | 等 L2-C 有数据 + 验证标记效果 |
| L2-F：已解决标记 | 0.5h | 依赖 failure_records 有数据 |
| L2-L：待验证标记 | 1h | 理论好但边际改进低 |
| L1-C：注册 Hermes 工具 | 0.5h | 手动调脚本够用 |
| L1-F：Recombination | 1h | 无 Kanban 多轨迹需求 |
| L1-H：Refinement | 1h | 当前冗余检测够用 |
| L2-J：聚类脚本调整 | 1h | 关键词因果链数据量小 |
| L2-G：作废标记 | 2h | 极少触发 |
| L2-H：第3层接口 | 0.5h | 依赖 Kanban 实际使用 |

### 12.7 推荐时间线

```
第 1 天（Phase A）：
  L2-D (1.5h) ─── 记忆标记写入 + 排除
  L2-K (0.5h) ─── 时态衰减评分
  L2-M (1.5h) ─── 评估基线定义 + 首次运行
      评估基线数据 → 决定是否进 Phase B

第 2-3 天（Phase B，仅当 Phase A 验证通过）：
  L2-A (1h) ───── 失败模式库建表
  L2-C (0.5h) ─── 插件日志扩展
  L1-B (1h) ───── Revision 接入 LLM
  L2-I (1h) ───── 多信号融合
  L1-D (1.5h) ─── 通用失败捕获

第 3-5 天（Phase C，等基线跑 1 个月后决定）：
  看数据趋势再决定要不要碰因果链和标记管理
```

---

## 十三、配置

```yaml
# config/layer2.yaml
failure_pattern_db:
  dsn: "${CLUSTERING_DB_URL}"
  causal_threshold: 3

causal_link_generation:
  enabled: true
  rule_a_weight_base: 0.5
  rule_a_weight_per_freq: 0.15
  rule_b_weight: 0.7
  rule_c_weight: 0.9

rerank:
  w_rerank: 0.6
  w_quality: 0.2
  w_causal: 0.2
  min_score: 0.6

retention:
  stale_days: 90
  min_text_length: 50
  resolved_grace_days: 30
  require_manual_confirm: true
```

---

## 十四、关键设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|:---|:---|:---:|:---|
| 失败模式库位置 | hindsight 库 vs 独立 schema | **独立 schema `evolution`** | 避免误操作 Hindsight 数据 |
| 因果链来源 | 关键词正则 vs 失败记录 | **失败记录** | 假阳性低，可验证 |
| 聚类脚本因果链 | 保留 vs 移除 | **从聚类脚本移除** | 由 failure_records 派生替代 |
| 聚类脚本实体提取 | 保留 vs 移除 | **保留** | 影响实体图遍历路径 |
| 权重存储 | memory_units 新列 vs memory_links 已有 | **memory_links.weight** | 非侵入，不改 Hindsight schema |
| 权重注入时机 | recall 时改 DB vs recall 后重排 | **recall 后重排** | 非侵入，不改 Hindsight |
| 遗忘执行 | 自动 vs 人工确认 | **人工确认** | 误删无法恢复 |
| Hook 集成 | 新建线索 vs 扩展现有插件 | **扩展现有插件** | 零新增故障点 |

---

## 十五、待验证问题

1. **CAUSAL_THRESHOLD=3 是否合理？** 需要实际运行一周后统计 failure_records 的频率分布
2. **w_rerank / w_quality / w_causal 权重比例** 需要 A/B 测试（开关性实验：先不加权重，看 baseline，再加权重对比差别）
3. **遗忘脚本的候选比例** 首次运行后查看 90 天前数据的实际数量，评估清理量级
4. **failure_records 写入性能** Revision 算子每次调用都 INSERT 一条，预期 < 5ms

---

*本文档基于 2026-05-30 实际环境验证结果编写。Hindsight recall 为三路（语义 + 实体图 + 因果链），关键词因果链方案已废弃。*
