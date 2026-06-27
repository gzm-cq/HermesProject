# Agent C Runtime / Memory Maintenance Review

> **文档状态：历史审查报告 / 修复前问题发现**  
> 本文是 Agent C 的运行时审查结果，问题是否已关闭请以 `03-post-fix-audit-2026-06-15.md`、当前 cron 配置和运行时文件为准。


日期：2026-06-15  
源码根目录：`/mnt/d/HermesProject`  
审查方式：只读源码/配置/cron 状态审查 + 非写入验证命令；未执行 `--apply`、未部署、未写 DB、未修改 MEMORY/USER。

---

## 1. 范围

本报告覆盖 review plan 中 Agent C 的运行调度与记忆维护范围：

- `scripts/self-evolving`
- `scripts/daily-learn`
- `scripts/memory-cleanup`
- `scripts/clustering-analysis-v3/scripts/mark_memory.py`
- `plugins/knowledge-navigation/src/knowledge_navigation/core/filtering.py` 中标记过滤逻辑
- `scripts/clustering-analysis-v3/scripts/cron_wrapper.sh`
- `deploy/deploy.sh`、`deploy/lib/common.sh`、相关 `deploy/projects/*.sh` 与 `deploy/manifests/*.manifest`
- 当前 Hermes cron 状态与输出目录：`/root/.hermes/cron/jobs.json`、`/root/.hermes/cron/output/...`（只读）

明确未覆盖：知识树建树/聚类算法主体与 DB schema 深审（由 Agent B 覆盖）、插件 recall 主链路深审（由 Agent A 覆盖）。

---

## 2. 关键证据

### 2.1 memory-cleanup apply 会真实修改 MEMORY/USER 并 retain 到 Hindsight

| 证据 | 位置 | 说明 |
|---|---|---|
| CLI `--apply` 开关 | `scripts/memory-cleanup/src/memory_cleanup/cli.py:88-101` | 默认 dry-run，传 `--apply` 才执行。 |
| apply 分支执行清理 | `scripts/memory-cleanup/src/memory_cleanup/cli.py:311-331` | `apply=True` 时分别调用 `execute_cleanup()` 处理 `MEMORY.md` 与 `USER.md`。 |
| 配置指向真实文件 | `scripts/memory-cleanup/config/default.yaml:8-10` | 默认路径为 `/root/.hermes/memories/MEMORY.md`、`USER.md`、`state.db`。 |
| 备份但非事务 | `scripts/memory-cleanup/src/memory_cleanup/adapters/memory_store.py:88-99` | 执行前复制 `.bak`，随后加载 Hermes `MemoryStore`。 |
| Hindsight retain | `scripts/memory-cleanup/src/memory_cleanup/adapters/memory_store.py:47-63` | 调 `hindsight_url` POST retain，2 次重试。 |
| retain 成功后删除 | `scripts/memory-cleanup/src/memory_cleanup/adapters/memory_store.py:185-210` | 并行 retain，随后仅删除 retain 成功的条目。 |
| USER hindsight 迁移 | `scripts/memory-cleanup/src/memory_cleanup/adapters/memory_store.py:212-228` | `hindsight_list` 先 retain 再 remove。 |
| 直接删除 | `scripts/memory-cleanup/src/memory_cleanup/adapters/memory_store.py:229-240` | `remove_list` 中非 Phase2 keep/skip 的条目直接 `_remove()`。 |

结论：源码具备先备份/先 retain 后删的保护，但 MEMORY/USER 文件写入与 Hindsight retain 不是同一个事务；任意中断或部分失败会导致双写不一致。

### 2.2 AUTO_REMOVE_PATTERNS 与 USER 保护

