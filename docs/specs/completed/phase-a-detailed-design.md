# Phase A 详细设计 — 记忆标记 + 时态衰减 + 评估基线

> **版本**: v1.1  
> **创建时间**: 2026-05-30  
> **涉及项目**: 聚类脚本（标记写入）+ 知识导航插件（排除/时态/基线）  
> **总工时**: 3.5h（4 个任务：L2-D1 0.5h + L2-D2 1h + L2-K 0.5h + L2-M 1.5h）  
> **设计前提**: 不修改 Hindsight daemon、不修改 Hermes Agent 核心、不新建 PG schema

---

## 一、修改文件清单

| 文件 | 当前行数 | 改动量 | 说明 | 验证状态 |
|:---|:---:|:---:|:---|:---:|
| `core/hooks.py` | 113 | +40 行 | 主逻辑：排除 + 时态 + eval 日志 | ✅ 已验证可行 |
| `core/filtering.py` | 67 | +40 行 | 新增 `exclude_marked()` + `calculate_time_score()` + **修复排序 bug** | ✅ 已验证可行 |
| `config.py` | 118 | +8 行 | 新增 eval_queries 配置 | ✅ 已验证可行 |
| `scripts/mark_memory.py` | **新建** | ~60 行 | 独立脚本，归属聚类项目 | ✅ 已验证可行 |

**不修改**：`adapters/hindsight.py`、`plugin.yaml`、`__init__.py`

### 环境验证结论（看板并行验证通过）

| 模块 | 验证人 | 结论 | 关键发现 |
|:---|:---:|:---:|:---|:---:|
| L2-D1 标记写入 | Kanban developer | ✅ | `psycopg2` 2.9.12 已装，`CLUSTERING_DB_URL` 已配，脚本已创建 |
| L2-D2 排除逻辑 | Kanban developer | ✅ | `recall → exclude_marked() → filter_by_score` 插入点确认 |
| L2-K 时态衰减 | Kanban developer | ✅ | `mentioned_at` 100% 覆盖，ISO 8601 格式，通过实测 30+ 条 |
| L2-M 评估基线 | Kanban developer | ✅ | 插件日志已是 JSON格式（`JSONFormatter`），通过 `extra=` 注入即可 |

**P1 已发现需修复**：`filter_by_score` 在截断到 `max_results` 前未按分数排序。

**T2 的"依赖 API 标记字段"警告不成立**：排除逻辑用 `"[标记:" in text` 纯字符串匹配，不依赖 API 任何字段。

---

## 二、L2-D：记忆标记（1.5h = 写入 0.5h + 排除 1h）

### 2.1 L2-D1：标记写入（归属聚类脚本）

**源码位置**：`/mnt/d/HermesProject/scripts/self-evolving/scripts/mark_memory.py`
**部署位置**：`~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py`

> ⚠️ **T1 看板验证已创建该脚本，但内容与设计存在 5 项差距，需修改后才能使用（见下方核查表）。**

```python
# mark_memory.py 源码（项目目录）
# deploy: cp scripts/mark_memory.py ~/.hermes/scripts/clustering-analysis-v3/scripts/
```

MARK_PREFIXES = {
    "[标记: 错误]",
    "[标记: 作废]",
    "[标记: 可疑]",
    "[标记: 已解决]",
}

def has_mark(text: str) -> bool:
    """检查一条记忆是否已被标记（幂等保护）。"""
    return any(p in text for p in MARK_PREFIXES)

def mark_memory(unit_id: str, mark_type: str, note: str,
                db_url: str = "") -> bool:
    """在 memory_units.text 末尾追加标记。"""
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT text FROM memory_units WHERE id = %s", (unit_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close(); return False
    
    text = row[0]
    if has_mark(text):
        cur.close(); conn.close(); return False  # 幂等
    
    new_text = text + f"\n[标记: {mark_type}] {note}"
    cur.execute("UPDATE memory_units SET text = %s WHERE id = %s",
                (new_text, unit_id))
    conn.commit()
    cur.close(); conn.close()
    return True

