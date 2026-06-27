---
name: clustering-analysis
description: 因果链聚类分析 V3 — HDBSCAN 对 Hindsight 记忆做语义聚类 + 实体挂靠 + LLM/正则因果链检测，增量写入 + 审计日志
version: 2.3.0
related_skills: [hindsight-memory, knowledge-navigation]
---

# 因果链聚类分析 V3

> 入口脚本：`~/.hermes/scripts/clustering-analysis-v3/src/clustering_analysis/cli.py`
> 调用方式：`cd ~/.hermes/scripts/clustering-analysis-v3 && python3 -m clustering_analysis.cli run [参数]`

## 管线概览

聚类分析是 **纯增量系统**，每次运行只写入新增和修正数据，**从不删除**已有实体和因果链。

```
Phase 1: 拉取数据（memory_units + embedding + 已有实体）
    ↓
Phase 2: 2 轮聚类
    ├── Round 1: 无实体记忆 → 挂靠已有实体（余弦相似度 ≥ 0.75）
    └── Round 2: HDBSCAN 语义聚类（单轮，替代原 3 轮 DBSCAN）
    ↓
    实体合并（两步法：新实体间 m² + 新→已有 m×k，避免全量 n×n）
    因果链增强（已有实体新增成员后重新检测，LLM 搭便车 / 正则双路径）
    ↓
Phase 3: 写入数据库（4 轮批量 UPSERT，永不删除）
    ├── 剥离 member_ids 过程数据（减少 ~90% 管道传输量）
    ├── 4 轮 execute_values → commit
    └── 审计日志（JSONL 追加到 clustering_audit.log）
    ↓
Phase 4: 批量更新 embedding（一次 execute_values 完成）
```

## 参数说明

| 参数 | 作用 | 说明 |
|------|------|------|
| `--apply` | 实际写入 PG | 不加则 dry-run，只出报告不改数据 |
| `--dry-run` | 试运行模式 | 等价于 `--apply=false`，只出计划 |
| `--skip-entity` | 跳过实体提取 | 实体提取需调 LLM 很慢，调试时跳过 |
| `--config` | 配置文件路径 | 默认 `config/default.yaml` |
| `--cleanup` | 保留全部数据 | 当前为空操作，实体和因果链永不删除 |
| `--force` | 跳过确认提示 | 配合 --cleanup 使用 |

## 常用调用方式

> ⚠️ 当前 Typer 单命令模式下，`python3 -m clustering_analysis.cli run --apply` 可能被解析为无效参数。cron 中推荐使用已修复的 `scripts/cron_wrapper.sh`，它直接调用 `run()` 函数绕过 Typer 参数解析。

```bash
# 0. cron/no_agent 完整管线（质量报告 → 超长记忆治理 → MinHash LSH 去重 → 聚类 → 飞书通知）
cd ~/.hermes/scripts/clustering-analysis-v3 \
  && source ~/.hermes/.env 2>/dev/null \
  && CLUSTERING_DB_URL="$CLUSTERING_DB_URL" bash scripts/cron_wrapper.sh

# 0b. 仅重跑聚类步骤（跳过质量报告、超长治理、LSH、通知）
cd ~/.hermes/scripts/clustering-analysis-v3 \
  && source ~/.hermes/.env 2>/dev/null \
  && CLUSTERING_DB_URL="$CLUSTERING_DB_URL" bash scripts/cron_wrapper.sh --skip-steps "1,2,3,5"

# 0c. 直接调用 Python 函数（绕过 Typer 参数解析）
cd ~/.hermes/scripts/clustering-analysis-v3 && source ~/.hermes/.env 2>/dev/null && \
PYTHONPATH="src:${PYTHONPATH:-}" CLUSTERING_DB_URL="$CLUSTERING_DB_URL" python3 -c '
from clustering_analysis.cli import run
run(apply=True, dry_run=False, cleanup=False, force=True, skip_entity=False, config_path="config/default.yaml")
'

# 1. 干跑看看结果（默认）
cd ~/.hermes/scripts/clustering-analysis-v3 && python3 -m clustering_analysis.cli run

# 2. 正常写入（增量模式）
cd ~/.hermes/scripts/clustering-analysis-v3 && python3 -m clustering_analysis.cli run --apply

# 3. 跳过实体提取（LLM 慢，调试用）
cd ~/.hermes/scripts/clustering-analysis-v3 && python3 -m clustering_analysis.cli run --skip-entity

# 4. 指定配置文件
cd ~/.hermes/scripts/clustering-analysis-v3 && python3 -m clustering_analysis.cli run --apply --config /path/to/custom.yaml
```