| 证据 | 位置 | 说明 |
|---|---|---|
| 自动直删关键词 | `scripts/memory-cleanup/src/memory_cleanup/core/classifier.py:15-24` | 包含 `清理`、`V6`、`方法论`、`Memory cleaning methodology` 等。 |
| 直接删判定 | `scripts/memory-cleanup/src/memory_cleanup/core/classifier.py:274-318` | 空条目、命中 `AUTO_REMOVE_PATTERNS`、被 merge/compress/hindsight 覆盖者跳过 Phase2。 |
| USER prompt 强保护 | `scripts/memory-cleanup/src/memory_cleanup/core/prompts.py:56-67` | USER.md 几乎都应保留；个人背景、偏好、规则、项目状态不得 remove。 |
| USER 允许 hindsight | `scripts/memory-cleanup/src/memory_cleanup/core/prompts.py:84-90` | 特定场景信息可迁移到 Hindsight，包括审计方法论、设计哲学、执行计划规范等。 |
| hindsight 质量校验较弱 | `scripts/memory-cleanup/src/memory_cleanup/core/classifier.py:535-570` | 只检查 index、关键词、长度，不验证“是否长期偏好/应留在 USER”。 |

结论：USER 保护主要依赖 prompt 与 LLM 分类，缺少 apply 前硬 gate。`AUTO_REMOVE_PATTERNS` 中的 `方法论` 对 USER 中长期偏好/方法论类条目存在误直删风险；USER 的 `hindsight` 迁移也可能把长期偏好移出 USER.md。

### 2.3 mark_memory 标记与 navigation 过滤一致性

| 证据 | 位置 | 说明 |
|---|---|---|
| Hindsight 标记追加到 text | `scripts/clustering-analysis-v3/scripts/mark_memory.py:92-117` | `UPDATE memory_units SET text = text || %s`，未同步更新 embedding 字段。 |
| unmark 只改 text | `scripts/clustering-analysis-v3/scripts/mark_memory.py:120-141` | 移除标记文本，未更新 embedding。 |
| Hermes MEMORY 标记写文件 | `scripts/clustering-analysis-v3/scripts/mark_memory.py:168-196` | 直接重写 `/root/.hermes/memories/MEMORY.md`。 |
| DB 与 Hermes 双写顺序 | `scripts/clustering-analysis-v3/scripts/mark_memory.py:412-435` | 先 DB `mark_memory()`，再可选 `mark_hermes_memory()`；无跨存储事务。 |
| navigation 仅看尾部 100 字符 | `plugins/knowledge-navigation/src/knowledge_navigation/core/filtering.py:37-45` | `tail = text[-100:]` 后匹配 `[标记: ...]`。 |
| 排除/降权类型一致 | `plugins/knowledge-navigation/src/knowledge_navigation/core/filtering.py:15-18, 41-47` | 错误/作废/可疑/待验证排除，已解决降权 0.3x。 |
| 自动标记批量 commit | `scripts/clustering-analysis-v3/scripts/mark_memory.py:345-355` | 自动规则调用 `mark_memory(... commit=False)`，最后统一 commit。 |

结论：标记类型与过滤类型基本一致，但标记追加文本不更新 embedding；DB 与 Hermes MEMORY.md 双写非事务；用户 CLI note 过长时，真实标记可能落在 text 尾部 100 字符之外，导致 navigation 过滤失效。

### 2.4 daily-learn 日志/临时目录

| 证据 | 位置 | 说明 |
|---|---|---|
| 临时目录自动删除 | `scripts/daily-learn/daily_learn.sh:5-6` | `TMP_DIR=$(mktemp -d ...)` + `trap 'rm -rf "$TMP_DIR"' EXIT`，失败现场会消失。 |
| 日志文件 | `scripts/daily-learn/daily_learn.sh:10-12` | 输出 tee 到 `/root/.hermes/logs/daily-learn-YYYYMMDD.log`。 |
| builder 输出只留 tail | `scripts/daily-learn/daily_learn.sh:114-116` | `knowledge_tree_builder.cli run ... 2>&1 | tail -5`，完整错误栈被截断。 |
| cron 最近 OK | `hermes cron list` 输出 | `每日在线学习` 最近 `2026-06-12T14:01:38... ok`。 |
| cron 输出示例 | `/root/.hermes/cron/output/919f31a5b42d/2026-06-12_14-01-38.md` | 只看到最终 5 行，符合脚本 tail 行为。 |

