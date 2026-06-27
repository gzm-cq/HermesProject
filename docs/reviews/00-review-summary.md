# 00 Review Summary — 记忆/知识系统

> **文档状态：历史审查记录 / 修复前状态**  
> 本文保留当时的 FAIL 结论作为问题发现证据，不代表当前状态。修复后最终状态见 `03-post-fix-audit-2026-06-15.md`。


> 来源：`spec-memory-knowledge-review-execution-2026-06-15.md`、A/B/C/D 四份审查报告、`02-verification-log.md`
> 执行范围：只读审查 + 安全验证；未修改业务代码、未部署、未执行生产写入。

## 总体裁决

**当前端到端 Release Gate：FAIL。**

原因不是单点崩溃，而是三个 P0 风险同时存在：

1. 在线学习写入的知识点疑似缺自身 `k_vector`，会导致“学到了但召回不到”。
2. 记忆清理 daily dryrun cron 实际执行 `--apply`，存在自动修改 MEMORY/USER 的高风险。
3. 聚类 weekly cron 当前失败，并且 wrapper 默认串联多个写入/删除型 `--apply` 步骤。

在这些问题关闭前，不建议执行部署或生产 apply。

---

## 实际验证结果

| 模块 | 验证结果 | 结论 |
|---|---:|---|
| import smoke | `imports ok` | PASS |
| daily-learn shell syntax | `daily_learn.sh syntax ok` | PASS |
| knowledge-navigation | `86 passed` | PASS |
| knowledge-tree-plugin | `35 passed, 2 failed` | FAIL |
| knowledge-tree-builder | `265 passed` | PASS |
| clustering-analysis-v3 | `66 passed, 5 failed` | FAIL |
| memory-cleanup | `123 passed` | PASS |
| self-evolving | `46 passed, 1 failed` | FAIL |
| pgvector | installed | PASS |
| knowledge_tree vector completeness | `knowledge_point 5649/2402 with k_vector; subject 54/33 with k_vector` | FAIL |
| knowledge_review_queue status | `pending=8` | FAIL |
| cron 聚类分析每周跑 | last_status=`error` | FAIL |
| memory-cleanup-daily-dryrun | last_status=`ok` 但脚本实际 apply | FAIL |

---

## P0 问题表

| ID | 模块 | 问题 | 证据 | 影响 | 建议修复 | 修改层级 | 阻塞部署 |
|---|---|---|---|---|---|---|---|
| P0-01 | knowledge-tree-plugin | 在线新增知识点未写自身 `k_vector` | A 报告；D 报告；DB 仅 2402/5649 knowledge_point 有 k_vector | post learning 写入后不可召回 | 插入知识点时同步写入 embedding/k_vector，补测试 | 插件/DB 写入逻辑 | 是 |
| P0-02 | memory-cleanup cron | `memory-cleanup-daily-dryrun` 实际执行 `--apply` | C 报告；cron 名称 dryrun，wrapper 实际 apply | 自动修改 MEMORY.md/USER.md，误删长期偏好 | 改 cron wrapper 为纯 dry-run；apply 需人工确认 | 脚本/cron | 是 |
| P0-03 | clustering cron | `聚类分析每周跑` 最近状态 error，实际 120s timeout | C 报告；cronjob list last_status=error | 记忆聚类/因果链维护不可用 | 拆分 wrapper、提高 timeout 或改为 no_agent 单步可恢复 | 脚本/cron | 是 |
| P0-04 | clustering wrapper | 默认连续执行治理/去重/聚类多个 `--apply`，失败不阻断后续 | C 报告 | 一个 cron 同时触发删除/写库/标记，风险不可控 | 分离 destructive 步骤；默认 report-only；apply 单独 gate | 脚本/cron | 是 |
| P0-05 | knowledge_review_queue | 写入 status=`pending`，查询/处理用 `pending_review` | B 报告；DB 当前 `pending=8` | 待审知识永远不可见 | 统一 status 枚举并迁移现有 pending | builder DB/schema | 是 |

---

## P1 问题表

