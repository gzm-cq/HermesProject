# 数据飞轮优化 SPEC 实施计划

> **审计状态**：✅ 已完成全量代码交叉审计（2026-06-28）
> **代码实施进度**：P0 ~ P3-4 全部已实施，仅 P3-5 ~ P3-10 待实施
> **原则**：不修改 Hermes Gateway 源码，所有优化在子项目/插件层面实施
> **Feature Flag**：每个优化项都有独立开关，出问题可一键回退
> **权威设计文档**：`docs/architecture/flywheel-optimization-report.md`

---

## 一、审计确认的问题清单

> 以下问题基于代码实测核实。行号以函数名定位，不依赖硬编码行号。

### ✅ 已验证属实的问题

| # | 项目 | 问题 | 验证位置 |
|---|------|------|---------|
| 1 | knowledge-tree-builder | `call_llm()` 硬编码 `time.sleep(0.3)` 限速 | `llm/client.py` `time.sleep(0.3)` |
| 2 | knowledge-tree-builder | `_dedup_single()` O(N*M) 线性扫描去重 | `phase/admit.py` `_dedup_single()` 内层 `for existing in existing_vectors` |
| 3 | knowledge-navigation | Skill Matcher 全量扫描 skills 目录 | `skill_matcher.py` `ensure_index()`（已支持 mtime 增量，不再瓶颈） |
| 4 | knowledge-navigation | 跨域去重阈值 0.65 偏低 | `hooks.py` `cross_domain_dedup(threshold=0.65)` |
| 5 | clustering-analysis-v3 | HDBSCAN `min_samples` 固定为 3，无自适应 | `config/default.yaml:6` `min_samples: 3` |

### ⚠️ 待进一步验证的问题（实施时确认）

| # | 项目 | 问题 |
|---|------|------|
| 6 | knowledge-tree-builder | Phase 3 和 Phase 4 重复计算 embedding（Phase 3 cache 在 Phase 4 前被清理） |
| 7 | knowledge-tree-builder | 矛盾检测条件提取正则覆盖率低（`_extract_condition()` 最初只匹配"在...上/下/时/后/中"） |
| 8 | knowledge-tree-builder | 领域判断缓存以文章标题为 key（`_p4_domains`，同名 README.md 会覆盖） |
| 9 | knowledge-tree-plugin | 后台队列 maxsize=100 无重试机制（有 warning 日志 `kt_queue_full`） |
| 10 | memory-cleanup | hindsight 分类关键词标签被丢弃（`_retain()` 最初只传 content） |
| 11 | clustering-analysis-v3 | dedup_memories O(n^2) Jaccard 比较（batch_size=500，~125k 次比较） |

> 注：问题 7~11 已在后续优化中修复（见下方实施状态），此处保留作为历史记录。

---

## 二、优化项优先级与实施顺序

### P0：性能优化（消费端效率）

| 编号 | 优化项 | 项目 | 预期效果 | 预估工作量 | Feature Flag | 状态 | 代码证据 |
|------|--------|------|----------|-----------|-------------|------|---------|
| P0-1 | Skill Matcher 关键词预筛选 + LLM 精排两级 | knowledge-navigation | 延迟 ~85%（~3s→~500ms），LLM token ~85% | 1 天 | `KN_SKILL_KEYWORD_PRESCREEN` | ✅ 已实施 | `skill_matcher.py` `_keyword_prescreen()`, `config.py` `kn_skill_keyword_prescreen: True` |
| P0-2 | 去重下推 pgvector HNSW 索引 | knowledge-tree-builder | 去重性能 ~480x 提升 | 0.5 天 | `KB_DEDUP_PGVECTOR` | ✅ 已实施 | `database.py` `find_nearest_neighbors()`, `admit.py` `_dedup_single()` db_adapter 分支 |
| P0-3 | LLM 调用合并（提取+领域判断一次完成） | knowledge-tree-builder | LLM 调用量 -50%，建树速度 +40% | 0.5 天 | `KB_MERGED_DOMAIN` | ✅ 已实施 | `merged.py` `analyze_and_split()` 返回三元组，`run.py` Phase 4 优先读 merged 输出 |

### P1：质量与闭环

