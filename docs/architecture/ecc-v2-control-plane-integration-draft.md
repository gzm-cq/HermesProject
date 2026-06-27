# ECC v2.0 控制面能力与自进化飞轮整合分析（未定稿）

> 状态：未定稿 / 讨论稿  
> 日期：2026-06-13  
> 约束：本规划不修改 Hermes 源码、不修改 Hindsight 源码、不修改 Hindsight memory/schema。所有能力通过 plugins、scripts、cron、sidecar DB、现有 API、日志读取、pre_llm_call/post_llm_call 旁路实现。

---

## 1. 结论

ECC v2.0 可以整合进自进化飞轮，但整合对象不是 ECC 的 skill 包本身，而是其 v2.0 控制面范式：MCP Inventory、Session Adapter、Worktree Lifecycle、Deterministic Audit、Project-scoped Learning。

正确定位：ECC v2.0 提供“跨 harness 控制面标准化”；我们的自进化飞轮提供“数据回写 + 算子进化 + 记忆沉淀”。两者互补，ECC 应作为飞轮的外挂控制面信号层，而不是替代三大算子或改造 Hindsight 内部。

一句话：

```text
ECC-like Control Plane = 传感器层
Self-Evolving Flywheel = 数据沉淀与进化引擎
Hindsight = 原始经验池，保持不改
Sidecar = 经验解释层 / 控制面索引 / 飞轮信号层
```

---

## 2. 强约束边界

### 2.1 禁止项

- 不改 Hermes core。
- 不改 Hindsight daemon。
- 不改 Hindsight memory 表结构。
- 不给 Hindsight memory 增加 scope、project_id 等字段。
- 不改 Hindsight recall 内部逻辑。
- 不把大量控制面事件直接塞进 Hindsight。

### 2.2 允许项

- Hermes plugins。
- 外部 scripts。
- cron。
- 独立 SQLite / PostgreSQL sidecar 表。
- 读取 trace.log、session jsonl、Kanban DB、配置文件、进程状态。
- 使用现有 Hindsight recall / retain API。
- 在 knowledge-navigation 的 pre_llm_call 中对 Hindsight recall 候选进行过滤、降权、重排、融合。
- 在 post_llm_call / cron 中做观察、提取、记录。
- 只把稳定、高价值、归纳后的结论 retain 到 Hindsight。

---

## 3. ECC v2.0 与自进化飞轮对比

| 维度 | ECC v2.0 | 当前自进化飞轮 |
|---|---|---|
| 核心目标 | 统一不同 Agent harness 的操作面 | 从失败、使用、轨迹中自我改进 |
| 本质 | Control Plane | Data Flywheel |
| 关注对象 | session、MCP、worktree、hooks、commands、skills | Hindsight、知识树、失败模式、Kanban、三大算子 |
| 强项 | 标准化、可观测、跨工具一致性 | 记忆沉淀、反馈闭环、经验复用 |
| 短板 | 不真正学习，更多是工具框架 | 对环境状态、跨 harness 状态感知不足 |
| 整合方式 | 作为控制面信号源 | 作为数据沉淀与进化执行层 |

ECC 不是进化引擎，而是进化飞轮的传感器和观测标准。

---

## 4. 修正后的总架构

不要改现有三层自进化架构，而是在外侧增加横切控制面信号层。

```text
ECC-like Control Plane Sensors
  ├─ MCP Inventory
  ├─ Session Adapter
  ├─ Worktree / Workspace Lifecycle
  ├─ Harness Audit
  └─ Quality Gate

        ↓ 结构化事件

Sidecar DB / FailurePatternDB / trace indexes

        ↓ 触发

第 1 层：Revision / Recombination / Refinement 算子
第 2 层：Hindsight / 知识树 / 失败模式数据层
第 3 层：Kanban / Cron / Health-check 调度
```

实现上不新增“第四层核心架构”，而是作为三层架构的外挂控制面输入。

---

## 5. 核心整合项

### 5.1 MCP Inventory → 环境漂移感知（P0）

ECC 能力：统一读取 Claude / Codex / OpenCode 的 MCP 配置，归一化 stdio/http/sse，做 secret redaction，检测 fragmentation/drift。

在我们系统中的实现方式：独立 sidecar，不进入 Hindsight schema。

建议表：

```text
control_plane_mcp_servers
control_plane_mcp_sources
control_plane_mcp_drift_events
```