结论：日常成功路径可用；失败排障资料不足，尤其是 builder 错误上下文和输入 md 临时文件不可复现。

### 2.5 聚类 cron error 与 wrapper

| 证据 | 位置 | 说明 |
|---|---|---|
| 当前 cron 状态 error | `hermes cron list` | `聚类分析每周跑` 最近状态：`error: Script timed out after 120s: /root/.hermes/scripts/clustering-weekly.sh`。 |
| 当前 job 配置 | `/root/.hermes/cron/jobs.json` 只读检查 | job `834f94944665`：script=`clustering-weekly.sh`，workdir=`/root/.hermes/scripts/clustering-analysis-v3`，`no_agent=True`。 |
| wrapper 调用 | `/root/.hermes/scripts/clustering-weekly.sh:1-4` | 仅 `bash /root/.hermes/scripts/clustering-analysis-v3/scripts/cron_wrapper.sh 2>&1`。 |
| cron 输出目录 | `/root/.hermes/cron/output/834f94944665/*.md` | 多次只记录 `Script timed out after 120s`。 |
| cron_wrapper 日志 | `scripts/clustering-analysis-v3/scripts/cron_wrapper.sh:30-34, 230-233` | 日志写 `/tmp/hindsight-cron-<timestamp>.log`，并记录 `CLUSTERING_DB_URL` 前缀。 |
| wrapper 中 apply 操作 | `scripts/clustering-analysis-v3/scripts/cron_wrapper.sh:256-287` | 步骤 2 `long_memory_governance.py --apply`，步骤 3 `dedup_minhash.py --apply`，步骤 4 `run(apply=True...)`。 |
| wrapper 失败不阻断后续 | `scripts/clustering-analysis-v3/scripts/cron_wrapper.sh:14-16, 235-294` | 设计为每步失败继续，最终 `OVERALL_STATUS=partial` 后 exit 1。 |

结论：聚类 cron 已实际失败，原因入口是 no-agent 默认 120s 超时与 wrapper 全量 apply 管线耗时不匹配。排障入口应优先看 `/root/.hermes/cron/output/834f94944665/`，其次看 `/tmp/hindsight-cron-*.log`（本次审查未发现近期 `/tmp/hindsight-cron-*` 文件，可能被清理或超时未落盘）。

### 2.6 self-evolving 写入风险

| 证据 | 位置 | 说明 |
|---|---|---|
| 算子主要内存返回 | `scripts/self-evolving/src/self_evolving/operators/refinement.py:334-389` | `execute()` 返回 `RefinementOutput`，不直接写记忆/代码。 |
| recombination 主要内存返回 | `scripts/self-evolving/src/self_evolving/operators/recombination.py:263-293` | 返回 `RecombinationOutput`，不直接写记忆/代码。 |
| revision 主要内存返回 | `scripts/self-evolving/src/self_evolving/operators/revision.py:284-310` | 返回 `RevisionOutput`，不直接写记忆/代码。 |
| kanban 反思可写输出文件 | `scripts/self-evolving/src/kanban_reflection/cli.py:93-96` | 仅用户传 `--output` 时写 JSON 文件。 |
| trace 只读 | `scripts/self-evolving/src/kanban_reflection/core/reflector.py:66-99` | 读取 trace.log，不写记忆。 |
| 单测失败 | 验证命令输出 | `TestRefinementOperator.test_refinement_execute` 因 `max(RiskLevel)` TypeError 失败。 |

结论：self-evolving 当前未发现自动写 MEMORY/USER/Hindsight/业务代码的路径；但 refinement 算子有测试失败，影响自进化功能可靠性。

### 2.7 deploy manifests / deploy 风险