def unmark_memory(unit_id: str, db_url: str = "") -> bool:
    """移除记忆中的标记（可逆恢复）。"""
    import re
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT text FROM memory_units WHERE id = %s", (unit_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close(); return False
    
    text = row[0]
    if not has_mark(text):
        cur.close(); conn.close(); return False
    
    cleaned = re.sub(r'\n\[标记: [^\]]+\] [^\n]*', '', text)
    cur.execute("UPDATE memory_units SET text = %s WHERE id = %s",
                (cleaned, unit_id))
    conn.commit()
    cur.close(); conn.close()
    return True

if __name__ == "__main__":
    # CLI 入口：python3 mark_memory.py <unit_id> <type> <note>
    if len(sys.argv) < 3:
        print("用法：python3 mark_memory.py <unit_id> <类型> [说明]")
        sys.exit(1)
    
    import os
    db_url = os.environ.get("CLUSTERING_DB_URL", "")
    
    unit_id = sys.argv[1]
    mark_type = sys.argv[2]
    note = sys.argv[3] if len(sys.argv) > 3 else ""
    
    success = mark_memory(unit_id, mark_type, note, db_url)
    print("✅ 标记成功" if success else "⏭️ 跳过（已有标记或未找到）")
```

**调用方式**：

```bash
# 手动标记一条错误记忆
cd ~/.hermes/scripts/clustering-analysis-v3
CLUSTERING_DB_URL="postgresql://..." python3 scripts/mark_memory.py \
  <unit_id> 错误 "正确信息：此处应为 X 而非 Y"

# 或直接 PG UPDATE（手边操作，不用脚本）
psql -h 127.0.0.1 -p 5434 -U postgres -d hindsight \
  -c "UPDATE memory_units SET text = text || E'\n[标记: 错误] 正确信息：xxx' WHERE id = '<unit_id>';"
```

### T1 核查：已创建脚本 vs 设计差距

T1 看板在部署路径 `/root/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py` 创建了初始版本。经核查，与设计要求存在 5 项差距：

| # | 设计要求 | T1 版本 | 影响 |
|:---:|:---|:---|:---:|
| 1 | 结构化标记格式 `\n[标记: 类型] 说明` | 自由文本追加 `current_text + " " + tag` | 排除逻辑 `"[标记:" in text` 无法识别 |
| 2 | 幂等保护：`has_mark()` 检查后再写入 | 无幂等保护，重复调用重复追加 | 多次标记同一记忆产生污染 |
| 3 | `unmark_memory()` 可逆恢复 | 未实现 | 标记错误后无法恢复 |
| 4 | 结构化前缀：`[标记: 错误/可疑/作废/已解决]` | 自由文本 `tag` 参数 | 排除逻辑依赖统一前缀 |
| 5 | 源码在项目目录，deploy 到 `~/.hermes/` | 仅部署路径有文件，源码目录无 | 版本管理缺失 |

**处理方式**：编码阶段需将源码写入项目目录 `/mnt/d/HermesProject/scripts/self-evolving/scripts/mark_memory.py`，按设计要求实现，然后 deploy 到运行目录。

---

### 2.2 L2-D2：排除逻辑（归属知识导航插件）

```python
# 在 pre_llm_call 函数中，recall 之后、filter_by_score 之前

def exclude_marked(results: list[dict]) -> tuple[list[dict], int]:
    """剔除已被标记的记忆。
    
    纯字符串匹配，零 I/O。不需要查 PG。
    即使 recall 返回了标记的记忆，也在注入前过滤掉。
    """
    filtered = []
    excluded_count = 0
    for r in results:
        text = r.get("text") or ""
        if "[标记:" in text:
            excluded_count += 1
            continue
        filtered.append(r)
    return filtered, excluded_count
```

**在 hooks.py 中的插入位置**（`pre_llm_call` 函数内）：

```python
def pre_llm_call(session_id, user_message, **kwargs):
    # 1. recall（已有）
    client = HindsightClient()
    result = client.recall(user_message)
    raw_results = result.get("results", [])
    
    # 2. 提取 rerank 分数（已有）
    rerank_map = extract_rerank_scores(result.get("trace", {}))
    
    # 3. 剔除标记记忆 ← 新增
    cleaned, excluded = exclude_marked(raw_results)
    
    # 4. 后续处理（已有）
    kept, all_scores = filter_by_score(cleaned, rerank_map, ...)