流程：

```text
cron 扫描 Hermes / Codex / Reasonix / OpenCode MCP 配置
  ↓
生成 hermes.mcp.v1 inventory
  ↓
发现 drift / duplicate / secret exposure / stale bridge
  ↓
写 sidecar events
  ↓
health-check 报告 / Kanban 任务 / Hindsight retain 摘要
```

建议事件类型：

```text
environment_drift.mcp_duplicate
environment_drift.mcp_transport_mismatch
environment_drift.mcp_secret_exposed
environment_drift.mcp_orphan_bridge
environment_drift.mcp_missing_server
```

注意：Hindsight 只存摘要性经验，例如“发现 axiom-wiki MCP 在 Reasonix 和 Hermes 存在 transport drift”，不存全量 MCP 明细。

---

### 5.2 Session Adapter → 跨 harness 轨迹统一格式（P1）

ECC 能力：`ecc.session.v1`，把 Claude history、dmux、Codex worktree、OpenCode session 统一成 snapshot。

我们应定义 `hermes.session.v1`：

```text
hermes.session.v1:
  source: hermes | kanban | codex | reasonix | claude | opencode
  session_id
  repo_root
  objective
  tools_used
  mcp_servers
  workers
  artifacts
  failures
  outcome
  health
```

读取源：

- Hermes session jsonl
- Kanban DB
- Reasonix / Codex 日志或配置
- ECC session-inspect 输出
- tmux / process 状态

用途：

- 判断任务卡死。
- 判断 worker 失败类型。
- 给 Revision / Recombination 提供轨迹输入。
- 支持 worker 能力画像。
- 最后只把高价值结论 retain 到 Hindsight。

---

### 5.3 Worktree / Workspace Lifecycle → Agent 工作区生命周期管理（P1）

ECC 状态机：

```text
main
detached
dirty
conflict
merge-ready
merged
stale
idle
```

我们不应只管 git worktree，而应扩展为 Agent Workspace Lifecycle：

```text
agent_workspace:
  git_worktree
  tmux_session
  kanban_task
  background_process
  deploy_artifact
  log_file
```

建议状态：

```text
active
dirty
merge-ready
conflict
merged
stale
orphan
needs-salvage
safe-to-clean
```

触发点：

- cron 定期扫描。
- system-health-check 调用。
- Kanban worker 完成后调用。
- 部署前检查。

输出：

```text
workspace_snapshots
workspace_events
workspace_cleanup_plans
```

作用：避免并行 agent 跑完后残留 worktree / tmux / background process，需要人工凭经验判断。

---

### 5.4 Harness Audit / Quality Gate → 飞轮评估信号（P1/P2）

ECC 能力：固定 rubric、脚本化评分、输出 top actions。

整合方式：作为评估信号，不作为核心架构。

```text
每次修改 / 部署 / cron
  ↓
跑 deterministic audit
  ↓
结果进入 baseline
  ↓
如果评分下降：
    - Revision 修复
    - Refinement 精简
    - Kanban retry
```

我们已有 system-health-check，ECC 的价值是“评分契约固定”。可以吸收思想，不必直接使用其脚本。

---

### 5.5 Project-scoped Learning → 项目级经验隔离（P2）

不能修改 Hindsight memory/schema，因此不能给 memory_units 加 project_id。改为外挂索引。

建议 sidecar 表：

```text
project_memory_index:
  memory_unit_id or text_hash
  project_id
  repo_root_hash
  scope: project | global
  confidence
  source_event_id
  promoted_at
```

召回流程：

```text
Hindsight recall(query)
  ↓
knowledge-navigation pre_llm_call 拿到候选
  ↓
sidecar 查 project_memory_index
  ↓
当前 project 命中 → 加权
其他 project 专属 → 降权
全局经验 → 保留
已解决/排除 → 降权或过滤
  ↓
注入 LLM
```

如果 Hindsight recall 返回 memory_unit_id，则使用 id；若拿不到 id，则使用 text_hash。

跨 2 个以上项目反复验证后，sidecar 中将 project scope 提升为 global scope。Hindsight 不感知 scope，scope 只在注入前生效。

---

## 6. 修正后的飞轮流程

