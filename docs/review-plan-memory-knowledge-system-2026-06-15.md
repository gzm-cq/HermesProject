# 记忆/知识系统全面 Review Plan（多 Agent 并行）

> **文档状态：历史 review 执行计划**  
> 本文记录 2026-06-15 多 Agent 审查的计划，不代表当前状态。修复后最终审计见 `reviews/03-post-fix-audit-2026-06-15.md`。


**目标**：对知识导航插件、知识树插件、知识树建树、聚类分析、自进化/深度学习、每日在线学习、记忆清理、记忆修正进行一次端到端 review，输出可执行问题清单、风险等级、修复建议、验证命令与部署前 gate。

**审查原则**：
1. 只读 review 先行，不直接改代码。
2. 所有结论必须基于源码、配置、cron、DB schema 或真实运行结果。
3. 插件/脚本不是 Hermes 核心源码；优先在插件/脚本/配置层解决。
4. 先查当前状态，不凭记忆判断。
5. 写入类命令必须 dry-run 或测试库先行。

---

## 0. 已验证范围

源码根目录：`/mnt/d/HermesProject`

| 模块 | 源码路径 | 部署路径 | 服务重启 | 规模 |
|---|---|---|---|---:|
| 知识导航插件 | `plugins/knowledge-navigation` | `/root/.hermes/plugins/knowledge-navigation` | `hermes-gateway.service` | 20 py / 4 tests / 4748 LOC |
| 知识树插件 | `plugins/knowledge-tree-plugin` | `/root/.hermes/plugins/knowledge-tree-plugin` | `hermes-gateway.service` | 19 py / 5 tests / 3018 LOC |
| 知识树建树 | `scripts/knowledge-tree-builder` | `/root/.hermes/scripts/knowledge-tree-builder` | 无 | 54 py / 19 tests / 11141 LOC |
| 聚类分析 v3 | `scripts/clustering-analysis-v3` | `/root/.hermes/scripts/clustering-analysis-v3` | 无 | 23 py / 4 tests / 4916 LOC |
| 自进化/深度学习 | `scripts/self-evolving` | `/root/.hermes/scripts/self-evolving` | 无 | 28 py / 4 tests / 3984 LOC |
| 每日在线学习 | `scripts/daily-learn` | `/root/.hermes/scripts/daily-learn` | 无 | shell |
| 记忆清理 | `scripts/memory-cleanup` | `/root/.hermes/scripts/memory-cleanup` | 无 | 22 py / 6 tests / 3464 LOC |
| 记忆修正 | `scripts/clustering-analysis-v3/scripts/mark_memory.py` + navigation filtering | 随 clustering 部署 | 无 | 已纳入聚类/导航 |

当前 Hermes cron 已验证有 8 个任务；相关任务：
- `聚类分析每周跑`：`clustering-weekly.sh`，workdir `/root/.hermes/scripts/clustering-analysis-v3`，最近状态 error。
- `memory-cleanup-daily-dryrun`：`memory-cleanup-daily.sh`，workdir `/root/.hermes/scripts/memory-cleanup`，最近状态 ok。
- `知识树维护每日`：`knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh`，最近状态 ok。
- `每日在线学习`：`daily-learn/daily_learn.sh`，最近状态 ok。
- `每周深度研究-知识树学习`：agent job，最近状态 ok。
- `知识导航评估基线`：`knowledge-navigation-baseline.sh`，最近状态 ok。

---

## 1. 多 Agent 并行分工

### Agent A：插件链路审查（知识导航 + 知识树插件）

**范围**：
- `plugins/knowledge-navigation`
- `plugins/knowledge-tree-plugin`

**核心问题**：
1. pre_llm_call 是否可靠召回 Hindsight + 知识树，并正确注入 `<memory-context>`。
2. post_llm_call 是否只学习稳定知识，不把 cron/日志/临时错误写入知识树。
3. 两插件之间 import、配置、路径、依赖是否稳定。
4. 知识导航是否正确处理标记记忆、熔断、超时、降权、去重。

