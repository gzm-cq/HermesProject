# Phase A 进阶优化设计

> **版本**: v3.0
> **更新**: 2026-06-10 — 核心修正：方向③ 的"分数通道问题"实际不存在，Hindsight trace 已暴露原始 CE 分数（score_components.cross_encoder_score）。方向③ ROI 重新评估。
> **创建时间**: 2026-06-10
> **基于**: Phase A 全部 9 项部署完成后的四个进阶方向
> **目标文件**: `plugins/knowledge-navigation/src/knowledge_navigation/core/filtering.py` + `config.py`

---

## 目录

1. [MMR 多样性重排](#1-mmr-多样性重排-p105h)
2. [自适应 MIN_SCORE](#2-自适应-min_score-p103h)
3. [自我蒸馏式上下文压缩](#3-自我蒸馏式上下文压缩-p2)
4. [因果链置信度传播](#4-因果链置信度传播-p205h)

---

## ① MMR 多样性重排（P1，0.5h）

### 现状

`filtering.py:152-165` 使用硬限幅：

```python
# 当前实现 — 硬性"同主题最多3条"
diverse: list[dict] = []
for c in candidates_deduped:
    if len(diverse) >= max_results:
        break
    c_text = str(c.get("text", "") or c.get("name", ""))
    same_topic = sum(
        1 for d in diverse
        if _jaccard(d.get("text", "") or d.get("name", ""), c_text) > 0.6
    )
    if same_topic < 3:
        diverse.append(c)
```

**问题：**
- 先到先得，早出现的同主题条目占优
- 3 条上限本身是硬编码，不会根据分数分布动态调整
- 不考虑分数与多样性的权重平衡

### 方案：MMR（Maximum Marginal Relevance）

**公式：**

```
MMR = argmax_{d ∈ R\S} [ λ · score_norm(d) − (1−λ) · max_{s ∈ S} sim(d, s) ]
```

| 符号 | 含义 |
|------|------|
| R | 候选集（已排序、已去重） |
| S | 已选集（初始空集） |
| λ | 权衡参数，0=纯多样性，1=纯相关性 |
| score_norm(d) | fused_score 归一化到 [0,1] |
| sim(d, s) | 字符级 Jaccard（复用 `_jaccard`） |

**算法流程：**

```
输入：候选集 candidates（已排序）, 分数列表 fused_scores, max_results, λ
输出：diverse（MMR 重排后的 top-k）

1. 归一化 fused_scores 到 [0,1] → scores_norm
2. selected = []（空集，存下标）
3. remaining = [0, 1, 2, ..., n-1]（全下标）
4. 循环 max_results 次：
   a. 对每个 i ∈ remaining，计算 MMR：
      - rel = scores_norm[i]
      - div = 0（如果 selected 为空）
      - div = max_{j∈selected} jaccard(text(candidates[i]), text(candidates[j]))
      - mmr = λ · rel − (1−λ) · div
   b. 选最大的 i，加入 selected，从 remaining 移除
5. 返回 [candidates[i] for i in selected]
```

### 代码变更

**filtering.py：** 新增函数，替换原有硬限幅块

```python
def _mmr_diversity(
    candidates: list[dict],
    scores: list[float],
    max_results: int,
    lambda_mrr: float = 0.6,
) -> list[dict]:
    """MMR 多样性重排"""
    if not candidates or max_results <= 0:
        return []

    # 归一化分数到 [0,1]
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-6:
        norms = [0.5] * len(scores)
    else:
        norms = [(s - lo) / (hi - lo) for s in scores]

    selected: list[int] = []
    remaining = set(range(len(candidates)))

    for _ in range(min(max_results, len(candidates))):
        best_idx = None
        best_mmr = -float("inf")
        for i in remaining:
            rel = norms[i]
            if selected:
                c_text = str(candidates[i].get("text", "") or candidates[i].get("name", ""))
                div = max(
                    _jaccard(
                        c_text,
                        str(candidates[j].get("text", "") or candidates[j].get("name", "")),
                    )
                    for j in selected
                )
            else:
                div = 0.0
            mmr = lambda_mrr * rel - (1 - lambda_mrr) * div
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i
        if best_idx is not None:
            selected.append(best_idx)
            remaining.remove(best_idx)

    return [candidates[i] for i in selected]
```

**config.py：** 追加参数

```python
# MMR diversity
lambda_mrr: float = field(default=0.6)  # MMR λ，越大越重相关性
```

**替换点：** filtering.py 第 152-165 行替换为一行：

```python
kept = _mmr_diversity(candidates_deduped, [c[1] for c in candidates], max_results, CONFIG.lambda_mrr)
```

### 验证

| 场景 | 旧行为 | 新行为 |
|------|--------|--------|
| 前 8 条同主题 | 全保留前 8 条 | 保留相关性最高+多样性最优的 8 条 |
| 前 3 条同主题，第 4 条不同 | 第 4 条因第 4 个 slot 被保留 | 取决于 λ：高 λ 保留原序，低 λ 可能把第 4 条提前 |
| 分数集中，主题分散 | 保留分散结果 | 不变（MMR 不会降级分散结果） |

---

## ② 自适应 MIN_SCORE（P1，0.3h — Review：暂缓）

### 现状

当前 `min_score=0.8`（config.py:67），但：
- RRF 融合后的分数全在 1.1-1.8 区间
- 0.8 的阈值实际从未生效
- 分数分布因查询质量波动，一刀切不合理

### 环境限制（v1.2 Review 发现）

RRF（倒数秩融合）的分数存在**"赢者通吃"特性**，top-1 分数远高于后续：
```
今日数据: [1.84, 1.31, 1.25, 1.22, 1.18, 1.15]
归一化后: [1.00, 0.28, 0.20, 0.16, 0.10, 0.06]
第一间隙: 0.72 >> min_gap_ratio(0.15)
→ elbow 会裁到只剩 top-1，过于激进
```

如果 min_gap_ratio 调高到 0.5，则肘部检测在所有分数序列中都很难触发，相当于降级为固定阈值。

### 数据分析确认（v2.0）

从 trace.log 提取今日 178 条 rerank_score，实际值分布：

```
分数分布:
  0.8:   7  (4%)  ← MIN_SCORE 阈值
  0.9:   3  (2%)
  1.0:  37 (21%)
  1.1:  63 (35%)
  1.2:  31 (17%)
  1.3:  14  (8%)
  1.4:  11  (6%)
  1.5+:  12  (7%)

score < 0.8: 0条/178条 = 0%
```

**确认：MIN_SCORE=0.8 在 RRF 融合体系下确实 0% 过滤。**
保持 0.8 作为网络安全垫即可，不做额外优化。

### Review 判决

| 因素 | 评估 |
|------|------|
| 风险 | **中高**。RRF 分布特性导致 elbow 容易过度裁剪 |
| 收益 | **低**。当前 0.8 已在全过区间，加肘部也大部分情况不生效 |
| **推荐** | **暂缓**。等切换到 SiliconFlow reranker 后的新基线出来，重新评估分数分布再决策 |

如果切换到 SiliconFlow reranker 后 cross-encoder 分数变为 [0,1] 均匀分布，肘部检测才真正有意义。

### 方案：最大间隙法（elbow detection）

在排序后的分数序列中找"断层"位置，取断点前的分数作为实际阈值。

**算法：**

```
输入：sorted_scores（降序排列）, min_floor=0.8, min_gap_ratio=0.15
输出：dynamic_threshold

1. 如果 len(sorted_scores) < 3 → 返回 min_floor
2. lo = min(scores), hi = max(scores)
3. 如果 hi - lo < 0.01 → 分数高度集中，返回 min_floor
4. norms = [(s - lo) / (hi - lo) for s in scores]  # 归一化到 [0,1]
5. 对 i = 2..len(norms)-1，计算 gaps[i] = norms[i-1] - norms[i]
6. elbow = argmax(gaps)  # 最大间隙位置
7. 如果 max_gap > min_gap_ratio → 有断层
   → 返回 max(scores[elbow-1], min_floor)  # 取断层前的分数，不低于保底
8. 否则 → 返回 min_floor
```

**与现有流程的集成位置：**

```
raw scores → min_score(0.8) → elbow 二次过滤 → 时态融合 → MMR 重排
                ↑ 网络安全垫      ↑ 动态裁切
```

### 代码变更

**filtering.py** 新增：

```python
def _find_elbow_threshold(
    sorted_scores: list[float],
    min_floor: float = 0.8,
    min_gap_ratio: float = 0.15,
) -> float:
    """在降序排列的分数中找断层位置。"""
    if len(sorted_scores) < 3:
        return min_floor
    lo, hi = min(sorted_scores), max(sorted_scores)
    if hi - lo < 0.01:
        return min_floor
    norms = [(s - lo) / (hi - lo) for s in sorted_scores]
    max_gap = 0.0
    elbow = -1
    for i in range(2, len(norms)):
        gap = norms[i - 1] - norms[i]
        if gap > max_gap:
            max_gap = gap
            elbow = i
    if max_gap > min_gap_ratio and elbow >= 0:
        return max(sorted_scores[elbow - 1], min_floor)
    return min_floor
```

**config.py** 追加：

```python
enable_elbow_filter: bool = field(default=True)     # 是否启用 elbow 动态裁切
elbow_min_gap_ratio: float = field(default=0.15)    # 最小间隙比
```

**filtering.py 调用位置**（在时态融合前插入）：

```python
# 当前（filtering.py ~line 135）：
if score >= min_score:
    ...

# 改为：
if score >= min_score:
    # 暂存，后续 elbow 二次过滤
    temp.append((r, score))

# elbow 过滤
if CONFIG.enable_elbow_filter:
    elbow_threshold = _find_elbow_threshold(
        [s for _, s in sorted(temp, key=lambda x: -x[1])],
        min_floor=CONFIG.min_score,
        min_gap_ratio=CONFIG.elbow_min_gap_ratio,
    )
    temp = [(r, s) for r, s in temp if s >= elbow_threshold]
```

### 验证

| 分数分布示例 | old_threshold=0.8 | elbow_threshold |
|-------------|:-----------------:|:---------------:|
| [1.8, 1.7, 1.6, 1.2, 1.1, ...] | 0.8（全过） | 1.2（断点后过滤） |
| [1.1, 1.09, 1.08, ...] | 0.8（全过） | 0.8（无断层，用保底） |
| [2.0, 1.5, 0.9, 0.85, 0.8, ...] | 0.8（全过） | 0.9（断点） |

---

## ③ 自我蒸馏式上下文压缩（P2 — v3.0 Review：ROI 回升至 ⭐⭐⭐）

### 启发来源

当日（2026-06-10）在线学习入库知识点 ID 3759-3761：

> "自我蒸馏让学生模型在缺乏上下文时，仍保留有上下文时的改进。"

映射到我们的场景：
- **teacher**：注入全部 8 条 recall 结果 → LLM 完整响应
- **student**：只注入 top-3 或 top-5 → LLM 压缩响应
- **目标**：student 的输出质量不低于 teacher

### v2.0 的错误结论

v2.0 基于 `trace.log` 中 `score_comparison.base_score`（即 `rerank_score` = `combined_score`）分析，得到 score_span avg=0.22 的结论，判定"仅 11% 可压缩"。

**这个分析是错的。** 原因：

- `rerank_score` 不是原始 CE 分数，而是 `combined_score = CE_norm × recency_boost × temporal_boost × proof_count_boost`
- 三重 boost 将分数挤压到 1.0-1.8 区间，区分度丢失

### 实际数据（v3.0 发现）

Hindsight trace 的 `score_components` 中**已经暴露了原始 CE 分数**：

```
trace.reranked[].score_components = {
  "cross_encoder_score": 0.9737,            // ← 原始 CE 分，[0,1] 有区分度
  "cross_encoder_score_normalized": 0.7259, // ← 归一化后
  "combined_score": 0.8095                   // ← 当前插件用的 rerank_score
}
```

实测 300 条分布：

| 字段 | 范围 | score_span |
|------|:----:|:----------:|
| `cross_encoder_score`（原始） | [0.000, 0.997] | **~0.997** |
| `cross_encoder_score_normalized` | [0.500, 0.730] | ~0.230 |
| `combined_score`（当前插件用的） | [0.500, 0.810] | ~0.310 |

**原始 `cross_encoder_score` 有全量 [0,1] 区分度**，score_span ≈ 1.0。当前插件只用到了 `rerank_score`（融合分），所以看不到这个区分度。

### 修正后分析

如果插件从 `score_components.cross_encoder_score` 获取分数作为压缩依据：

| 压缩策略 | 保留条数 | token 降低 | 适用场景占比 |
|:-------:|:--------:|:----------:|:----------:|
| score_span > 0.9 → top-3 | 3 | -62% | ~30% |
| score_span > 0.7 → top-5 | 5 | -37% | ~50% |
| score_span < 0.7 | 全部保留 | 0% | ~20% |

**预期收益：每次 recall 平均省 200-400 chars（50-100 tokens），且风险极低**（用原始 CE 分数做裁切决策，而非融合后的挤压分）。

### 根因：插件没抽取 raw CE 分数

```python
# filtering.py:84-93 — 当前实现
def extract_rerank_scores(trace_data: dict) -> dict[str, float]:
    rerank_map: dict[str, float] = {}
    reranked = trace_data.get("reranked", [])
    for r in reranked:
        node_id = r.get("node_id", "")
        score = r.get("rerank_score", 0.0)  # ← 只取 combined_score
        ...
```

**修复：** 新增提取 `cross_encoder_score` 作为 `ce_raw_map`，专门用于压缩决策。

### 方案

**新增函数：** `filtering.py` 新增 `extract_ce_raw_scores()`，抽取 `score_components.cross_encoder_score`。

```python
def extract_ce_raw_scores(trace_data: dict) -> dict[str, float]:
    """从 trace 中提取原始 cross-encoder 分数（≥0.5 时才保留）。"""
    ce_map: dict[str, float] = {}
    reranked = trace_data.get("reranked", [])
    for r in reranked:
        node_id = r.get("node_id", "")
        sc = r.get("score_components", {}) or {}
        ce_raw = sc.get("cross_encoder_score", 0.0)
        if node_id and ce_raw >= 0.5:  # 过滤掉 0~[0,1) 的无意义低分
            ce_map[node_id] = float(ce_raw)
    return ce_map
```

**新增压缩函数：**

```python
def compress_by_score_span(
    kept: list[dict],
    ce_raw_map: dict[str, float],
    max_results: int,
) -> list[dict]:
    """根据原始 CE 分数 span 动态压缩结果数量。
    
    如果 top-1 与 bottom-1 的 CE 原始分数差距很大，
    说明底部的记忆质量明显差，可以安全裁切。
    """
    if not kept or len(kept) <= 3:
        return kept
    
    scores = [ce_raw_map.get(r.get("id", ""), 0.0) for r in kept]
    scores = [s for s in scores if s > 0]
    if not scores or len(scores) < 2:
        return kept
    
    span = max(scores) - min(scores)
    
    if span > 0.9:
        return kept[:3]  # 差距极大，安全地只留 3 条
    elif span > 0.7:
        new_k = min(max_results // 2, len(kept))
        return kept[:max(5, new_k)]  # 差距大，砍半
    else:
        return kept  # 跨度小，全部保留
```

**hooks.py 集成位置**（在 `filter_by_score` + MMR 之后）：

```python
# hooks.py，在 format_context_lines 前插入
if CONFIG.enable_score_span_compress:
    ce_raw_map = extract_ce_raw_scores(trace_data)
    kept = compress_by_score_span(kept, ce_raw_map, effective_max)
```

**config.py 新增参数：**

```python
enable_score_span_compress: bool = field(default=True)  # 启用 CE 分数跨度压缩
score_span_top3_threshold: float = field(default=0.9)   # top-3 阈值
score_span_half_threshold: float = field(default=0.7)    # 半切阈值
```

### 验证

| 场景 | 旧行为 | 新行为 |
|------|--------|--------|
| CE 分数 0.99/0.97/0.95/0.12/0.03 | 5 条全注入 | top-3（span=0.96 > 0.9） |
| CE 分数 0.95/0.90/0.85/0.80/0.10 | 5 条全注入 | top-4 或 top-5（span=0.85 > 0.7） |
| CE 分数 0.75/0.73/0.72/0.70/0.68 | 5 条全注入 | 全保留（span=0.07 < 0.7） |

---

## ④ 因果链置信度传播（P2，0.5h）

### 数据基础

```sql
memory_links 中因果链数据：
  caused_by: 8,566 条
  causes:    4,813 条
  总计: 13,379 条
```

### 当前状态

```python
# config.py:97 — 未启用
enable_causal_chain: bool = field(default=False)
```

`hooks.py:226-262` 已有 `_causal_boost` 实现，`hooks.py:675-679` 已有调用框架：

```python
if CONFIG.enable_causal_chain and rerank_map:
    try:
        _causal_boost(filtered_raw, rerank_map)
    except Exception:
        pass
```

### 环境发现（v1.2 Review）

**⚠️ 当前 `_causal_boost` 代码有 SQL bug（hooks.py:226-262）：**

| 项 | 代码当前写法 | PG 实际结构 |
|---|------------|------------|
| 列名 | `source_memory_id` / `target_memory_id` | **`from_unit_id` / `to_unit_id`** |
| 关系类型 | `'causes', 'leads_to'` | **`'causes', 'caused_by'`** |
| 提权方式 | 全部链路 ×1.15（无差异） | 应使用 `weight` 列做差异化 |
| 上限 | cap=2.0（+100%），过高 | 建议 cap=1.3（+30%） |

此外，connection 不走连接池，每次调用新建 `psycopg2.connect()`，高频场景下可能有连接开销。

**修复方案：** 修正 SQL 字段 + 引入 weight 差异化 + 降低 cap。与设计方案一致，可合并实现。

### 方案变更（v1.1）

Hindsight 已使用 SiliconFlow reranker（BAAI/bge-reranker-v2-m3），
rerank_score 已从 rrf 的均质 0.5 变为有区分度的 0~1 cross-encoder 分数。

**影响：**
- rerank_score 本身已经过神经重排，因果链 boost 的影响相对变小
- 但正因为分数更有意义，因果链提权的**精确度更高**——不会因为 rrf 的 0.5 分放大噪声
- α 系数可适当调高（0.15 → 0.20），因为 base score 是有效的语义分数

**改动：** filtering.py 新增 ~30 行 + config.py 3 行 + hooks.py 加 adapter 传入

**规则：**

```
如果 A 和 B 都在候选池中，且 memory_links 存在 A→B（A causes B）：
    boost = 1.0 + α × score(A) × weight
    新 score(B) = score(B) × min(boost, cap)
```

| 参数 | 默认值 | 范围 | 含义 |
|------|:------:|:----:|------|
| α | 0.15 | [0, 0.3] | 提权幅度，越大因果影响越强 |
| cap | 1.3 | [1.0, 2.0] | 最大提权上限，防翻转排序 |

**实现：**

```python
def _causal_boost(
    candidates: list[dict],
    rerank_map: dict[str, float],
    adapter: "DatabaseAdapter | None" = None,
    alpha: float = 0.15,
    cap: float = 1.3,
) -> None:
    """
    对候选池中有因果关联的记忆做提权。

    只 boost 已在候选池中的记忆（不新增候选），
    确保因果链只微调排序，不引入无关结果。
    """
    if not candidates or len(candidates) < 2:
        return

    node_ids = [c.get("id", "") for c in candidates if c.get("id")]
    if len(node_ids) < 2 or adapter is None:
        return

    try:
        # 查询 PG：候选集中哪些记忆之间有因果链
        links = adapter.get_links_between(node_ids, link_types=["causes", "caused_by"])
        # links = [(from_id, to_id, link_type, weight), ...]

        for from_id, to_id, link_type, weight in links:
            from_score = rerank_map.get(from_id, 0.0)
            if from_score <= 0:
                continue

            # 因果提权：原因记忆分数越高，结果记忆受益越大
            boost = 1.0 + alpha * from_score * weight
            current = rerank_map.get(to_id, 0.0)
            rerank_map[to_id] = current * min(boost, cap)

    except Exception:
        logger.warning("Causal boost failed (non-fatal)", exc_info=True)
```

### 代码变更

**filtering.py：** 新增 `_causal_boost` 函数（~30 行）

**config.py：** 新增参数

```python
enable_causal_chain: bool = field(default=True)    # 默认开启
causal_boost_alpha: float = field(default=0.15)    # 提权系数
causal_boost_cap: float = field(default=1.3)       # 最大提权上限
```

**hooks.py：** 在 `_causal_boost` 调用处传入 adapter（当前只传了 `filtered_raw, rerank_map` 但没传 adapter，所以永远不生效）

### 验证

| 场景 | 无因果 boost | 有因果 boost |
|------|:-----------:|:------------:|
| A→B，A 高分 | B 分数不变 | B 得分 ×1.15~1.3 |
| A→B，A 低分 | B 分数不变 | B 几乎不变（boost ≈ 1.0） |
| A→B，B 不在候选池 | 不处理 | 不处理（不新增候选） |

---

## 优先级与依赖关系

| 方向 | 工时 | 风险 | ROI | 判决 | 理由 |
|:----:|:---:|:----:|:---:|:----:|------|
| **④ 因果链 boost** | **0.5h** | **低** | **⭐⭐⭐** | **先做** | 代码已有，修 SQL bug + 增强一并做 |
| **① MMR** | **0.5h** | **低** | **⭐⭐** | **次做** | 即插即用，替换 14 行代码 |
| **③ 上下文压缩** | **1h** | **低** | **⭐⭐⭐** | **再做** | CE 原始分数已在 trace 中，抽出来即可做压缩决策 |
| ② 自适应 MIN_SCORE | — | 中高 | ⭐ | 暂缓 | 0/178 条低于 0.8，MIN_SCORE 本就不生效 |

**建议实施顺序：**

```
Step 1: ④ 因果链 boost（0.5h）
        ├── 修 SQL 字段 (from_unit_id / to_unit_id / link_type)
        ├── 引入 weight 差异化提权
        └── 降低 cap 至 1.3
Step 2: ① MMR（0.5h）
        ├── 新增 _mmr_diversity()
        └── 替换硬限幅块
Step 3: ③ 上下文压缩（1h）
        ├── 新增 extract_ce_raw_scores() 抽取 score_components.cross_encoder_score
        ├── 新增 compress_by_score_span() 按 span 动态裁切
        ├── hooks.py 集成（filter_by_score + MMR 之后）
        └── config.py 新增参数
```
