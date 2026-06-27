# Hindsight 记忆维护框架

> 修正版（2026-06-12）— 不改 Hindsight 源码，充分复用现有能力

---

## 核心认知

| 点 | 结论 |
|:--|:------|
| Hindsight 没有清理能力 | 只能做加法（consolidation 提炼 observation），减法必须我们 SQL 做 |
| 去重必须在库层面 | 不只是 recall 端过滤——50 个 rerank 候选位可能全是同一条 |
| 删源要附带清理 | entities / unit_entities / memory_links / entity_names 里对应的行；observation 可能通过 source_memory_ids 引用被删记忆，需一并处理 |
| observation 让 consolidation 自行处理 | 源删了之后 consolidation 的 _DeleteAction 会在**下次运行**时清理 observation，有延迟，非即时 |
| Mental Model 暂不碰 | Mental Model（限定主题合成）和聚类（全局无监督发现）解决不同粒度的问题，但当前无迫切需求 |
| 不改 Hindsight 源码 | 所有改动在 SQL 脚本和我们的脚本层 |
| 不堵入口控制 | 根源是不碰 Hindsight 源码，所以 retain 侧不管，只做定期清理 |

---

## 一次性清理

### ① 超长记忆清理（170 条 >10K）

**现象**：大部分为重复积累 bug 产物——每条有效内容 ~300 字、同一内容重复数十到数百次（最严重 912 次）。集中爆发于 5/23-5/26。6 月以来的 2 条超长记忆（15.7K 的 `llmai 兼容性分析`、10.1K 的 `自进化工作项规划`）是**合法长内容**，不应截断或删除。

**⚠️ 执行前**：先用 SELECT 读一遍样本确认，或做一次 pg_dump 备份。

**操作**：

1. 查 `length(text) > 10000 AND bank_id = 'hermes'` 的记忆
2. **初步筛选**：排除 `created_at >= '2026-06-01'` 的记忆（6 月后几乎全是合法内容）
3. 对疑似重复条目，检测是否同一段落/句子连续出现 >3 次：
   - 实现方式：取 text 的前 300 字和最后 300 字比较，若高度重复则从 text 头部到第一个句号截取唯一段；或全文用正则提取去重后拼接唯一内容
4. 是重复积累 → 截断到唯一版本（保留第一段 ~300 字）
5. 如果该记忆**已完全被 consolidation 覆盖**（判断标准：该 memory 的 id 存在于某 observation 的 `source_memory_ids` 中，且 observation 与原始记忆的语义近似度 >0.7）→ 直接删除整条
6. 删除时附带清理：
   - `unit_entities` 中 `unit_id = 被删记忆.id`
   - `memory_links` 中 `from_unit_id = 被删记忆.id` 或 `to_unit_id = 被删记忆.id`
   - `entity_names` 中已无任何 unit 引用的孤立实体（可保留不动，不影响 recall）
7. **分批执行**：建议先处理 14 条 >50K 的，确认无误后再处理剩余的 156 条

### ② 50 条 consolidation failed

**现象**：全在 2026-05-08 同一 2 分钟窗口（06:53-06:55 UTC）内失败，13条 experience + 37条 world，平均 210 字。显然是瞬时 API 问题。

**操作**：

```sql
UPDATE memory_units
SET consolidation_failed_at = NULL
WHERE bank_id = 'hermes'
  AND consolidation_failed_at IS NOT NULL;
```

Hindsight 下次 consolidation 运行时会自动重试。

---

## 周常自动维护（Cron）

### 管线

| 顺序 | 步骤 | 说明 |
|:----:|:-----|:-----|
| ① | MinHash LSH 跨条目去重 | 新增，跑在聚类之前 |
| ② | 聚类分析（HDBSCAN → 噪声标记 → 因果链写入） | 已有 cron，每周一 10:00 |

### MinHash LSH 跨条目去重（新增步骤）

**定位**：精准发现多条记忆之间的近似重复，作为聚类的上游去噪步骤。

**原理**：每条记忆转成 N 个 shingles（字符 n-gram）的 MinHash 签名 → LSH 分桶 → cosine > 0.8 的近似重复对落入同一桶。O(n) 复杂度，50K 条约 1-2 分钟。

**依赖**：`pip install datasketch`（纯 CPU，无 GPU 需求）
**脚本**：`scripts/self-evolving/scripts/dedup_minhash.py`
**输出**：疑似重复对列表（memory_id_A, memory_id_B, similarity）
**后续**：SQL 合并（保留最长/最新/信息最丰富的一条，其他删源并清理关联表）

**适用场景**：自动 retain 产生的同一事实的多条副本（如 3 次不同的"9p 崩溃"记录）。不适用于独一性事实型记忆。

---

## 持续监控

作为周常维护 cron 的最后一步（LSH 去重 → 聚类分析 → 质量报告），输出指标趋势到飞书。

| 指标 | 获取方式 | 触发条件 |
|:----|:---------|:--------:|
| 重复积累率 | SQL: `length>10K 且重复模式的条数 / 总数` | >1% 告警 |
| consolidation 覆盖率 | SQL: `consolidated_at IS NOT NULL / 总数` | <50% 告警 |
| 中位记忆长度 | SQL: `PERCENTILE_CONT(0.5)` | >2K 告警 |
| 单条最大长度 | SQL: `MAX(length(text))` | >8K 告警 |

---

## 不做的事

- ❌ 改 Hindsight 源码（retain 逻辑、consolidation 行为、reranker 策略）
- ❌ 入口控制（根源：不碰源码，所以 retain 侧不管入）
- ❌ Mental Model（不同粒度工具，当前无迫切需求）
- ❌ pre_llm_call 过滤标记噪音（库本身必须干净，召回端做过滤是本末倒置）

