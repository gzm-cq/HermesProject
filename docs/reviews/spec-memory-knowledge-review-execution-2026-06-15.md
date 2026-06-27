# SPEC：记忆/知识系统 Review 执行计划

> **文档状态：历史 SPEC / 审查执行说明**  
> 本文定义当轮 review 的执行方法，不代表当前待办。最终状态见 `03-post-fix-audit-2026-06-15.md`。


> 环境：WSL2，源码根目录 `/mnt/d/HermesProject`
> 来源：`docs/review-plan-memory-knowledge-system-2026-06-15.md`
> 执行方式：多 agent 并行只读审查 + 安全验证；不改业务代码、不部署、不执行生产写入。

## S — Situation（现状）

| 组件 | 当前状态 | 审查方式 |
|---|---|---|
| 知识导航插件 | pre_llm_call 融合 Hindsight + 知识树注入 | Agent A 深读源码 + 单测/import smoke |
| 知识树插件 | post_llm_call 在线提取知识点，public_api 给导航调用 | Agent A 深读源码 + 单测/import smoke |
| 知识树建树 | scan/analyze/split/admit/place 离线入树 | Agent B 深读源码 + dry-run/DB schema |
| 聚类分析 v3 | Hindsight 聚类、实体、因果链、标记、富化 | Agent B 深读源码 + dry-run/DB schema |
| 自进化/在线学习/记忆清理/记忆修正 | cron/脚本链路维护记忆与知识 | Agent C 深读源码 + cron/写入风险审查 |
| 端到端链路 | 用户→导航→LLM→知识树插件→知识树/Hindsight | Agent D 基于 A/B/C 做回归方案和 gate |

## P — Problem（核心问题）

1. `聚类分析每周跑` 最近 cron 状态为 error，说明生产调度链路已出现实际失败。
2. `memory-cleanup --apply`、`dedup_minhash --apply`、`mark_memory --apply` 都是高风险写入链路，必须明确安全 gate。
3. 知识树插件新增知识点是否写入 `k_vector` 未验证；若不写，会导致“入库但不可召回”。
4. 聚类和知识树 builder 的 DB schema/ON CONFLICT/index 可能与生产库不一致，可能导致 apply 部分失败。
5. 插件间存在隐式依赖、环境变量命名不一致、测试与源码不一致等维护风险。
6. 当前 review 文档是计划，需要实际执行成 A/B/C/D 报告、汇总表和验证日志。

## E — Evaluation & Priority

| # | 任务 | ROI | 工时 | 类型 | 产出 |
|---|---|:---:|:---:|---|---|
| P0-1 | 并行执行 A/B/C 三条只读 review | 🔴 不做无法判断真实风险 | ~15-30min | 审查 | A/B/C review md |
| P0-2 | 查询 cron 当前状态与失败任务信息 | 🔴 已有真实 error | ~5min | 验证 | verification log |
| P0-3 | 查询 DB schema/index/vector 完整性 | 🔴 决定 apply 是否安全 | ~5-10min | 验证 | schema gate 输出 |
| P0-4 | 执行 import smoke + 安全单测 | 🔴 决定插件是否可加载 | ~10-20min | 验证 | test log |
| P1-1 | 基于 A/B/C 做 D 端到端回归计划 | 🟡 防止局部修复破坏链路 | ~10min | 设计 | D review md |
| P1-2 | 汇总 P0/P1/P2 问题与修复顺序 | 🟡 转为可执行修复计划 | ~10min | 计划 | 00 summary + 01 fix plan |

## C — Criteria（验收标准）

必须生成以下文件：

1. `docs/reviews/A-plugin-review.md`
2. `docs/reviews/B-builder-clustering-review.md`
3. `docs/reviews/C-runtime-memory-review.md`
4. `docs/reviews/D-e2e-regression-review.md`
5. `docs/reviews/00-review-summary.md`
6. `docs/reviews/01-fix-plan.md`
7. `docs/reviews/02-verification-log.md`

必须执行并记录：

- `cronjob list` 或等价 Hermes cron 状态。
- 插件 import smoke。
- 各模块安全单测/语法检查，失败也记录真实输出。
- 只读 DB schema/index/vector 查询；若 DB 工具不可用，记录阻塞原因。

禁止执行：

- `memory-cleanup --apply`
- `dedup_minhash.py --apply`
- `long_memory_governance.py --apply`
- `clustering_analysis.run(apply=True)`
- `knowledge_tree_builder run` 非 dry-run 且连接生产 DB
- `mark_memory.py mark ... --apply`
- `deploy.sh deploy ...`

## 执行顺序

1. 创建本 SPEC 文件。
2. 并行启动 Agent A/B/C，写入各自报告。
3. 验证报告文件存在。
4. 启动 Agent D，读取 A/B/C 形成端到端回归方案。
5. 执行安全验证命令，记录到 `02-verification-log.md`。
6. 生成 `00-review-summary.md` 和 `01-fix-plan.md`。
7. 汇报文件路径、关键 P0/P1 结论和阻塞项。