| 证据 | 位置 | 说明 |
|---|---|---|
| 分发器只委托项目脚本 | `deploy/deploy.sh:76-90` | `plan/deploy/rollback/history/cleanup` 委托 `deploy/projects/<project>.sh`。 |
| deploy 默认交互确认 | `deploy/lib/common.sh:116-131` | 未传 `--yes` 会提示确认。 |
| deploy 文件级备份 | `deploy/lib/common.sh:133-155` | 覆盖前备份已存在文件。 |
| 首次部署防残留删除 | `deploy/lib/common.sh:167-237` | 会删除目标目录中不在 manifest 的旧文件。 |
| memory-cleanup manifest | `deploy/manifests/memory-cleanup.manifest:8-22` | 包含 `run.sh`、配置和源码；排除 tests/pycache。注释称“手动触发”，但当前实际有 cron。 |
| daily-learn manifest | `deploy/manifests/daily-learn.manifest:5-9` | 只部署 `*.sh` 与 README。 |
| clustering manifest | `deploy/manifests/clustering-analysis-v3.manifest:9-16` | 部署 scripts 中 py/sh，包含 `mark_memory.py`、`cron_wrapper.sh`。 |
| self-evolving manifest | `deploy/manifests/self-evolving.manifest:9-13` | 部署源码、脚本、配置、README。 |

结论：plan 命令安全；deploy 会真实写 `/root/.hermes/...` 并可能删除目标残留，review 阶段不得执行 deploy。memory-cleanup 部署说明与 cron 实际运行方式不一致。

---

## 3. P0 / P1 / P2 问题表

### P0

| ID | 模块 | 问题 | 证据 | 风险 | 建议 | 阻塞部署 |
|---|---|---|---|---|---|---|
| C-P0-01 | cron / memory-cleanup | `memory-cleanup-daily-dryrun` 名称为 dryrun，但当前 runtime wrapper 实际执行 `bash run.sh --vote 1 --apply`。 | `/root/.hermes/scripts/memory-cleanup-daily.sh:4-5`；`cli.py:311-331`；cron job `c194bd1bc26e` 名称。 | 每日自动修改 MEMORY.md/USER.md 并 retain 到 Hindsight；误删/误迁移成本高，且命名误导。 | 立即 gate：暂停或改为 dry-run；apply 需人工审批、差异报告、备份校验、保留窗口。 | 是 |
| C-P0-02 | cron / clustering | `聚类分析每周跑` 最近实际 error，no-agent 120s 超时。 | `hermes cron list`：`Script timed out after 120s`；`/root/.hermes/cron/output/834f94944665/*.md`。 | 每周 Hindsight 维护链路不可用；且 wrapper 中包含 long governance、MinHash 删除、聚类 apply，失败点不透明。 | 单独拆分步骤/延长超时/改 agent 汇总；优先查 `/root/.hermes/cron/output/834f94944665/` 与 `/tmp/hindsight-cron-*`。 | 是 |
| C-P0-03 | clustering cron / 写入安全 | `cron_wrapper.sh` 默认连续运行多项 destructive apply：超长治理、MinHash 删除、聚类 apply。 | `cron_wrapper.sh:256-287`。 | 单次 cron 覆盖多类写 DB 操作，失败不阻断后续，难以回滚和定位。 | 拆为 dry-run 报告 + 人工批准 apply；或每步独立 cron、独立超时、独立审计日志。 | 是 |

### P1