```

### 2.3 验证方法（T1）

```
1. 选 1 条记忆，用 mark_memory() 标记为 [标记: 错误]
2. 发一条包含该记忆相关关键词的消息
3. 检查 pre_llm_call 日志：
   - excluded_count > 0
   - 该记忆不在 injected context 中
4. 可逆验证：用 unmark_memory() 移除标记，确认重新被 recall
```

---

## 三、L2-K：时态衰减评分（0.5h）

### 3.1 评分函数

```python
# 在 filtering.py 中新增

import math
from datetime import datetime, timezone

def calculate_time_score(mentioned_at_str: str) -> float:
    """基于 mentioned_at 的指数衰减。
    
    使用 recall 返回的 mentioned_at 字段（Hindsight 时序标签），
    而非 created_at（recall 接口不返回该字段）。
    
    公式：time_score = exp(-λ × days_since_mention)
    λ = 0.008（半衰期约 87 天）
    
    30 天内 ≈ 1.0
    90 天  ≈ 0.49（接近半衰期）
    180 天 ≈ 0.24
    365 天 ≈ 0.05
    """
    if not mentioned_at_str:
        return 1.0
    
    try:
        ts = datetime.fromisoformat(mentioned_at_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        
        days = (datetime.now(timezone.utc) - ts).days
        if days < 0:
            return 1.0  # 未来的时间戳（异常）
        
        return math.exp(-0.008 * days)
    except (ValueError, TypeError):
        return 1.0  # 解析失败，不衰减
```

### 3.2 修改 filter_by_score（含排序 bug 修复）

```python
def filter_by_score(
    raw_results: list[dict],
    rerank_map: dict[str, float],
    min_score: float = CONFIG.min_score,
    max_results: int = CONFIG.max_results,
    enable_temporal: bool = True,  # ← 新增参数
) -> tuple[list[dict], list[float]]:
    all_scores: list[float] = []
    scored: list[tuple[dict, float]] = []  # (result, score)
    
    for r in raw_results:
        node_id = r.get("id", "")
        score = rerank_map.get(node_id, 0.0)
        
        # 时态衰减（新增）
        if enable_temporal:
            time_score = calculate_time_score(r.get("mentioned_at", ""))
            score = 0.5 * score + 0.5 * time_score
        
        all_scores.append(score)
        if score >= min_score:
            scored.append((r, score))
    
    # [修复] 按分数降序排序后再截断
    # 原代码直接 kept[:max_results] 未排序，可能导致高分结果被遗漏
    scored.sort(key=lambda x: x[1], reverse=True)
    kept = [r for r, s in scored[:max_results]]
    return kept, all_scores
```

**为什么 Phase A 用 0.5/0.5 而不是 0.5/0.15/0.15/0.2**：Phase A 只有 rerank + time 两个信号可用（failure_records 表和因果链数据都是 Phase B 的事）。0.5/0.5 确保新旧信号的权重对等。

**为什么用 mentioned_at 而不是 created_at**：recall 接口返回的字段已确认不包含 `created_at`，但包含 `mentioned_at`（所有返回结果中均有值）。`mentioned_at` 是 Hindsight 的时间标签，语义上接近"最后一次被提及(记录)的时间"，作为时态衰减的依据够用。

### 3.3 验证方法

```
1. 构造 2 条记忆：一条 3 天前（time_score≈0.98），一条 90 天前（time_score≈0.49）
2. 发一条同时命中这两条记忆的查询
3. 确认排序：无衰减时顺序不变；有衰减时近期记忆在前
4. 日志 score_stats 中 avg 不应显著下降（近期记忆被 boost）
```

---

## 四、L2-M：评估基线（1.5h）

### 4.1 配置（修改 config.py）

```python
# 在 KnowledgeNavigationConfig 中新增

eval_queries: dict = field(default_factory=lambda: {
    "semantic_01": "LiteLLM 配置相关的问题怎么处理",
    "semantic_02": "PG 连接错误怎么排查",
    "entity_01": "shared-postgres 相关的技术配置",
    "entity_02": "Hindsight embedding 模型是什么",
    "causal_01": "gateway 崩溃的原因和修复",
    "causal_02": "worker 超时与内存不足的关系",
    "temporal_01": "上周做的性能优化方案",
    "temporal_02": "本月的配置变更记录",
    "conflict_01": "老方案 v1 和当前方案有什么区别",
})
```

初始 50 条查询的完整列表见项目 `config/eval_queries.yaml`。此处只展示 9 条示例。

### 4.2 日志扩展（修改 hooks.py）

```python
# 在 pre_llm_call 的日志 extra 中

# 匹配查询 ID（前缀匹配，仅针对预定义的 50 条评估查询）
eval_query_id = None
for qid in CONFIG.eval_queries:
    if user_message.startswith(CONFIG.eval_queries[qid][:20]):
        eval_query_id = qid
        break

extra = {
    "eval_query_id": eval_query_id,          # 新增：评估基线用，非评估查询时为 null
    "session_id": session_id,
    "query_trunc": query_trunc,
    "event": "recall_success",
    "total_results": len(raw_results),
    "kept_results": len(kept),
    "excluded_count": excluded_count,        # 新增
    "score_stats": score_stats,
    "total_chars": sum(len(line) for line in context_lines),
    "latency_ms": latency_ms,
}

# 说明：插件日志已使用 JSONFormatter（config.py 中定义），
# 通过 extra= 注入的字段会自动序列化为 JSON。
# 无需改日志系统、无需新建文件、无需改 agent.log 格式。
# 已验证：当前 hooks.py 的 extra 字段已包含多个自定义字段。
```

### 4.3 基线提取脚本

**源码位置**：`/mnt/d/HermesProject/plugins/knowledge-navigation/scripts/collect_baseline.py`
**部署位置**：`~/.hermes/scripts/collect_baseline.py`

已创建，内容见项目目录 `scripts/collect_baseline.py`。核心逻辑：从 agent.log 中解析含 `eval_query_id` 的 JSON 日志行，按查询 ID 分组汇总，输出结构化基线数据。
    
    输出：
        {
            "semantic_01": {
                "total_results": 45,
                "kept_results": 3,
                "avg_score": 0.62,
                "excluded_count": 0,
            },
            ...
        }
    """
    with open(log_file, "r") as f:
        lines = f.readlines()
    
    baselines = {}
    for line in lines:
        if '"eval_query_id"' not in line:
            continue
        try:
            data = json.loads(line)
            qid = data.get("eval_query_id")
            if qid:
                baselines[qid] = {
                    "total_results": data.get("total_results", 0),
                    "kept_results": data.get("kept_results", 0),
                    "avg_score": data.get("score_stats", {}).get("avg", 0),
                    "excluded_count": data.get("excluded_count", 0),
                    "latency_ms": data.get("latency_ms", 0),
                }
        except (json.JSONDecodeError, KeyError):
            continue
    
    return baselines
```

### 4.4 验证方法（T5）

```
1. 运行 50 条查询（手动发或脚本自动发）
2. 运行 collect_baseline.py 输出 baseline 数据
3. 检查：每条查询都有 kept > 0
4. 检查：score_stats.avg 分布合理（平均 0.5-0.7）
5. 重复运行 3 次，验证波动 < 5%
```

---

## 五、完整数据流

```
用户发消息
    │
    ▼
pre_llm_call(user_message, session_id)
    │
    ├── 1. client.recall(user_message)         ← 已有
    │       └── POST /memories/recall → results[]
    │
    ├── 2. extract_rerank_scores(trace)         ← 已有
    │       └── {node_id: rerank_score, ...}
    │
    ├── 3. exclude_marked(results)              ← 新增
    │       └── f"[标记:" in text → skip
    │       └── 返回 (filtered, excluded_count)
    │
    ├── 4. filter_by_score(filtered, ...)       ← 修改
    │       ├── rerank_score × 0.5
    │       ├── time_score × 0.5（新增）
    │       └── kept = top 3 with score ≥ 0.6
    │
    ├── 5. format_context_lines(kept)           ← 已有
    │
    └── 6. 日志 extra                           ← 修改
            ├── eval_query_id（新增）
            ├── excluded_count（新增）
            └── 已有字段不变
```

---

## 六、backward compatibility

| 场景 | 当前行为 | Phase A 后行为 |
|:---|:---|:---|
| 无标记的记忆 | 正常注入 | 不变 |
| 有标记的记忆 | 正常注入（污染源） | **排除** |
| 时态衰减关闭 | — | `KN_DISABLE_TEMPORAL=1` 环境变量恢复旧行为 |
| 无 eval_query_id | 日志有 eval_query_id: null | 不变，不影响其他日志字段 |

---

## 七、风险与回滚

| 风险 | 概率 | 影响 | 回滚方式 |
|:---|:---:|:---:|:---|
| 标记格式有误匹配到正常文本 | 低 | 排除不该排除的记忆 | 修复正则或 `unmark_memory()` |
| 时态衰减过度导致旧好记忆消失 | 低 | top-3 质量下降 | 调 λ 或关闭 `enable_temporal` |
| 日志新增字段破坏下游解析 | 极低 | 日志分析脚本报错 | 加字段是追加，不破坏已有解析 |

### 7.1 修改前备份

**4 个文件需要备份**，编码前拷贝到项目目录：

```bash
# 备份到项目目录（不是部署目录）
cp ~/.hermes/plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py \
   /mnt/d/HermesProject/scripts/self-evolving/backups/hooks.py.bak
cp ~/.hermes/plugins/knowledge-navigation/src/knowledge_navigation/core/filtering.py \
   /mnt/d/HermesProject/scripts/self-evolving/backups/filtering.py.bak
cp ~/.hermes/plugins/knowledge-navigation/src/knowledge_navigation/config.py \
   /mnt/d/HermesProject/scripts/self-evolving/backups/config.py.bak
```

### 7.2 回滚操作

| 层别 | 操作 |
|:---|:---|
| **插件级别**（回滚整个插件） | 从 `config.yaml` 的 `plugins.enabled` 中移除 `knowledge-navigation` → 重启 Gateway |
| **文件级别**（回滚单个文件） | `cp /mnt/d/HermesProject/scripts/self-evolving/backups/hooks.py.bak ~/.hermes/plugins/knowledge-navigation/src/core/hooks.py` → 重启 Gateway |
| **代码级别**（有 git 时） | `cd /mnt/d/HermesProject/self-evolving && git checkout -- docs/phase-a-detailed-design.md` |

**注意**：修改知识导航插件后必须重启 Hermes Gateway 才能生效。重启操作由用户自行执行。

---

## 八、实施顺序

```
第 1 步：创建 mark_memory.py（0.5h）— ✅ 看板验证已创建
  └── 文件：~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py
  └── 内容：mark_memory() + unmark_memory() + CLI 入口
  └── 环境已验证：psycopg2、CLUSTERING_DB_URL、memory_units 表结构均就绪

第 2 步：修改 filtering.py（0.5h）
  └── 新增 exclude_marked() 排除逻辑
  └── 新增 calculate_time_score(mentioned_at) 时态衰减
  └── 修改 filter_by_score() → 排序 + 时态衰减 + enable_temporal 参数
  └── 环境已确认：mentioned_at 100% 覆盖，ISO 8601 格式

第 3 步：修改 hooks.py（0.5h）
  └── pre_llm_call 插入 exclude_marked() 调用
  └── 日志 extra 加 eval_query_id + excluded_count
  └── filter_by_score 传 enable_temporal=True, max_results=3
  └── 环境已确认：pre_llm_call 函数签名 = (session_id, user_message, **kwargs)
  └── 返回值类型：str → 注入到 user message 末尾

第 4 步：修改 config.py + 创建配置文件（0.5h）
  └── config.py 新增 eval_queries 字典
  └── 创建 config/eval_queries.yaml（50 条覆盖 5 维度查询）
  └── 环境已确认：KnowledgeNavigationConfig 支持 @classmethod from_env 模式

第 5 步：编写 collect_baseline.py（0.5h）
  └── 文件：~/.hermes/scripts/collect_baseline.py
  └── 从 agent.log 提取 eval_query_id 日志
  └── 环境已确认：agent.log 中插件日志已是 JSON 格式

第 6 步：验证 T1 + T5（1h）
  ├── T1：标记 1 条错误记忆 → 发相关消息 → 确认排除
  ├── 时态对比：构造不同时间的记忆 → 确认排序
  └── T5：跑 50 条查询 → 确认 baseline 输出
```