```text
1. 控制面采集
   MCP inventory / session snapshot / workspace lifecycle / health-check

2. sidecar 结构化记录
   独立 DB，不写 Hindsight schema

3. 事件归因
   失败类型、漂移类型、卡死类型、重复类型

4. 算子触发
   Revision 修复失败
   Recombination 融合多轨迹
   Refinement 精简策略/报告

5. 外挂策略更新
   project_memory_index
   failure_pattern_db
   control_plane_events
   workspace_state

6. 注入前生效
   knowledge-navigation pre_llm_call 读取 sidecar，对 Hindsight recall 结果重排/过滤/融合

7. 稳定结论沉淀
   只把稳定、高价值、归纳后的结论 retain 到 Hindsight
```

---

## 7. 优先级

### P0：Hermes MCP Inventory

最值得先做。

理由：

- 真实痛点：Hermes / Codex / Reasonix / SSE bridge / gateway MCP 多套并存。
- 独立脚本即可实现。
- 不碰核心循环。
- 可直接接入 system-health-check。
- 能直接发现 SSE bridge、stdio MCP、Reasonix/Codex 配置漂移。

建议产物：

```text
scripts/control-plane/mcp_inventory.py
scripts/control-plane/control_plane.db
scripts/control-plane/schemas/hermes.mcp.v1.json
system-health-check 集成项
FailurePatternDB 事件写入
```

### P1：Session Snapshot

建议产物：

```text
scripts/control-plane/session_snapshot.py
scripts/control-plane/schemas/hermes.session.v1.json
scripts/control-plane/session_health.py
```

### P1：Workspace Lifecycle

建议产物：

```text
scripts/control-plane/workspace_lifecycle.py
workspace_state_report.json
safe_cleanup_plan.json
```

### P2：Project-scoped memory index

建议产物：

```text
scripts/control-plane/project_memory_index.py
pre_llm_call project-aware rerank
promotion cron
```

---

## 8. 暂不整合项

| ECC v2.0 能力 | 处理意见 | 原因 |
|---|---|---|
| hooks-runtime | 不整合 | Claude-specific，和 Hermes plugin/gateway hook 体系不同，易冲突 |
| 261 skills 全量吸收 | 不整合 | 会污染已有技能库，只抽方法论 |
| continuous-learning-v2 全套实现 | 不照搬 | 与 Hindsight / 知识树 / skill curator 重复，只吸收 project-scoped 思想 |
| full profile | 暂不使用 | developer profile 已足够，full 增加低频噪声 |

---

## 9. 当前判断

ECC v2.0 不能直接提升系统“智能”，但能提升自进化飞轮的“可观测性”。

自进化飞轮最怕没有稳定输入信号。ECC 的控制面范式正好补这个信号层：

```text
MCP 漂移是信号
session 卡死是信号
workspace stale 是信号
quality gate 下降是信号
跨项目重复 instinct 是信号
```

这些信号进入 sidecar / FailurePatternDB / trace indexes 后，才会真正变成进化数据。

最终判断：可以整合，而且应该整合；但只能作为外挂控制面信号层进入，不修改 Hermes/Hindsight 源码，不修改 Hindsight memory/schema。

---

## 10. 未定事项

以下内容未定稿，后续需要继续验证：

1. `hermes.mcp.v1` schema 的字段是否完全复用 ECC `ecc.mcp.v1`，还是做 Hermes 专用扩展。
2. Sidecar DB 使用 SQLite 还是 shared-postgres 独立 schema。
3. MCP Inventory 是否只接 system-health-check，还是同时接 Kanban 自动任务。
4. Session Snapshot 是否先覆盖 Hermes/Kanban，再扩展 Codex/Reasonix。
5. project_memory_index 如何稳定获得 memory_unit_id；若只能 text_hash，需评估碰撞和文本变体问题。
6. promotion cron 的触发规则：跨项目次数、时间窗口、人工确认是否需要。
7. FailurePatternDB 与已有 self-evolving 算子输出格式如何统一。

---

## 11. 当前进展确认

当前讨论仍在正向推进：

- 已完成 ECC v2.0 能力盘点。
- 已确认不全量吸收 ECC skills。
- 已确认最有价值的是控制面范式。
- 已纠正边界：不改 Hermes / Hindsight 源码，不改 Hindsight memory/schema。
- 已形成未定稿整合方向：外挂控制面信号层 + sidecar DB + knowledge-navigation 注入前重排。
- 下一步建议先细化 P0 MCP Inventory 的 schema 与扫描路径。