| ID | 模块 | 问题 | 证据 | 风险 | 建议 | 阻塞部署 |
|---|---|---|---|---|---|---|
| C-P1-01 | memory-cleanup | `AUTO_REMOVE_PATTERNS` 过宽，含 `方法论`，命中后跳过 Phase2。 | `classifier.py:15-24, 274-318`。 | 可能直删有价值方法论/长期偏好，尤其 USER 条目。 | AUTO_REMOVE 仅限明确 cleanup 自身元数据；USER 禁用 `方法论` 自动直删；直接删前输出 diff 并人工确认。 | 是，若启用 apply |
| C-P1-02 | memory-cleanup / USER | USER 保护依赖 prompt，且 prompt 允许部分 USER 条目迁 Hindsight；硬校验不足。 | `prompts.py:56-67, 84-90`；`validate_hindsight_quality()` `classifier.py:535-570`。 | 长期偏好/工作风格可能被迁出 USER.md，影响每轮对话稳定注入。 | 对 USER 增加 hard denylist：偏好、规则、工作习惯、沟通风格、个人背景不得 remove/hindsight；apply 前 USER 单独人工审批。 | 是，若启用 apply |
| C-P1-03 | memory-cleanup | Hindsight retain 与 MEMORY/USER 文件修改非事务。 | `_retain()` `memory_store.py:47-63`；文件操作 `memory_store.py:88-99, 119-134, 185-240`。 | 中断/部分失败导致 Hindsight 与 MEMORY/USER 不一致。 | 引入 manifest journal：计划→retain 成功→文件 diff→commit→校验；失败可按 journal 恢复。 | 否，但需 gate |
| C-P1-04 | mark_memory | 标记仅 append 到 `memory_units.text`，embedding 不同步。 | `mark_memory.py:108-116, 120-141`。 | 召回向量仍反映旧文本，标记说明不会参与语义检索；unmark 后也不一致。 | 若标记用于过滤可接受；若用于召回语义，应重算 embedding 或将标记拆为结构化列。 | 否 |
| C-P1-05 | mark_memory / Hermes | Hindsight DB 与 Hermes MEMORY.md 双写非事务。 | `cmd_mark()` `mark_memory.py:417-433`；`mark_hermes_memory()` `mark_memory.py:174-196`。 | DB 已标记但 MEMORY 未标，或反向不一致。 | 默认不要双写；如需双写，用计划文件 + 两阶段校验 + rollback/unmark。 | 否 |
| C-P1-06 | navigation filtering | 只检查文本尾部 100 字符，note 太长会过滤失效。 | `filtering.py:39-45`；CLI note 任意长度 `mark_memory.py:509-512`。 | `[标记: 错误]` 后长说明超过 100 字符时，尾部不含标记，错误记忆仍被召回。 | 限制 note 长度，或过滤 `r"\n\[标记: ...\]"` 至最后一行/最后 500 字符；最好结构化列。 | 否 |
| C-P1-07 | daily-learn | 失败现场不可复现：临时目录 EXIT 删除，builder 只留 tail -5。 | `daily_learn.sh:5-6, 114-116`。 | 入树失败时缺完整 traceback 和输入样本。 | 失败时保留 TMP_DIR；builder 完整输出写单独日志，cron 输出只摘要。 | 否 |
| C-P1-08 | self-evolving | 单测失败：`RiskLevel` enum 不能直接 `max()`。 | 验证命令 `PYTHONPATH=src python3 -m pytest tests -q`；`refinement.py:293`。 | refinement 算子在风险扫描后可能运行时崩溃。 | 用 severity 映射排序或 `max(..., key=...)`。 | 否，除非部署 self-evolving |
| C-P1-09 | cron / memory-cleanup | cron 最近显示 OK，但 6/14 曾因 120s 超时失败；实际输出显示 apply 运行可超过 120s。 | `/root/.hermes/cron/output/c194bd1bc26e/2026-06-14_13-03-15.md`；`2026-06-14_13-19-35.md`。 | 同一任务可能半途被杀，留下部分 retain/文件变更。 | 禁止 cron apply；若必须，设置足够超时和 journal，且每次只做 dry-run 报告。 | 是，若继续 apply |

### P2

| ID | 模块 | 问题 | 证据 | 风险 | 建议 | 阻塞部署 |
|---|---|---|---|---|---|---|
| C-P2-01 | deploy docs | memory-cleanup manifest 注释称“手动触发”，但实际有 daily cron。 | `deploy/manifests/memory-cleanup.manifest:4`；`hermes cron list`。 | 运维误判运行方式。 | 更新部署说明/cron 说明。 | 否 |
| C-P2-02 | deploy | 首次部署防残留会删除目标目录中不在 manifest 的文件。 | `deploy/lib/common.sh:167-237`。 | 若目标目录有人手工放置运行态文件，deploy 会删除。 | 运行态日志/报告不要放项目目标目录；deploy 前必须 `plan` + `history`。 | 否 |
| C-P2-03 | mark_memory tests | tests 仅覆盖核心匹配/正则，未覆盖长 note 过滤失效与双写失败。 | `tests/test_mark_memory.py:1-247`。 | 回归无法捕获关键一致性问题。 | 增加长 note、DB/Hermes partial failure、navigation tail 过滤测试。 | 否 |
| C-P2-04 | daily-learn | Cron 与本地日志分散：cron output 和 `/root/.hermes/logs/daily-learn-*.log` 都需查。 | `daily_learn.sh:10-12`。 | 排障入口不统一。 | 在 cron 输出末尾打印完整日志路径和失败保留目录。 | 否 |