**重点文件**：
- `plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py`
- `plugins/knowledge-navigation/src/knowledge_navigation/core/filtering.py`
- `plugins/knowledge-navigation/src/knowledge_navigation/adapters/hindsight.py`
- `plugins/knowledge-navigation/src/knowledge_navigation/turn_gate.py`
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/hooks.py`
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/public_api.py`
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/recall.py`
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/placement.py`
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/adapters/database.py`

**必查风险**：
- `knowledge-tree-plugin/tests/test_public_api.py` 与当前 `public_api.py` 疑似不一致：测试引用 `_adapter_cache/_get_cached_adapter`，源码无对应符号。
- tree plugin 新增知识点疑似未持久化自身 `k_vector`，可能影响后续 recall/dedup。
- tree plugin 对 navigation 的 `turn_gate` 有隐式依赖，但 pyproject 未声明。
- navigation 与 tree plugin embedding key 不一致：`SILICONFLOW_API_KEY` vs `HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY`。
- navigation README 版本与 `pyproject/plugin.yaml` 不一致。
- public_api 高频 recall 每次新建 DB adapter，需评估连接压力。
- knowledge tree recall 异常只跳过，不参与熔断，需确认策略。

**建议命令**：
```bash
cd /mnt/d/HermesProject/plugins/knowledge-navigation
PYTHONPATH=src:/mnt/d/HermesProject/plugins/knowledge-tree-plugin/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src \
KN_TRACE_LOG_PATH=/tmp/knowledge-navigation-pytest-trace.log \
pytest -q

cd /mnt/d/HermesProject/plugins/knowledge-tree-plugin
PYTHONPATH=src:/mnt/d/HermesProject/plugins/knowledge-navigation/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src \
pytest -q