---

## 可落地性评估

> 基于 2026-06-12 实际数据与系统状态。

### 现有周一 cron 时间线

```
09:00  系统巡检（no_agent 脚本）
09:30  聚类分析（agent 驱动，LLM prompt → 执行 → 飞书通知）
10:30  知识树 consolidate（no_agent 脚本，每日）
11:00  知识导航基线（no_agent 脚本）
13:00  记忆清理（agent 驱动，每日）
```

### Hindsight DB 当前状态

| 指标 | 数值 |
|:----|:----|
| 总记忆条数 | 49,753 |
| 已 consolidation | 33,445（67.2%） |
| consolidation 失败 | 50（0.1%） |
| 未 consolidation | 16,273（32.7%，即 observations） |
| >10K（5月前/重复积累） | 168 条，均值 24K，中位 16K |
| >10K（6月后/合法长内容） | 2 条 |
| >5K | 506 条 |
| 中位长度 | 136 字符 |
| 日均 retain | ~1,500 条 |
| Entities | 13,721 |
| Unit_entities | 127,344 |
| memory_links | 1,315,520 |

### 分步评估

#### ② 50 条 consolidation failed — 可落地性 100%

1 条 SQL UPDATE，5 秒完成。全在 2026-05-08 同一 2 分钟窗口内失败，清 consolidation_failed_at 后下轮 consolidation 自动重试。零风险。

#### ① 168 条超长记忆清理 — 可落地性 85%

需要写一个新脚本 `scripts/self-evolving/scripts/dedup_long_memories.py`，逻辑：
1. SQL 查询 168 条 >10K 且 created_at < '2026-06-01' 的记忆
2. 逐条检测前 300 字是否在全文重复出现 >3 次
3. 是重复积累 → 截断到唯一版本（~300 字）
4. 如果已被 consolidation 完全覆盖（source_memory_ids 引用 + 语义近似度 >0.7）→ 直接删除
5. 删除时清理 unit_entities / memory_links 关联行
6. 分批执行：先处理 14 条 >50K 的，再处理 156 条剩余

**风险点**：步骤 4 的"语义近似度 >0.7"判断需要在 SQL 里做或者脚本里调 embedding API + cosine 计算，有一定实现复杂度。

**代码归属**：全新脚本，可 Qoder 写或直接 WSL 新建后同步到 D:\HermesProject。

#### MinHash LSH 跨条目去重 — 可落地性 50%

**卡点 1：聚类 cron 是 agent 驱动的**

现有 cron `834f94944665`（每周一 09:30）的 prompt 是：
```
执行聚类分析脚本（clustering-analysis-v3）的 --apply 模式。
完成后用 send_message 发飞书通知，简要报告执行结果...
```

这是 agent 模式——每次跑都要走 LLM 一次。在其前面插入 LSH 步骤，有两种选择：

| 方案 | 操作 | 优劣 |
|:----|:-----|:-----|
| A：保持 agent，改 prompt | 更新 cron prompt，加一句"先跑 LSH 去重脚本，再跑聚类" | 简单，但 LSH 脚本调 LLM 判断结果浪费 token |
| B：整个管线改为 no_agent 脚本 | 写一个 wrapper 脚本依次执行 LSH → 聚类 → 发飞书，cron 改为 no_agent | 消除 token 浪费，管线原子化。聚类脚本原本是 agent 因为需要 send_message，改成 no_agent 后可在脚本内直接用 curl 或 Python 发飞书 |

推荐方案 B——聚类 cron 当前是 agent 但实际只是跑脚本+发通知，转为 no_agent 每月可省 ~300K token（~15次×每次~20K）。

**卡点 2：LSH 脚本是新代码，有工程风险**

`dedup_minhash.py` 涉及：
- MinHash 签名生成（字符 n-gram shingle）
- LSH 分桶（datasketch 库）
- 疑似重复对排序输出
- SQL merge（保留一条，其他删源）
- 清理 entity/unit_entity/memory_link 关联数据
- 处理 observation 的 source_memory_ids 引用断裂

合并逻辑有风险——删错实体可能导致知识图谱引用断裂。建议 Qoder 实现，先输出 dry-run 报告（不执行删除），确认后再上线。

**卡点 3：依赖**

```bash
pip install datasketch  # 纯 CPU，50K 条约 1-2 分钟
```

Hermes venv 内安装即可，无 GPU 需求。

#### 持续监控 — 可落地性 90%

接在聚类 cron 尾步，4 条 SQL → 格式化 → 发飞书。如果管线改为 no_agent 脚本（方案 B），在 wrapper 末尾追加 Python SQL 查询非常直接。如果保持 agent，加在 LLM prompt 里也可以。

唯一依赖：送飞书的 token/chat_id，复用现有集群 cron 已有的飞书通知通道。

### 实施建议概要

| 步骤 | 优先级 | 可落地性 | 核心工作 | 代码归属 |
|:----|:-----:|:-------:|:--------|:--------:|
| 50条 consolidation failed | P0 | 100% | 1条 SQL，5秒 | 无需写代码 |
| 168条超长记忆清理 | P0 | 85% | 写 dedup_long_memories.py | Qoder 或直接写 |
| 聚类 cron 改 no_agent | P1 | 80% | 写 wrapper 脚本 + 更新 cron 配置 | Qoder |
| MinHash LSH 去重 | P1 | 50% | 写 dedup_minhash.py → 集成到管线 | Qoder |
| 持续监控 | P2 | 90% | SQL + 飞书通知 | Qoder 或直接写 |