---

## 4. Cron 调度图与排障入口

| 时间 | 任务 | job id | 模式 | 写入风险 | 最近状态 | 排障入口 |
|---|---|---|---|---|---|---|
| 周一 09:30 | 聚类分析每周跑 | `834f94944665` | no-agent script | 高：wrapper 内多项 `--apply` | error：120s timeout | `/root/.hermes/cron/output/834f94944665/`，`/tmp/hindsight-cron-*.log`，`/root/.hermes/scripts/clustering-weekly.sh` |
| 每日 10:30 | 知识树维护每日 | `cd7b584da8e8` | no-agent script | 中：维护知识树 | ok | `/root/.hermes/cron/output/cd7b584da8e8/` |
| 每日 13:00 | memory-cleanup-daily-dryrun | `c194bd1bc26e` | no-agent script | 高：当前 wrapper 实际 `--apply` | ok（但曾 timeout） | `/root/.hermes/cron/output/c194bd1bc26e/`，`/root/.hermes/scripts/memory-cleanup-daily.sh` |
| 工作日 14:00 | 每日在线学习 | `919f31a5b42d` | no-agent script | 中：入知识树 | ok | `/root/.hermes/cron/output/919f31a5b42d/`，`/root/.hermes/logs/daily-learn-YYYYMMDD.log` |
| 周日 20:00 | 每周深度研究-知识树学习 | `b310f3366c4f` | agent job | 中：写研究成果/知识树 | ok | `/root/.hermes/cron/output/b310f3366c4f/` |
| 周一 11:00 | 知识导航评估基线 | `144efc44089b` | no-agent script | 低：评估输出/baseline | ok | `/root/.hermes/cron/output/144efc44089b/` |

---

## 5. 写入安全 Gate

### 禁止在 review 阶段执行

- `bash run.sh --apply`
- `python3 -m memory_cleanup ... --apply`
- `scripts/mark_memory.py mark ... --apply`
- `scripts/mark_memory.py unmark ... --apply`
- `scripts/mark_memory.py ... --apply-hermes --apply`
- `scripts/clustering-analysis-v3/scripts/cron_wrapper.sh`（默认包含 apply）
- `long_memory_governance.py --apply`
- `dedup_minhash.py --apply`
- `clustering_analysis.run(apply=True, ...)`
- `./deploy/deploy.sh deploy ...`

### apply 前必须满足

1. **Dry-run 报告**：输出待删除/压缩/迁移条目全文或 diff，至少保留最近一次 JSON 报告。
2. **人工确认**：USER.md 所有 remove/hindsight 必须逐条确认；MEMORY.md 命中 `AUTO_REMOVE_PATTERNS` 也必须列出全文确认。
3. **备份校验**：确认 `.bak` 文件存在且 `wc -c` 非 0；记录 restore 命令。
4. **事务/Journal**：Hindsight retain 与文件 remove 之间要有 journal；失败可恢复。
5. **Cron 禁止 apply**：定时任务只允许 dry-run + 报告；apply 由人工触发。
6. **长任务超时**：聚类/cleanup apply 若人工运行，必须设置合理 timeout 并写完整日志，不使用 120s no-agent 默认超时。
7. **标记一致性**：mark_memory 双写前先 search/check；如同步 Hermes，限制 note 长度并保存 before/after diff。

---

## 6. 验证命令与实际结果摘要

已执行的非写入命令：

```bash
cd /mnt/d/HermesProject
hermes cron list
```

结果摘要：8 个 active jobs；`聚类分析每周跑` 最近 error：`Script timed out after 120s`；`memory-cleanup-daily-dryrun` 最近 ok；`每日在线学习` 最近 ok。