## 技术要点

### 聚类算法
- **HDBSCAN** 替代原 3 轮 DBSCAN（2026-06-04 升级）
- 自动根据数据密度确定聚类，无需多 eps 扫描
- `min_cluster_size` 由配置的 `min_samples` 控制（默认 3）
- 聚类方式 `leaf`（细粒度划分）
- 输出 Silhouette 评分，持久化到审计日志

### LLM 搭便车因果链（2026-06-04 新增）

大簇（≥ `min_llm_size=10`）走 LLM 路径提取因果链，小簇走正则路径。

**调用函数**：`call_llm_for_entity_with_causal(texts)` — 一次 LLM 调用同时获取实体名和因果对（零额外 input token）。

**JSON 解析兜底**（`_parse_llm_json_response`）：
1. 直接 `json.loads` 解析
2. Markdown 代码块中提取
3. Raw JSON 对象正则提取

**因果对校验**：越界过滤、自环过滤、`seen_pairs` 去重、非 dict 跳过。

**reason 分段**：在"导致/引发/造成"等因果词处自动分段，使富化文本的 `[因果来源]` 和 `[因果结果]` 都有内容。

### 正则因果链检测（小簇 fallback）

- 5 个 pattern 匹配："X导致Y"、"X失败"、"因为X，所以Y"、"根因是X"
- `NOISE_WORDS` 过滤（~50 个通用词）
- 置信度门槛 0.6
- `_detect_causal_in_group` 内 O(n²) 全量对匹配

### 跨运行去重（2026-06-04 新增）

`fetch_all_links(bank_id)` 加载 DB 已有链接到 `seen_pairs` 集合，避免多次 `--apply` 重复写入同一因果对。

### 审计日志（2026-06-04 新增）

每次 `--apply` 成功后，`_log_clustering_run()` 将运行元数据追加到审计文件：

**路径**：`~/.hermes/plugins/knowledge-navigation/clustering_audit.log`

**记录字段**：
| 字段 | 说明 | 来源 |
|------|------|------|
| timestamp | 运行时间 | `datetime.now(timezone.utc)` |
| total_units | 总记忆数 | `len(embeddings)` |
| processed_units | 已处理记忆数 | `processed.sum()` |
| noise_units | 噪声数 | `n - processed_units` |
| entity_count | 新增实体数 | `len(entity_write_plan)` |
| cluster_count | HDBSCAN 实际簇数 | `len(set(r_labels) - {-1})` |
| silhouette | Silhouette 评分 | `run_hdbscan_clustering` 返回值 |
| memory_links | 因果链总数 | `len(memory_link_plan)` |
| duration_sec | 总耗时 | `time.time() - run_start` |

### 实体合并（两步法，避免 O(n²) 矩阵）

| 步骤 | 算法 | 矩阵大小 | 阈值 |
|------|------|---------|------|
| Step A | `merge_similar_entities` | 新实体间 m × m | 0.88 |
| Step B | `match_new_to_existing` | 新→已有 m × k | 0.85 |

- 基于实体质心（成员嵌入均值）的余弦相似度
- Union-Find 处理传递相似关系
- 被合并实体的 `entity_id` 在写入前通过 `while` 循环重新映射为保留实体的 ID