| 编号 | 优化项 | 项目 | 预期效果 | 预估工作量 | Feature Flag | 状态 | 代码证据 |
|------|--------|------|----------|-----------|-------------|------|---------|
| P1-1 | Token 预算守门 | knowledge-navigation | Context 膨胀控制 | 1 天 | `KN_ENABLE_TOKEN_BUDGET` | ✅ 已实施 | `filtering.py` `apply_token_budget()`, `hooks.py` 第 1061 行调用点, `config.py` 默认 True |
| P1-2 | 跨域去重改为降权（不删除） | knowledge-navigation | 避免误删语义近似的高质量知识 | 0.5 天 | `KN_CROSS_DEDUP_ACTION` + `KN_CROSS_DEDUP_DEMOTE_FACTOR` | ✅ 已实施 | `filtering.py` `cross_domain_dedup()` 支持 `action="demote"`, 默认 demote |
| P1-3 | HDBSCAN 自适应参数 | clustering-analysis-v3 | 小/大数据集分簇质量提升 | 0.5 天 | `CA_HDBSCAN_ADAPTIVE` | ✅ 已实施 | `clustering.py` `adaptive_hdbscan_params()` 分档阶梯函数, 默认开启 |
| P1-4 | 因果链检测增量化 | clustering-analysis-v3 | 避免旧-旧关系遗漏 | 0.5 天 | `CLUSTERING_CAUSAL_INCREMENTAL` | ✅ 已实施 | `clustering.py` `_detect_causal_in_group_incremental()`, 默认 `causal_incremental=True` |
| P1-5 | MEMORY compress 质量规则加强 | memory-cleanup | 防止 USER.md 过度压缩 | 0.5 天 | `MEMORY_CLEANUP_COMPRESS_STRICT_MODE` | ✅ 已实施 | `classifier.py` `validate_compress_quality()` strict 模式, USER 阈值 0.25, 默认严格模式 |
| P1-6 | Hindsight 关键词回填 | memory-cleanup | 修复标签丢弃问题 | 1 天 | `MEMORY_CLEANUP_KEYWORD_BACKFILL` | ✅ 已实施 | `classifier.py` `backfill_hindsight_keywords()`, `memory_store.py` `_retain()` 传递 tags, 默认开启 |

### P2：质量治理闭环

| 编号 | 优化项 | 项目 | 预期效果 | 预估工作量 | 状态 | 代码证据 |
|------|--------|------|----------|-----------|------|---------|
| P2-1 | 入库门控增强（矛盾检测+准入门控白名单） | knowledge-tree-builder + plugin | 源头减少低质量数据 | 1 天 | ✅ 已实施 | `admit.py` `_CONDITION_PATTERNS` 12 种条件匹配, `_is_whitelisted()`, `_detect_conflicts()` 两步判定 |
| P2-2 | 记忆闭环 Phase A：memory_use_log | knowledge-navigation | Hindsight 使用可观测 | 1 天 | ✅ 已实施 | `use_log.py` `UseLogger` 类 + PG `knowledge_use_log` 表, `recall.py` 已接 |
| P2-3 | Skill index mtime 增量更新 | knowledge-navigation | 首次构建后增量扫描 | 0.5 天 | ✅ 已实施 | `skill_matcher.py` `_update_incremental()` 新增/修改/mtime/删除全量处理, 默认 `skill_index_incremental=True` |
| P2-4 | 领域缓存改用路径 hash | knowledge-tree-builder | 避免同名文件覆盖缓存 | 0.5 天 | ✅ 已实施 | `run.py` `_domain_cache_key()` MD5[:12] path_hash, 默认开启, 含旧缓存自动迁移 |
| P2-5 | dedup_memories 用 MinHash | clustering-analysis-v3 | O(n) 替代 O(n^2) Jaccard | 0.5 天 | ✅ 已实施 | `dedup.py` MinHash LSH 默认启用, 有 datasketch fallback 到 Jaccard |

### P3：进阶能力（远期）