```bash
cd /mnt/d/HermesProject/scripts/daily-learn
bash -n daily_learn.sh
```

结果：退出码 0，无语法错误。

```bash
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3
python3 scripts/mark_memory.py --help
python3 -m pytest tests/test_mark_memory.py -q
```

结果：help 正常；`23 passed in 0.04s`。

```bash
cd /mnt/d/HermesProject/scripts/memory-cleanup
PYTHONPATH=src python3 -m pytest tests -q
```

结果：`123 passed in 9.33s`。

```bash
cd /mnt/d/HermesProject/scripts/self-evolving
PYTHONPATH=src python3 -m pytest tests -q
```

结果：`1 failed, 46 passed`；失败为 `TestRefinementOperator.test_refinement_execute`，`refinement.py:293` 对 `RiskLevel` 执行 `max()` 触发 `TypeError: '>' not supported between instances of 'RiskLevel' and 'RiskLevel'`。

```bash
cd /mnt/d/HermesProject
./deploy/deploy.sh plan memory-cleanup
./deploy/deploy.sh plan daily-learn
./deploy/deploy.sh plan clustering-analysis-v3
./deploy/deploy.sh plan self-evolving
```

结果：plan 均成功，仅展开待部署文件；未执行 deploy。摘要：

- memory-cleanup：包含源码/config/run.sh，并预览 2 个 skill 文件。
- daily-learn：2 个文件（README.md、daily_learn.sh）。
- clustering-analysis-v3：包含 scripts/py/sh/config/README/skills。
- self-evolving：包含源码/scripts/config/README/skill。

```bash
# 只读排查 runtime cron 配置与 wrapper
python3 - <<'PY'
import json
j=json.load(open('/root/.hermes/cron/jobs.json'))
for job in j['jobs']:
    if job['id'] in ['834f94944665','c194bd1bc26e','919f31a5b42d']:
        print(job['id'], job['name'], job.get('script'), job.get('workdir'), job.get('last_status'), job.get('last_error'))
PY
```

结果摘要：聚类 job `last_status=error`，`last_error=Script timed out after 120s`；memory-cleanup job 名称 dryrun 但 runtime script 指向 `memory-cleanup-daily.sh`。

建议后续验证但本轮未执行（避免生产写入或长 LLM 调用）：

```bash
# memory-cleanup dry-run（会调用真实 LLM/读取真实 MEMORY/USER，但不写）
cd /mnt/d/HermesProject/scripts/memory-cleanup
PYTHONPATH=src python3 -m memory_cleanup run --config config/default.yaml --json --vote 1

# 聚类只读/干跑，不执行 cron_wrapper
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3
PYTHONPATH=src python3 - <<'PY'
from clustering_analysis.cli import run
run(apply=False, dry_run=True, cleanup=False, force=True, skip_entity=True, config_path='config/default.yaml')
PY

# cron error 日志入口
ls -lt /root/.hermes/cron/output/834f94944665/
ls -lt /tmp/hindsight-cron-*.log 2>/dev/null || true
```

---

## 7. 总结

本轮审查核实了 review plan 的全部 Agent C 疑点：

- `memory-cleanup --apply` 风险成立，且当前 runtime cron 名为 dryrun 却实际 `--apply`，应列为 P0。
- `AUTO_REMOVE_PATTERNS` 过宽，`方法论` 等词可能误删/误迁移长期价值条目。
- USER 保护主要靠 prompt，缺硬 gate；USER hindsight 迁移需要人工确认。
- `mark_memory` 与 navigation 标记类型一致，但存在 text append/embedding 不一致、DB/MEMORY 双写非事务、尾部 100 字符过滤失效风险。
- `daily-learn` 成功运行，但失败排障材料不足：TMP 删除 + builder 输出 `tail -5`。
- 聚类 cron error 已由真实 cron 状态证实，主要排障入口是 `/root/.hermes/cron/output/834f94944665/` 与 wrapper 的 `/tmp/hindsight-cron-*` 日志。
- self-evolving 未发现自动写记忆/代码路径，但当前单测有 1 个失败，需修复后再依赖该链路。