| ID | 模块 | 问题 | 证据 | 建议修复 | 修改层级 |
|---|---|---|---|---|---|
| P1-01 | knowledge-navigation | Hindsight 失败/空结果时丢弃知识树 recall | A/D 报告 | 将 Hindsight 与知识树降级策略解耦，Hindsight fail 不影响 KT 注入 | 插件 |
| P1-02 | knowledge-tree-plugin | `public_api` 测试期待 adapter cache，但源码无 cache | `test_public_api.py` 两个 AttributeError | 实现 `_adapter_cache/_get_cached_adapter` 或修正测试/设计 | 插件 |
| P1-03 | knowledge-tree-plugin | 高频 recall 每次新建 DB adapter/连接 | A 报告 | 引入健康检查 + TTL/进程内 cache | 插件 |
| P1-04 | knowledge-tree-plugin | 隐式依赖 `knowledge_navigation.turn_gate` 未声明 | A 报告 | 抽出 shared turn_gate 或声明依赖/降级 fallback | 插件/依赖 |
| P1-05 | builder | `_write_to_db()` 非整体事务 | B 报告 | 同一 article/domain 批次改为事务边界，失败可回滚/重试 | 脚本/DB |
| P1-06 | builder | subject k_vector 缺失影响科目匹配 | DB：subject 54/33 with_vec | backfill subject k_vector；写入时强制更新 | 脚本/DB |
| P1-07 | clustering | HDBSCAN 未显式 L2 normalize，且环境缺 sklearn HDBSCAN | verification log：ImportError sklearn >=1.3 | 固定依赖版本或 fallback；聚类前 normalize | 脚本/依赖 |
| P1-08 | clustering | `batch_embed()` 部分失败后 zip 静默截断 | B 报告 | 数量校验，不一致则 fail-fast/重试 | 脚本 |
| P1-09 | clustering | `entities.mention_count += 1` 重跑非幂等 | B 报告 | 使用 run_id 或去重后计数，避免重跑累计 | 脚本/DB |
| P1-10 | memory-cleanup | `AUTO_REMOVE_PATTERNS` 过宽且跳过 Phase2 | C 报告 | 移除宽泛词；direct remove 也进 verifier | 脚本 |
| P1-11 | USER.md 保护 | 主要靠 prompt，无硬 gate | C 报告 | USER 删除/迁移必须白名单或人工确认 | 脚本 |
| P1-12 | mark_memory | append 标记不更新 embedding，DB/MEMORY 双写非事务 | C 报告 | 标记改 metadata 表或标记后回写 embedding；双写加补偿日志 | 脚本/DB |
| P1-13 | navigation 标记过滤 | 只看尾部 100 字符，长 note 可能失效 | C 报告 | 改为全局/结构化 metadata 检测，或限定 marker 永远在最后 | 插件 |
| P1-14 | daily-learn | 失败时删临时目录，日志仅 tail -5 | C 报告 | 失败保留 TMP_DIR；完整日志落盘 | 脚本 |
| P1-15 | self-evolving | `RiskLevel` enum 被 `max()` 比较报 TypeError | verification log | 用显式 severity rank 比较 | 脚本 |

---

## P2 问题表

| ID | 模块 | 问题 | 建议 |
|---|---|---|---|
| P2-01 | knowledge-navigation | README 版本 `1.2.1` 与 `pyproject/plugin.yaml` 的 `1.1.0` 不一致 | 统一版本 |
| P2-02 | knowledge-navigation/tree-plugin | embedding key env 命名不一致 | 统一配置别名并文档化 |
| P2-03 | plugin manifests | `plugin.yaml` 依赖清单落后于 `pyproject.toml` | 同步依赖 |
| P2-04 | tree-plugin | `pyproject.toml` 声明 README.md 但目录缺 README | 补 README 或改 metadata |
| P2-05 | navigation tests | 默认禁用知识树路径，缺 KT enabled 回归 | 增加 KT enabled mock test |

---

## 修复批次建议

### Batch 1：先关写入安全 P0

1. 修 `memory-cleanup-daily-dryrun`：名称和行为一致，禁止自动 apply。
2. 拆/禁 clustering destructive apply wrapper，先让 weekly cron 只 report-only 或 dry-run。
3. 明确所有 apply 型命令的人工确认 gate。

### Batch 2：关知识树可召回 P0

1. 修 tree plugin 新增点 `k_vector` 写入。
2. backfill 缺失 knowledge_point/subject k_vector。
3. 修 `knowledge_review_queue` status 不一致。

### Batch 3：关插件链路 P1

1. Hindsight fail 不再丢弃 KT recall。
2. public_api adapter cache 设计落地或测试修正。
3. 插件依赖/turn_gate 处理。

### Batch 4：关聚类算法/依赖 P1

1. 修 HDBSCAN 依赖或 fallback。
2. 聚类前 normalize。
3. batch_embed 数量校验。
4. mention_count 幂等。

### Batch 5：关维护性 P2

版本、README、依赖声明、env 命名、测试覆盖补齐。

---

## 不建议现在做的事

- 不建议直接部署插件，因为当前 tree plugin 单测失败且 k_vector 链路未关闭。
- 不建议运行任何生产 `--apply`，尤其是 memory-cleanup、dedup、clustering wrapper。
- 不建议先修 P2 文档；当前主要风险是写入安全与可召回性。

---

## 产物清单

- `A-plugin-review.md`：插件链路审查。
- `B-builder-clustering-review.md`：builder/聚类/DB 审查。
- `C-runtime-memory-review.md`：运行调度/记忆维护审查。
- `D-e2e-regression-review.md`：端到端回归方案。
- `02-verification-log.md`：真实验证输出。
- `01-fix-plan.md`：后续修复计划。