| 编号 | 优化项 | 项目 | 预期效果 | 默认状态 | 状态 | 代码证据 |
|------|--------|------|----------|---------|------|---------|
| P3-1 | 自动反馈飞轮（RAGAS faithfulness 评估） | recall-eval 子项目 | Recall 质量可量化 | 关闭 | ✅ 已实施 | `scripts/recall-eval/` 完整子项目: `cli.py`, `core/runner.py`, `core/metrics.py`, `pyproject.toml` |
| P3-2 | 全库语义质量评分 | clustering-analysis-v3 | 首次获得全库质量全景 | 关闭 | ✅ 已实施 | `core/quality.py` LLM 3 维评分, `config.py` `enable_quality_scoring` |
| P3-3 | 数据血缘记录 | knowledge-tree-builder + plugin | 出问题可回溯 | 关闭 | ✅ 已实施 | `commands/lineage.py`, `run.py` 入库时记录 `source_file`, Feature Flag 控制 |
| P3-4 | 冷记忆自动淘汰 + 高频回升 L2 | memory-cleanup | 记忆自然生命周期 | 关闭 | ✅ 已实施 | `core/lifecycle.py` `_estimate_last_access()`, 基于 recall_count + faithfulness 的淘汰/回升逻辑 |
| P3-5 | 质量治理 Phase C（自动修复） | clustering-analysis-v3 + builder | 基于评分自动修复 | 关闭（依赖 P3-2） | 待实施 | 评分有了（P3-2），但基于评分的自动修复（标记/合并/改写）尚未实现 |
| P3-6 | 跨系统一致性检查 | 新建 cron 脚本 | 消除跨系统矛盾 | 关闭 | 待实施 | 需新建子项目 |
| P3-7 | Embedding 新鲜度检查 | knowledge-tree-builder | 保证 recall 精度 | 关闭 | 待实施 | 需新建 cron 脚本 |
| P3-8 | 知识图谱增强（三元组抽取） | 新建子项目 | 跨域关系召回 | 关闭 | 待实施 | 需新建子项目 |
| P3-9 | 时态感知增强（temporal_tag） | knowledge-tree-plugin | 召回时效性提升 | 关闭 | 待实施 | 需开发 |
| P3-10 | 缓存文件统一管理 | knowledge-tree-builder | 3 类缓存统一到 `.kb_cache/` | 关闭 | 待实施 | 需重构缓存路径 |

> P3-1 ~ P3-4 代码已实施，默认关闭需手动启用。P3-5 ~ P3-10 为剩余的真正待实施项。
> 完整设计见 `docs/architecture/flywheel-optimization-report.md`。

---

## 三、P0 详细设计

### P0-1：Skill Matcher 关键词预筛选 + LLM 精排两级 ✅ 已实施

> 与 Report 的差异：Report 推荐 bge-m3 embedding 预筛选（~10ms，Top-15），实际按关键词方案实施。理由：skill name 是精确标识符，关键词比 embedding 更可靠（用户验证 embedding 效果差）。方案验证：代码已实现，待 benchmark 确认实际效果。

**现状**：
- 每次 pre_llm_call 把 ~345 个 skill 的 name+description 全部发给 LLM
- Prompt 很长（~30k chars / ~8k tokens），延迟 ~3s

**方案**：两阶段检索（关键词预筛选 + LLM 精排，不用 embedding）

```
Stage 1: 关键词预筛选（<1ms，345 → Top-20）
  → 基于中英文关键词提取（英文单词 + 中文 2-gram，去停用词）
  → 按 skill name/category/description 关键词重叠打分
  → 取 Top-20 进入下一轮

Stage 2: LLM 精排（~500ms，20 → Top-3）
  → 只对 20 个候选发给 LLM，prompt 短很多（~2k chars）
  → Feature Flag: KN_SKILL_KEYWORD_PRESCREEN=true 时启用，false 时走原全量路径
```

**预筛选算法**（实际实现比初始设计多 category 维度）：
1. 从 query 中提取关键词（`_extract_keywords()`，英文单词 + 中文连续汉字 2-gram，停用词过滤）
2. 对每个 skill 计算得分：
   - name 完全匹配：+10 分
   - name 关键词重叠：每个 +5 分
   - category 关键词重叠：每个 +3 分（额外维度）
   - description 关键词重叠：每个 +1 分
3. 按得分降序取 Top-20

**已实施文件**：
- `skill_matcher.py`：`_extract_keywords()`、`_keyword_prescreen()`、`match_skills()` 两级入口
- `config.py`：`kn_skill_keyword_prescreen: bool = True`、ENV `KN_SKILL_KEYWORD_PRESCREEN`

**待完成**：benchmark 验证（见第四章）

---

### P0-2：去重下推 pgvector ✅ 已实施

**现状**：
- `_dedup_single()` 对每条新知识点遍历全库已有向量（O(N*M) 内存扫描）
- `DatabaseAdapter.get_leaf_nodes()` 已存在
- pgvector 扩展已启用（`k_vector VECTOR(1024)` 已存在）

**方案**：下推到 pgvector HNSW 索引做近似最近邻