PYTHONPATH=/mnt/d/HermesProject/plugins/knowledge-navigation/src:/mnt/d/HermesProject/plugins/knowledge-tree-plugin/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src \
python - <<'PY'
import knowledge_navigation, knowledge_tree_plugin
from knowledge_navigation.core.hooks import pre_llm_call
from knowledge_tree_plugin.hooks import post_llm_call
from knowledge_tree_plugin.public_api import recall_from_tree_raw
print('imports ok')
PY
```

**产出**：
- `A-plugin-review.md`
- P0/P1/P2 问题表。
- hook 触发矩阵：用户入口、cron、subagent、系统 prompt、工具输出。
- pre/post 性能预算表。

---

### Agent B：知识树建树 + 聚类算法/DB 审查

**范围**：
- `scripts/knowledge-tree-builder`
- `scripts/clustering-analysis-v3`

**核心问题**：
1. 知识树建树管线 scan/analyze/split/admit/place 是否幂等、可恢复、可验证。
2. 聚类分析 HDBSCAN/实体挂靠/因果链/文本富化是否算法正确。
3. PG schema、pgvector、唯一索引、ON CONFLICT 是否与代码一致。
4. embedding 格式、维度、缓存、回写是否一致。

**重点文件**：
- `scripts/knowledge-tree-builder/src/knowledge_tree_builder/cli.py`
- `scripts/knowledge-tree-builder/src/knowledge_tree_builder/phase/*.py`
- `scripts/knowledge-tree-builder/src/knowledge_tree_builder/place.py`
- `scripts/knowledge-tree-builder/src/knowledge_tree_builder/adapters/database.py`
- `scripts/knowledge-tree-builder/src/knowledge_tree_builder/core/embeddings.py`
- `scripts/clustering-analysis-v3/src/clustering_analysis/cli.py`
- `scripts/clustering-analysis-v3/src/clustering_analysis/core/clustering.py`
- `scripts/clustering-analysis-v3/src/clustering_analysis/core/embeddings.py`
- `scripts/clustering-analysis-v3/src/clustering_analysis/adapters/database.py`
- `scripts/clustering-analysis-v3/scripts/cron_wrapper.sh`

**必查风险**：
- knowledge-tree-builder `_write_to_db()` 不是整体原子事务；中断后可能节点已插入但 text/k_vector 未补齐。
- `source_articles.insert_article()`、`knowledge_point_texts` 幂等性不足，重复运行/并发运行需测。
- `update_retrieval_confidence()` 使用字段 `retrieval_confidence`，但 create_tables 未见创建该列。
- review queue 状态可能不一致：写入 `pending`，查询默认 `pending_review`。
- Phase4 subject k_vector 缺失时匹配失败，可能持续创建“新科目”。
- clustering HDBSCAN 输入未显式 L2 normalize，metric=euclidean，需验证是否符合语义聚类。
- clustering `batch_embed()` 部分失败可能导致 zip 静默少更新。
- clustering `memory_links ON CONFLICT` 依赖唯一索引，需与生产 schema 核对。
- `entities.mention_count += 1` 重跑非严格幂等。
- `dedup_minhash --apply` 是物理删除，必须单独 gate。

**建议命令**：
```bash
cd /mnt/d/HermesProject/scripts/knowledge-tree-builder
PYTHONPATH=src python3 -m pytest tests -q
PYTHONPATH=src python3 -m knowledge_tree_builder.cli --help
PYTHONPATH=src python3 -m knowledge_tree_builder.cli run --input-dir test_articles --merged --dry-run

cd /mnt/d/HermesProject/scripts/clustering-analysis-v3
PYTHONPATH=src python3 -m pytest tests -q
PYTHONPATH=src python3 - <<'PY'
from clustering_analysis.cli import run
run(apply=False, dry_run=True, cleanup=False, force=True, skip_entity=True, config_path='config/default.yaml')
PY
python3 scripts/dedup_minhash.py --threshold 0.85 --limit 5000
```

**DB 检查 SQL**：
```sql
-- pgvector/schema
select extname from pg_extension where extname='vector';
select table_name, column_name, data_type, udt_name
from information_schema.columns
where table_name in ('knowledge_tree','knowledge_point_texts','memory_units','entities','unit_entities','memory_links','knowledge_review_queue')
order by table_name, ordinal_position;

-- unique/index support
select tablename, indexname, indexdef
from pg_indexes
where tablename in ('knowledge_tree','knowledge_point_texts','entities','unit_entities','memory_links')
order by tablename, indexname;

-- vector completeness
select node_type, count(*) total, count(k_vector) with_vec
from knowledge_tree
group by node_type;
```

**产出**：
- `B-builder-clustering-review.md`
- schema-code mismatch 表。
- 幂等/恢复测试矩阵。
- 算法风险样本与阈值建议。

---

### Agent C：运行调度 + 记忆维护审查

**范围**：
- `scripts/self-evolving`
- `scripts/daily-learn`
- `scripts/memory-cleanup`
- `scripts/clustering-analysis-v3/scripts/mark_memory.py`
- cron jobs / deploy manifests

**核心问题**：
1. online learning、深度研究、知识树维护、聚类、记忆清理是否错峰且互不污染。
2. memory-cleanup 是否安全保护 MEMORY.md/USER.md。
3. mark_memory 是否与 knowledge-navigation 过滤逻辑一致。
4. 自进化/反思是否只是生成建议，不会意外写入记忆或代码。

**重点文件**：
- `scripts/self-evolving/src/self_evolving/operators/*.py`
- `scripts/self-evolving/src/kanban_reflection/*.py`
- `scripts/daily-learn/daily_learn.sh`
- `scripts/memory-cleanup/src/memory_cleanup/cli.py`
- `scripts/memory-cleanup/src/memory_cleanup/adapters/memory_store.py`
- `scripts/memory-cleanup/src/memory_cleanup/core/classifier.py`
- `scripts/memory-cleanup/src/memory_cleanup/core/verifier.py`
- `scripts/clustering-analysis-v3/scripts/mark_memory.py`
- `plugins/knowledge-navigation/src/knowledge_navigation/core/filtering.py`
- `deploy/projects/*.sh`

**必查风险**：
- `memory-cleanup --apply` 会真实修改 MEMORY.md/USER.md 并 retain 到 Hindsight；必须 dry-run + 人工确认。
- `AUTO_REMOVE_PATTERNS` 可能误删有价值方法论/长期偏好。
- USER.md 中长期偏好不应被误迁移到 Hindsight。
- mark_memory 直接 append 标记到 `memory_units.text`，embedding 不一定同步更新。
- Hindsight 与 MEMORY.md 双写不是事务。
- knowledge-navigation 只检查尾部 100 字符内标记；note 太长可能过滤失效。
- daily_learn 失败时临时目录删除，且 builder 输出只留 tail -5，排障不足。
- cron wrapper 当前最近一次 `聚类分析每周跑` 状态 error，应作为 P0 调度项单独复查。

**建议命令**：
```bash
# cron 当前状态
hermes cron list

# self-evolving
cd /mnt/d/HermesProject/scripts/self-evolving
python3 -m pytest tests -q
PYTHONPATH=src python3 -m kanban_reflection.cli --help

# daily-learn shell 检查
cd /mnt/d/HermesProject/scripts/daily-learn
bash -n daily_learn.sh

# memory-cleanup dry-run
cd /mnt/d/HermesProject/scripts/memory-cleanup
python3 -m pytest tests -q
PYTHONPATH=src python3 -m memory_cleanup run --config config/default.yaml --json --vote 1

# mark_memory 只读
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3
python3 -m pytest tests/test_mark_memory.py -q
python3 scripts/mark_memory.py --help
```

**一致性检查命令**：
```bash
wc -c /root/.hermes/memories/MEMORY.md /root/.hermes/memories/USER.md
grep -c '^§$' /root/.hermes/memories/MEMORY.md
grep -c '^§$' /root/.hermes/memories/USER.md
grep -o '\[标记: [^]]*\]' /root/.hermes/memories/MEMORY.md | sort | uniq -c
```

**产出**：
- `C-runtime-memory-review.md`
- cron 调度图。
- 写入风险表。
- MEMORY/USER/Hindsight 一致性报告。

---

### Agent D：端到端集成与回归测试审查

**范围**：全部模块，只负责验证链路，不深入读每个文件。

**核心链路**：
```text
用户消息
  → knowledge-navigation pre_llm_call
  → Hindsight recall + knowledge-tree recall
  → <memory-context> 注入
  → LLM 回复
  → knowledge-tree-plugin post_llm_call
  → 提取新知识点
  → embedding + placement
  → PG knowledge_tree 写入
  → 下轮 navigation 可召回

cron/daily-learn/deep-research
  → knowledge-tree-builder 入树
  → knowledge-tree-plugin/public_api 可召回

clustering-analysis
  → memory_units 聚类/标记/因果链/富化
  → knowledge-navigation 过滤标记并利用因果链 boost

memory-cleanup
  → MEMORY/USER 清理或迁移到 Hindsight
  → Hindsight recall 可见
```

**必测场景**：
1. pre recall：Hindsight 可用、知识树可用、二者之一不可用、二者都不可用。
2. post learning：真实知识、日志文本、代码输出、cron 输出、短回答、系统 prompt。
3. 标记过滤：错误/作废/可疑/待验证排除，已解决降权。
4. 知识树新增后下一轮可召回。
5. daily-learn 写入知识点后 navigation 能召回。
6. memory-cleanup dry-run 不写文件；临时文件 apply 可恢复。
7. clustering dry-run 不写 DB；apply 前 schema gate 通过。

**产出**：
- `D-e2e-regression-review.md`
- PASS/FAIL gate 表。
- 最小 smoke 测试脚本建议。

---

## 2. 汇总 Agent：统一裁决与任务化

并行 Agent A-D 完成后，由汇总 Agent 执行：

1. 合并重复问题。
2. 按 P0/P1/P2 分类。
3. 标注修改层级：插件 / 脚本 / 配置 / DB schema / cron / 文档。
4. 标注是否需要 Hermes 核心源码：默认否；只有实证证明 hook/脚本无法解决才标是。
5. 标注是否需要模型训练：默认否；API prompt/embedding 调用不算训练。
6. 输出修复顺序和冲突策略。

**汇总输出格式**：

| ID | 优先级 | 模块 | 问题 | 证据 | 建议修复 | 修改层级 | 验证方法 | 是否阻塞部署 |
|---|---|---|---|---|---|---|---|---|

---

## 3. Review 执行顺序

### Phase 1：只读并行审查

并行启动 Agent A/B/C/D。

禁止事项：
- 不执行 `--apply`。
- 不部署。
- 不修改代码。
- 不修改 DB。
- 不改 MEMORY.md/USER.md。

### Phase 2：真实状态验证

顺序执行，因为会碰共享服务/DB：

1. `cronjob list` / `hermes cron list` 确认调度状态。
2. `deploy/deploy.sh plan <project>` 确认部署源/目标。
3. 运行单元测试，不碰生产写入。
4. 查询 DB schema 和只读统计。
5. 对最近失败 cron 单独拉日志。

### Phase 3：问题汇总与优先级裁剪

- P0：会导致数据丢失、错误写入、召回完全失效、cron 持续失败。
- P1：正确性/性能/幂等/可恢复性明显风险。
- P2：文档、版本、轻微维护性。

### Phase 4：修复计划，不直接执行

生成开发方案，每项包含：
- 当前实现逻辑。
- 建议实现逻辑。
- 文件路径。
- 工作量。
- 验证命令。
- 是否需要 deploy。
- 是否需要 gateway restart。

### Phase 5：用户确认后分批修复

建议批次：
1. 测试/文档/配置不一致。
2. plugin import/config/trace/门控问题。
3. DB schema/幂等/事务问题。
4. cron 调度/日志/告警问题。
5. 性能优化。

---

## 4. 全局 Gate

### Gate 1：测试 Gate

必须通过：
```bash
cd /mnt/d/HermesProject/plugins/knowledge-navigation && PYTHONPATH=src:/mnt/d/HermesProject/plugins/knowledge-tree-plugin/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src pytest -q
cd /mnt/d/HermesProject/plugins/knowledge-tree-plugin && PYTHONPATH=src:/mnt/d/HermesProject/plugins/knowledge-navigation/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src pytest -q
cd /mnt/d/HermesProject/scripts/knowledge-tree-builder && PYTHONPATH=src pytest -q
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3 && PYTHONPATH=src pytest -q
cd /mnt/d/HermesProject/scripts/memory-cleanup && PYTHONPATH=src pytest -q
cd /mnt/d/HermesProject/scripts/self-evolving && PYTHONPATH=src pytest -q
cd /mnt/d/HermesProject/scripts/daily-learn && bash -n daily_learn.sh
```

### Gate 2：DB Schema Gate

必须确认：
- pgvector 可用。
- knowledge tree vector 维度与 bge-m3 一致。
- `memory_links`、`entities`、`unit_entities` 的 unique indexes 支持 ON CONFLICT。
- knowledge tree 新增点有可召回向量。
- review_queue status 取值一致。

### Gate 3：写入安全 Gate

以下命令不得在 review 阶段运行：
- `memory-cleanup --apply`
- `dedup_minhash.py --apply`
- `long_memory_governance.py --apply`
- `clustering_analysis.run(apply=True)`
- `knowledge_tree_builder run` 非 dry-run 且连接生产 DB
- `mark_memory.py mark ... --apply`

### Gate 4：部署 Gate

修改后必须：
```bash
cd /mnt/d/HermesProject
./deploy/deploy.sh plan <project>
./deploy/deploy.sh deploy <project>
./deploy/deploy.sh history <project>
```

涉及插件：
- `knowledge-navigation`
- `knowledge-tree-plugin`

部署后需要确认 gateway 重启成功：
```bash
systemctl status hermes-gateway.service --no-pager
```

### Gate 5：端到端 Gate

至少验证：
1. navigation import smoke。
2. tree plugin import smoke。
3. pre_llm_call 在 Hindsight/knowledge tree 单侧失败时降级正常。
4. post_llm_call 对日志/cron 内容跳过。
5. 标记记忆过滤有效。
6. 知识树新增知识能被 recall。

---

## 5. 首轮应优先审查的 P0/P1 疑点

| 优先级 | 疑点 | 原因 |
|---|---|---|
| P0 | `聚类分析每周跑` 最近 cron 状态 error | 调度链路已实际失败，需先查日志 |
| P0 | `memory-cleanup --apply` 安全边界 | 可能修改 MEMORY/USER 并迁移 Hindsight，误删成本高 |
| P0 | knowledge-tree-plugin 新增知识点是否写入 k_vector | 不写则在线学习“入库但不可召回” |
| P0 | DB schema 与 ON CONFLICT/index 是否一致 | 不一致会导致聚类 apply 写库失败或部分失败 |
| P1 | tree plugin public_api 测试与源码不一致 | 测试可能过时或源码缺功能 |
| P1 | clustering HDBSCAN 是否归一化 | 影响聚类质量，可能产生错误实体/因果链 |
| P1 | mark_memory 标记后 embedding 不一致 | recall 语义可能仍受旧向量影响 |
| P1 | daily-learn 失败现场不可复现 | 临时目录删除 + tail -5，排障成本高 |
| P1 | 插件间隐式依赖未声明 | 单独部署/加载顺序可能失败 |
| P2 | README/版本/env 名称不一致 | 影响维护和部署理解 |

---

## 6. 推荐并行执行命令模板

```python
# Hermes delegate_task batch，最多 3 个并行；D 可第二轮执行
[
  {
    "goal": "审查 knowledge-navigation + knowledge-tree-plugin，只读，输出 A-plugin-review.md 内容",
    "toolsets": ["terminal", "file"]
  },
  {
    "goal": "审查 knowledge-tree-builder + clustering-analysis-v3，只读，输出 B-builder-clustering-review.md 内容",
    "toolsets": ["terminal", "file"]
  },
  {
    "goal": "审查 self-evolving + daily-learn + memory-cleanup + mark_memory + cron，只读，输出 C-runtime-memory-review.md 内容",
    "toolsets": ["terminal", "file"]
  }
]
```

第二轮：
```python
{
  "goal": "基于 A/B/C 结果做端到端集成与回归测试计划，输出 D-e2e-regression-review.md 内容",
  "toolsets": ["terminal", "file"]
}
```

---

## 7. 最终交付物清单

1. `A-plugin-review.md`
2. `B-builder-clustering-review.md`
3. `C-runtime-memory-review.md`
4. `D-e2e-regression-review.md`
5. `00-review-summary.md`：合并问题、优先级、修复顺序。
6. `01-fix-plan.md`：用户确认后可执行开发方案。
7. `02-verification-log.md`：测试/DB/cron/部署验证输出。

---

## 8. 结论

本轮 review 应按“插件链路 / 算法与 DB / 运行与记忆维护 / 端到端回归”四条线并行推进；先只读查证和 dry-run，优先处理已知 cron error、写入安全、k_vector 可召回性、DB schema/ON CONFLICT 一致性四类 P0 风险，再进入修复计划。