### 数据写入（4 轮批量 UPSERT，永不删除）

| 轮次 | 表 | 冲突处理 |
|------|----|---------|
| 1 | `entities` | `ON CONFLICT DO UPDATE`（同名覆盖 metadata + 递增 mention_count） |
| 2 | `unit_entities` | `ON CONFLICT DO NOTHING`（仅新增，不更新） |
| 3 | `memory_units.text` | 追加实体标记 + 富化文本（合并为单次 UPDATE） |
| 4 | `memory_links` | `ON CONFLICT DO UPDATE weight = GREATEST(...)` |

所有写入使用 `psycopg2.extras.execute_values` 批量操作，单 `commit` 完成。

### Embedding 更新
- 所有富化文本的 embedding 通过 `batch_update_embeddings` 一次 `execute_values` 完成
- 写入失败降级处理（打印警告，不阻塞流程）
- LLM 超时从 60s 提升到 120s（2026-06-04 调整）

### 因果链增强（已有实体新增成员）

- 实体在 Round 1 获得新成员后，触发该实体的全量因果链重新检测
- `MAX_FULL_MEMBERS=50`，大实体采样旧成员（`MAX_SAMPLE_OLD=30`）
- 外部成员文本通过 `fetch_unit_texts_batch` 批量拉取
- 结果写入 `memory_link_plan` 和 `enriched_texts`

### Phase 3.5 自动标记（mark_memory）

2026-06-14：CLI 已补全，所有子命令可直接使用。默认 `mark` / `unmark` 为 dry-run，加 `--apply` 写入。

脚本位置：`~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py`

```bash
# 搜索记忆单元
CLUSTERING_DB_URL="postgresql://..." \
  python3 ~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py \
  search <关键词> --limit 20

# 标记一条错误记忆（dry-run 预览，加 --apply 写入）
CLUSTERING_DB_URL="postgresql://..." \
  python3 ~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py \
  mark <unit_id> 错误 "可选说明"

# 实际写入
CLUSTERING_DB_URL="postgresql://..." \
  python3 ~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py \
  mark <unit_id> 错误 "可选说明" --apply

# 移除标记（dry-run 预览，加 --apply 写入）
CLUSTERING_DB_URL="postgresql://..." \
  python3 ~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py \
  unmark <unit_id>

# 实际移除
CLUSTERING_DB_URL="postgresql://..." \
  python3 ~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py \
  unmark <unit_id> --apply

# 输出标记状态
CLUSTERING_DB_URL="postgresql://..." \
  python3 ~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py \
  check <unit_id>
```

| 标记类型 | 对 recall 的影响 |
|----------|-----------------|
| `错误` / `作废` / `可疑` / `待验证` | 被 `exclude_marked()` 排除 |
| `已解决` | 不排除 |

### 关键配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `min_samples` | 3 | HDBSCAN 最小簇大小 |
| `max_group_size` | 20 | 超过此大小的簇跳过（防 OOM） |
| `min_llm_size` | 10 | 大簇阈值，≥此值走 LLM 因果链 |
| `epsilon_range` | `[0.15, 0.20, ...]` | 保留兼容，HDBSCAN 不再使用 |

### 性能优化
- 倒排索引 + co-occurrence 统计替代 N² 矩阵（实体重叠度）
- 1D 统计量前置计算（信息密度）
- 预取文本字典消除 `apply_to_db` 内部重复查询
- `json.loads` 替代 `ast.literal_eval` 解析 embedding
- GPU 加速（torch.cuda）自动检测

## 相关文档

参考文档散落在以下 skill 中，如需查阅：

- `knowledge-navigation/references/clustering-analysis-results-20260520.md`
- `hindsight-memory/references/clustering-analysis-v3-dryrun-20260521.md`
- `hindsight-memory/references/causal-chain-clustering-v3.md`