```sql
CREATE INDEX IF NOT EXISTS idx_knowledge_tree_k_vector
  ON knowledge_tree
  USING hnsw (k_vector vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

SET LOCAL hnsw.ef_search = 200;
WITH nearest AS MATERIALIZED (
  SELECT id, k_vector <=> $1 AS dist
  FROM knowledge_tree
  WHERE k_vector IS NOT NULL AND node_type = 'knowledge_point'
  ORDER BY dist
  LIMIT 10
)
SELECT id, dist FROM nearest WHERE dist < 0.05;  -- 相似度 > 0.95
```

**兼容性策略**：
- Feature Flag `KB_DEDUP_PGVECTOR=true` 时启用，false 时走原内存扫描路径
- 自动检测：Python import 检测 + SQL pg_extension 查询双重检测

**已实施文件**：
- `database.py`：`find_nearest_neighbors()`、`pgvector_extension_available()`、`_ensure_hnsw_index()`
- `admit.py`：`_dedup_single()` 新增 `db_adapter` 参数
- `config.py`：`kb_dedup_pgvector: bool = True`、ENV `KB_DEDUP_PGVECTOR`

---

### P0-3：LLM 调用合并（提取+领域判断一次完成） ✅ 已实施

**现状审计确认**：
- Phase 1+2（merged.py）：每篇文章 1 次 LLM 调用
- Phase 4（run.py）：每篇文章 1 次 LLM 调用（`_llm_domain()`）
- 合计：2N 次 LLM 调用。合并后：N 次，节省 50%

**方案**：在 merged.py 的 prompt 中追加领域判断要求，一次调用输出

```
返回结构：analyze_and_split() 返回三元组 (list[AtomicKnowledge], content_summary, suggested_domain)
```

**兼容性策略**：
- `KB_MERGED_DOMAIN=true` 时启用，false 时走原路径
- suggested_domain 为空时降级为 `_llm_domain()` 兜底

**已实施文件**：
- `merged.py`：`_SYSTEM_PROMPT` 已含领域判断，输出 JSON 含 `suggested_domain`
- `run.py`：Phase 4 优先读 merged 输出
- `config.py`：`kb_merged_domain: bool = True`

---

## 四、性能测试方案

### 基准测试脚本

每个 P0 优化项对应的 benchmark 脚本**尚未编写**：

| 优化项 | Benchmark 指标 | 测试方法 | 状态 |
|--------|---------------|---------|------|
| P0-1 | Skill 匹配延迟 + 准确率 | 100 条测试 query，对比开启/关闭的延迟和匹配结果一致性 | 待完成 |
| P0-2 | 去重速度 + 准确率 | 1000/5000/10000 条知识库，对比内存扫描 vs pgvector 的速度和去重结果一致性 | 待完成 |
| P0-3 | LLM 调用次数 + 建树质量 | 相同输入文章集，对比开启/关闭的 LLM 调用次数和产出的知识点数量/质量 | 待完成 |

### 验收标准

| 优化项 | 验收标准 |
|--------|---------|
| P0-1 | Skill 匹配延迟从 ~3s 降到 < 1s；LLM token 减少 ~85%；匹配准确率（与原全量结果的一致性）≥ 95% |
| P0-2 | 1000 条知识库时，去重速度提升 10x+；10000 条时提升 100x+；去重结果与内存扫描一致性 100%（精确去重） |
| P0-3 | 相同文章数下，LLM 调用次数减少 ~50%；建树质量（知识点数量/类型分布）差异 < 5% |

> P0-1 准确率标准"与原全量结果一致性 ≥ 95%"——原全量 LLM 匹配本身不是 ground truth，此标准为实用下限。

---

## 五、风险与回滚

### 技术风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| pgvector 扩展未启用 | 低 | 高 | import + SQL 双重检测，自动降级内存扫描 |
| 关键词预筛选漏掉语义相关的 skill | 中 | 中 | Top-20 候选池足够宽，LLM 精排弥补；Feature Flag 可关闭回退全量 |
| LLM 合并调用后输出格式不对 | 中 | 低 | 容错解析，suggested_domain 为空时降级 |
| HNSW 索引创建耗时 | 低 | 中 | 异步创建，不阻塞主流程；10K 以下不建索引 |

### 回滚策略

1. **Feature Flag 一键关闭**：每个优化项都有独立开关，配置环境变量即可回退
2. **部署回滚**：`deploy.sh rollback <project>` 一键回滚
3. **数据库变更**：HNSW 索引是增量的，可随时 DROP INDEX
4. **数据安全**：所有优化都不修改已有数据的内容

---

## 六、实施计划总览

### 已完成

```
P0-1 ~ P0-3: ✅ 已实施（待 benchmark 验证）
P1-1 ~ P1-6: ✅ 已全部实施
P2-1 ~ P2-5: ✅ 已全部实施
P3-1 (RAGAS 评估): ✅ 已实施（recall-eval 子项目）
P3-2 (语义质量评分): ✅ 已实施（quality.py）
P3-3 (数据血缘):   ✅ 已实施（lineage.py）
P3-4 (冷记忆淘汰):  ✅ 已实施（lifecycle.py）
```

### 待实施

```
Benchmark 验证（可与其他并行）：约 1 天
  - P0-1 bench: 100 条 query 准确率 + 延迟对比（待完成）
  - P0-2 bench: 不同规模知识库去重速度对比（待完成）
  - P0-3 bench: LLM 调用次数 + 建树质量对比（待完成）

P3-5 ~ P3-10：按需排期
  - P3-5 质量治理自动修复（依赖 P3-2 评分数据）
  - P3-6 跨系统一致性检查
  - P3-7 Embedding 新鲜度检查
  - P3-8 知识图谱增强
  - P3-9 时态感知增强
  - P3-10 缓存文件统一管理
```

---

## 七、修正记录

### 第一轮修正（2026-06-28）

| # | 问题 | 严重程度 | 修正内容 |
|---|------|---------|---------|
| 1 | Token 估算精度不足 | 低 | 增加数字/标点估算，改进正则表达式 |
| 2 | UseLogger 线程安全边界 | 低 | 改用 buffer 复制而非直接引用交换 |
| 3 | 冷记忆淘汰可能误判 | 中 | 增加历史记录关键词和近期信号检测 |
| 4 | MinHash 与 Jaccard 结果可能不一致 | 低 | 增加算法特性说明文档 |
| 5 | 血缘记录版本覆盖 | 低 | 改为 (node_id, version) 作为 key 保留历史版本 |

### 第二轮修正（4 路并行审查后）

| # | 问题 | 严重程度 | 修正内容 |
|---|------|---------|---------|
| 6 | KN_CROSS_DOMAIN_DEDUP_ACTION 环境变量 | MEDIUM | hooks.py 传入 action 和 demote_factor 参数 |
| 7 | enable_quality_scoring 配置 | MEDIUM | quality_score CLI 添加 Feature Flag 检查 |
| 8 | _estimate_last_access 分支 | MEDIUM | 删除 if/else 中完全相同的分支 |
| 9 | backfill_k_vectors 测试 mock 错误 | MEDIUM | 修复测试中 cursor.fetchall 的 mock 设置 |

### 第三轮修正（外部交叉审计后）

| # | 问题 | 严重程度 | 修正内容 |
|---|------|---------|---------|
| 10 | SPEC 状态标注全部错误 | **高** | 代码核实发现 P0~P3-4 全部已实施，仅 6 个 P3 项待实施。更新全表状态列，补充代码证据 |
| 11 | 第一章 3 个行号过时 | 高 | 改用函数名定位 |
| 12 | P0-1 延迟声称 50% 与自身拆解矛盾 | 中 | 修正为"~85%" |
| 13 | P0-3 返回值"第 4 个"错误 | 中 | 修正为三元组 |
| 14 | P1-2 Feature Flag 名称错误 | 中 | 从 `KN_CROSS_DEDUP_MODE` 修正为 `KN_CROSS_DEDUP_ACTION` |
| 15 | P3 cron 项缺少默认开关声明 | 中 | 全部标注"默认关闭" |

> 最大的教训：**SPEC 严重滞后于代码**。P1/P2 被错误地标注为"待实施"，实际已全部实现。下轮审计应先检查代码再写文档。

---

## 八、自审计修正记录

| # | 问题 | 修正 |
|---|------|------|
| 1 | 缺少 Feature Flag 设计 | 每个优化项独立开关 |
| 2 | 缺少性能测试方案 | 第四章（benchmark 仍未编写） |
| 3 | P0-2 未考虑 pgvector 兼容性 | 自动检测 + 降级策略 |
| 4 | P0-1 未明确缓存策略 | 缓存位置、失效策略 |
| 5 | P0-3 描述不够准确 | 2N → N 次 |
| 6 | P0-1 用 embedding 不准确 | 改为关键词预筛选 |
