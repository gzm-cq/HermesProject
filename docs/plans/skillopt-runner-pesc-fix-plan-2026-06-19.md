# PESC 修复计划：SkillOpt-Runner 有效优化链路

**目标**：修复 `skillopt-nightly-run` 目前“cron 成功但没有任何 skill 优化”的代码问题，让它能从 Hermes 当前会话存储中增量采集真实对话、正确挖掘任务、调用 SkillOpt-Sleep，并在验证门控通过后准确写回目标 skill。

**范围**：仅修改 `/mnt/d/HermesProject/scripts/skillopt-runner/` 项目源码、测试、配置和必要文档；不修改 Hermes 核心源码，不直接改 `/root/.hermes/` 运行时文件。修完后按 HermesProject 流程：源码修改 → 本地测试 → review → 用户确认 → deploy → 运行时验证。

**当前证据时间**：2026-06-19 15:00 cron 输出与运行时文件检查。

---

## P — Problem（问题）

### P0-1：SkillOpt cron 空跑，没有进入优化循环

**现象**：`skillopt-nightly-run` cron 状态为 OK，但输出：

```text
Processed 0 complete sessions from Hermes
Harvested 0 sessions from Hermes
Mined 0 recurring tasks
No tasks mined, exiting
```

**根因**：`skillopt_runner.py` 只扫描：

```python
/root/.hermes/sessions/*.jsonl
/root/.hermes/sessions/session_*.json
```

但当前 Hermes 实际新会话主要在：

```text
/root/.hermes/state.db       # sessions/messages SQLite 主库
/root/.hermes/sessions/sessions.json  # 仅活跃 session 元数据，不含完整 messages
```

因此增量采集拿不到 2026-06-17 之后的新对话消息。

---

### P0-2：空任务退出时不推进 `last_run_iso`，导致重复扫描旧窗口

当前代码只在 accepted edits 后更新：

```python
state['last_run_iso'] = datetime.now(timezone.utc).isoformat()
save_state(state)
```

但如果 `mine()` 返回 0 tasks，会提前：

```python
print('No tasks mined, exiting')
return 0
```

导致 `state.json` 仍停在旧时间：

```json
"last_run_iso": "2026-06-17T15:54:48.579516+00:00"
```

下一次 cron 又继续扫同一窗口，形成空跑循环。

---

### P0-3：accepted edit 无法可靠映射回 skill 名

当前 apply 代码：

```python
skill_name = pathlib.Path(edit.target).parent.name
```

但 SkillOpt-Sleep 的 `EditRecord.target` 可能是 `"skill"` / `"memory"` 这类目标标识，不一定是 `/.../<skill>/SKILL.md` 文件路径。即使后续有 accepted edits，也可能映射为空或错误 skill。

---

### P1-1：负反馈匹配污染，导致 `hermes-agent` 等基础设施技能异常高分

当前状态：

```text
hermes-agent 负反馈 904
```

当前匹配逻辑：

```python
if nl in lp:
```

这会让 `hermes-agent`、`agent`、`system` 等短词/泛词在普通讨论中被大量误计，导致精排长期偏向基础设施技能，而非真实需要优化的业务 skill。

---

### P1-2：中文负反馈关键词过宽

当前代码仍包含：

```python
'不能'
```

这类词在中文日常询问中频繁出现，不一定表示对 skill 的负反馈，容易误触发。

---

### P1-3：`load_config(**our_cfg)` 传入 SkillOpt-Sleep 不认识的 runner 私有键

`config.yaml` 中存在 runner 私有配置：

```yaml
top_k: 5
```

未来还会显式加入：

```yaml
denylist_patterns:
max_sessions:
```

这些不应直接透传给 SkillOpt-Sleep 的 `load_config(**our_cfg)`，否则取决于上游 config 实现，可能报错或产生不可预期行为。

---

### P1-4：`projects` 传 SKILL.md 文件路径，可能不符合 SkillOpt-Sleep 项目语义

当前代码：

```python
skill_paths.append(str(p))  # p = .../SKILL.md
our_cfg['projects'] = skill_paths
```

如果 SkillOpt-Sleep 期望的是项目目录或可优化目标目录，而不是单个 `SKILL.md` 文件，会导致 task/replay/patch 语义错位。

---

### P1-5：运行模型配置文档/配置可能陈旧

当前 `scripts/skillopt-runner/config.yaml` 写的是：

```yaml
model: "doubao-auto"
```

但当前 Hermes 统一模型路由偏好是 LiteLLM 网关 + `sensenova-6.7-flash-lite`。需要按当前配置源验证后更新，避免 SkillOpt 使用旧模型名。

---

## E — Evidence（证据）

### 真实 cron 输出

文件：

```text
/root/.hermes/cron/output/beb1ca8792e1/2026-06-19_15-00-26.md
```

关键行：

```text
Processed 0 complete sessions from Hermes
Harvested 0 sessions from Hermes
Mined 0 recurring tasks
No tasks mined, exiting
```

### 当前会话真实存储

`/root/.hermes/sessions/` 最新文件：

```text
sessions.json
```

但它只保存活跃 session 元数据，不包含完整 `messages`。

SQLite 主库：

```text
/root/.hermes/state.db
```

表结构已确认：

```text
sessions(id, source, user_id, started_at, ended_at, message_count, title, ...)
messages(session_id, role, content, tool_name, timestamp, ...)
```

当前最新 session 示例：

```text
20260619_150507_09831a feishu 2026-06-19T15:05:07 message_count=143
20260619_130807_23df4f8a feishu 2026-06-19T13:08:07 message_count=156
```

### 现有测试状态

当前 `skillopt-runner` 测试通过：

```text
45 passed in 0.18s
```

但测试只覆盖旧 `*.jsonl` / `session_*.json` 文件格式，未覆盖 `state.db`。

---

## S — Solution（修复方案）

### 总体策略

按 TDD 修复，先补失败测试，再改实现：

```text
RED: 新增失败测试
GREEN: 最小实现通过测试
REFACTOR: 简化公共解析函数
VERIFY: 全量 pytest + dry-run + cron run 验证
REVIEW: 提审，不直接 deploy
DEPLOY: 用户确认后 deploy skillopt-runner
```

---

## 修复任务清单

### P0-A：新增 SQLite state.db harvest 支持

| 字段 | 内容 |
|---|---|
| 来源 | P0-1 |
| 当前状态 | 只扫描 `*.jsonl` / `session_*.json`，拿不到当前 Hermes 新会话 |
| 目标状态 | 支持从 `/root/.hermes/state.db` 的 `sessions/messages` 表构造 `SessionDigest` |
| 期望目标 | 每日 cron 能采集当天真实对话，不再 `Harvested 0 sessions` 空跑 |
| 改动位置 | `scripts/skillopt-runner/skillopt_runner.py` |
| 测试位置 | `scripts/skillopt-runner/tests/test_skillopt_runner.py` |
| 工作量 | 0.5 天 |
| 前置依赖 | 无 |

#### 设计

新增函数：

```python
def harvest_state_db_sessions(since_iso: Optional[str], max_sessions: int = 0) -> list[SessionDigest]:
    ...
```

要点：

1. 使用 Python 标准库 `sqlite3`，不新增依赖。
2. 读取 `HERMES_HOME / 'state.db'`。
3. `sessions.started_at` / `sessions.ended_at` 是 Unix timestamp float；转 ISO 字符串。
4. 增量过滤逻辑：使用 `COALESCE(ended_at, started_at)` 与 `since_iso` 对比。
5. `messages` 按 id/timestamp 排序，构造：
   - `user_prompts`
   - `assistant_finals`
   - `tools_used`
   - `feedback_signals`
6. 只纳入 `n_user_turns > 0` 的 session。
7. DB 不存在或 schema 不兼容时 warning 后降级为空，不影响旧文件格式。

#### RED 测试

新增测试：

```python
def test_state_db_format(self, tmp_hermes):
    # 构造 tmp_hermes/state.db，写 sessions/messages
    # 调 harvest_hermes_sessions(None)
    # 断言采集到 1 个 digest，含 user prompt / assistant / tool
```

新增测试：

```python
def test_state_db_incremental_filter(self, tmp_hermes):
    # old/new 两个 sqlite session
    # since_iso 只返回 new
```

新增测试：

```python
def test_state_db_and_file_sources_dedup_by_session_id(self, tmp_hermes):
    # 同一个 session 同时出现在 state.db 和 session_*.json
    # 只保留一份，优先 state.db 或较新的 ended_at
```

---

### P0-B：空任务也推进 `last_run_iso`

| 字段 | 内容 |
|---|---|
| 来源 | P0-2 |
| 当前状态 | `mine()` 为 0 时直接退出，不更新 state 时间点 |
| 目标状态 | 只要 harvest + rank 成功，即使无 tasks/无 accepted edits，也推进 `last_run_iso` |
| 期望目标 | cron 不会每天重复扫描同一批旧会话 |
| 改动位置 | `scripts/skillopt-runner/skillopt_runner.py` |
| 测试位置 | `scripts/skillopt-runner/tests/test_skillopt_runner.py` |
| 工作量 | 0.25 天 |
| 前置依赖 | P0-A 可并行 |

#### 设计

引入小函数：

```python
def advance_last_run(state: dict, *, now: datetime | None = None) -> None:
    state['last_run_iso'] = (now or datetime.now(timezone.utc)).isoformat()
```

调用位置：

1. `eligible` 为空：保留现有推进。
2. `top_skill_names` 为空：推进。
3. `tasks` 为空：推进。
4. `run_sleep_cycle` 完成但无 accepted edits：推进。
5. `run_sleep_cycle` 抛异常：不推进，保留下次重试。
6. apply 有失败：不推进或仅在全部成功时推进，避免丢失待重试窗口。

#### RED 测试

新增 main 级单测或抽函数测试：

```python
def test_no_tasks_mined_advances_last_run(monkeypatch, tmp_hermes):
    # mock mine 返回 []
    # 调 main()
    # 断言 state.json last_run_iso 被更新
```

---

### P0-C：建立 accepted edits → skill name 的明确映射

| 字段 | 内容 |
|---|---|
| 来源 | P0-3 |
| 当前状态 | 从 `edit.target` 当路径解析 skill name，不可靠 |
| 目标状态 | 在传给 SkillOpt 的目标中保留 `target_to_skill` 映射；apply 时只接受可解析的 SKILL.md 路径或显式映射 |
| 期望目标 | 有 accepted edits 时能准确写回对应 skill，不会写错或静默失败 |
| 改动位置 | `scripts/skillopt-runner/skillopt_runner.py` |
| 测试位置 | `scripts/skillopt-runner/tests/test_skillopt_runner.py` |
| 工作量 | 0.5 天 |
| 前置依赖 | P1-B 项目路径语义确认 |

#### 设计

新增函数：

```python
def resolve_edit_skill_name(edit_target: str, target_to_skill: dict[str, str]) -> str | None:
    ...
```

解析顺序：

1. 若 `edit_target` 在 `target_to_skill` 中，直接返回。
2. 若 `edit_target` 是路径且 basename 是 `SKILL.md`，返回 parent.name。
3. 若 `edit_target` 是 skill 名且 `get_skill_path(edit_target)` 存在，返回它。
4. 否则返回 None，记录 error，不写入。

#### RED 测试

```python
def test_resolve_edit_skill_name_from_mapping(): ...
def test_resolve_edit_skill_name_from_skill_md_path(): ...
def test_resolve_edit_skill_name_rejects_generic_target_skill(): ...
```

---

### P1-A：修复负反馈 skill 匹配污染

| 字段 | 内容 |
|---|---|
| 来源 | P1-1 |
| 当前状态 | `if nl in lp` 子串匹配导致泛词技能污染 |
| 目标状态 | 英文技能名用 token/词边界匹配；中文/短词必须更严格 |
| 期望目标 | 精排榜单反映真实负反馈，不再被 `hermes-agent=904` 这类污染主导 |
| 改动位置 | `scripts/skillopt-runner/skillopt_runner.py` |
| 测试位置 | `scripts/skillopt-runner/tests/test_skillopt_runner.py` |
| 工作量 | 0.5 天 |
| 前置依赖 | 无 |

#### 设计

新增函数：

```python
def message_mentions_skill(message: str, skill_name: str) -> bool:
    ...
```

规则：

1. 对 `skill_name` 做规范化：`-`、`_`、`/` 拆为 token。
2. 英文 token 必须按词边界匹配，不能只靠子串。
3. 对过短 token（如 `ai`、`ml`、`agent`、`system`）不单独计命中，除非完整 skill name 短语命中。
4. 中文 skill 名或描述匹配暂不做，避免误伤；后续如需要可接入 skill_matcher 的中英双语 tokenizer。

#### RED 测试

```python
def test_hermes_agent_not_matched_by_generic_agent_word(): ...
def test_exact_skill_name_matches_hyphenated_name(): ...
def test_category_skill_name_matches_full_name_not_category_only(): ...
```

---

### P1-B：收紧中文负反馈词表

| 字段 | 内容 |
|---|---|
| 来源 | P1-2 |
| 当前状态 | `不能` 等宽泛词触发负反馈 |
| 目标状态 | 删除宽泛词，保留明确抱怨短语 |
| 期望目标 | 日常询问不再被误判为负反馈 |
| 改动位置 | `scripts/skillopt-runner/skillopt_runner.py` |
| 测试位置 | `scripts/skillopt-runner/tests/test_skillopt_runner.py` |
| 工作量 | 0.25 天 |
| 前置依赖 | 无 |

#### 设计

删除或降级：

```python
'不能'
```

保留：

```python
'不对', '错了', '没改好', '没修好', '仍然不对', '不正确', '还是不行', '不行', '不好', '不可以', '失败'
```

#### RED 测试

```python
def test_chinese_cannot_question_is_not_negative():
    assert _detect_feedback('这个功能能不能配置成每天运行') == []
```

---

### P1-C：隔离 runner 私有 config 与 SkillOpt-Sleep config

| 字段 | 内容 |
|---|---|
| 来源 | P1-3 |
| 当前状态 | `load_config(**our_cfg)` 可能传入 `top_k` 等无效 key |
| 目标状态 | `runner_cfg` 与 `sleep_cfg` 分离，只把 SkillOpt-Sleep 支持的 key 传给 `load_config` |
| 期望目标 | 新增 runner 参数不会破坏 SkillOpt-Sleep config 解析 |
| 改动位置 | `scripts/skillopt-runner/skillopt_runner.py`, `scripts/skillopt-runner/config.yaml` |
| 测试位置 | `scripts/skillopt-runner/tests/test_skillopt_runner.py` |
| 工作量 | 0.5 天 |
| 前置依赖 | 无 |

#### 设计

新增：

```python
RUNNER_CONFIG_KEYS = {'top_k', 'denylist_patterns', 'max_sessions', 'reset_polluted_state'}

def split_config(raw: dict) -> tuple[dict, dict]:
    runner_cfg = {k: raw[k] for k in raw if k in RUNNER_CONFIG_KEYS}
    sleep_cfg = {k: raw[k] for k in raw if k not in RUNNER_CONFIG_KEYS}
    return runner_cfg, sleep_cfg
```

调用：

```python
runner_cfg, sleep_cfg = split_config(our_cfg)
cfg = load_config(**sleep_cfg)
```

---

### P1-D：确认并修正 `projects` 目标语义

| 字段 | 内容 |
|---|---|
| 来源 | P1-4 |
| 当前状态 | 传入 `SKILL.md` 文件路径 |
| 目标状态 | 根据 SkillOpt-Sleep 实际语义传入正确目标；若不支持单 skill 文件，封装 staging project |
| 期望目标 | SkillOpt-Sleep 能真正针对 top-K skill 生成候选编辑 |
| 改动位置 | `scripts/skillopt-runner/skillopt_runner.py` |
| 测试位置 | `scripts/skillopt-runner/tests/test_skillopt_runner.py` |
| 工作量 | 0.5-1 天 |
| 前置依赖 | 需要读 `/root/.hermes/skillopt-sleep` 的 `config.py` / `cycle.py` / patch 语义 |

#### 方案选型

优先级：

1. 如果 SkillOpt-Sleep 支持文件路径项目：保留 SKILL.md 路径，补测试和映射。
2. 如果只支持目录：传 skill 目录而不是 `SKILL.md`。
3. 如果项目结构必须有特定文件：为 top-K skill 生成临时 staging project，并维护 staging target → 原 skill 映射。

---

### P1-E：更新模型配置为当前 LiteLLM 主模型

| 字段 | 内容 |
|---|---|
| 来源 | P1-5 |
| 当前状态 | `model: doubao-auto` 可能陈旧 |
| 目标状态 | 读取或写明当前模型路由；若当前主模型是 `sensenova-6.7-flash-lite`，同步配置和 README |
| 期望目标 | SkillOpt 优化调用走当前稳定模型，不因旧模型名失败 |
| 改动位置 | `scripts/skillopt-runner/config.yaml`, `README.md` |
| 测试位置 | 配置读取测试 / dry-run 实测 |
| 工作量 | 0.25 天 |
| 前置依赖 | 验证当前 LiteLLM 可用模型名 |

---

## C — Criteria（验收标准）

### 单元测试验收

在源码目录运行：

```bash
cd /mnt/d/HermesProject/scripts/skillopt-runner
PYTHONPATH=. python3 -m pytest tests -q
```

要求：

```text
全部通过，且新增测试覆盖：
- state.db harvest
- incremental filter
- no tasks mined advances last_run_iso
- edit target skill name resolution
- message_mentions_skill 边界匹配
- 中文 `不能` 不误判
- config split 不透传 runner 私有 key
```

---

### 本地集成 dry-run 验收

部署前可在源码目录做只读/临时状态验证，不改真实 skill：

```bash
cd /mnt/d/HermesProject/scripts/skillopt-runner
PYTHONPATH=. python3 skillopt_runner.py --dry-run --since-hours 24
```

要求输出不再是：

```text
Processed 0 complete sessions from Hermes
Harvested 0 sessions from Hermes
```

而应至少能从 `state.db` 读取 2026-06-19 的 Feishu/CLI session。

---

### 部署计划验收

```bash
cd /mnt/d/HermesProject
./deploy/deploy.sh plan skillopt-runner
```

要求：

- 包含 `skillopt_runner.py`、`config.yaml`、`README.md`。
- 不包含 `state.json`、`__pycache__`、`.skillopt-sleep`、日志、备份。
- 如果新增测试文件，确认测试不部署或部署无害；当前 manifest `*.py` 只匹配项目根 `*.py`，不递归 tests，保持即可。

---

### 运行时验证

用户确认后部署：

```bash
cd /mnt/d/HermesProject
./deploy/deploy.sh deploy skillopt-runner --yes
```

部署后验证：

```bash
python3 -m py_compile /root/.hermes/skillopt-runner/skillopt_runner.py
/root/.hermes/hermes-agent/venv/bin/python /root/.hermes/skillopt-runner/skillopt_runner.py --dry-run --since-hours 24
```

要求：

1. 能读取 state.db 新 session。
2. 不再 `Harvested 0 sessions`。
3. 若 `Mined 0 recurring tasks`，也要更新 `state.json.last_run_iso`。
4. 若有 accepted edits，能解析具体 skill name；dry-run 不写回。

---

### cron 验收

手动触发一次：

```text
cronjob run skillopt-nightly-run
```

或通过工具 `cronjob(action='run', job_id='beb1ca8792e1')`。

要求：

- cron status 为 OK。
- 输出能看到从 state.db harvest 的 session 数。
- 不再长期停留在旧 `last_run_iso`。
- 若无任务，明确输出“no tasks mined, state advanced”。

---

## 执行顺序

### Batch 0：提交/隔离当前文档改动

当前工作区已有 3 个文档改动：

```text
docs/README.md
docs/hermes_project/cron-scheduler-design.md
docs/hermes_project/project-profile.md
```

先提交或暂存这批文档，避免与代码修复混在一个 diff。

---

### Batch 1：TDD 修 P0-A / P0-B

1. 新增 state.db harvest 测试，确认失败。
2. 实现 SQLite harvest。
3. 新增 no-task advance 测试，确认失败。
4. 实现 state 推进逻辑。
5. 跑全量测试。

---

### Batch 2：TDD 修 P0-C / P1-A / P1-B

1. 新增 edit target resolution 测试。
2. 实现 `resolve_edit_skill_name()`。
3. 新增 skill mention 边界测试。
4. 实现 `message_mentions_skill()` 并替换 `if nl in lp`。
5. 新增中文 `不能` 误判测试。
6. 收紧词表。
7. 跑全量测试。

---

### Batch 3：配置与 SkillOpt-Sleep 语义修复

1. 读 `/root/.hermes/skillopt-sleep/skillopt_sleep/config.py` 和 `cycle.py`，确认 `projects` 语义。
2. 新增 config split 测试。
3. 实现 `split_config()`。
4. 根据实际语义修正 `projects` 传参。
5. 更新 `config.yaml` 模型名和显式 `denylist_patterns`。
6. 跑测试 + dry-run。

---

### Batch 4：Review / Deploy / Runtime 验证

1. `git diff --stat` 和源码 review。
2. `pytest` 全绿。
3. `deploy plan skillopt-runner` 验证 manifest。
4. 提审给用户。
5. 用户确认后 deploy。
6. 部署后运行 dry-run 和 cron 手动触发验证。

---

## 不做事项

- 不修改 Hermes 核心源码。
- 不直接编辑 `/root/.hermes/skillopt-runner/skillopt_runner.py`。
- 不直接清空 `state.json`，除非作为独立运维动作经用户确认。
- 不在非 dry-run 情况下手动触发真实 skill 写回，除非先完成 review 并获得用户确认。
- 不把完整统一调度框架混入本次修复；本次只修 SkillOpt-Runner 代码链路。

---

## 风险与处理

| 风险 | 处理 |
|---|---|
| `state.db` 很大，直接全量读慢 | 增量 SQL 按 `COALESCE(ended_at, started_at)` 过滤，默认只读窗口内 session |
| 当前活跃 session `ended_at IS NULL` | 用 `started_at` 或最新 message timestamp 作为 ended_at；避免跳过活跃会话 |
| session 内容过长 | harvest 只构造 prompts/finals，不把 tool 大输出完整纳入 task mining；必要时截断 tool content |
| 旧 state 负反馈已污染 | 先修新逻辑；是否清洗旧 `state.json` 作为单独运维动作确认 |
| SkillOpt-Sleep `projects` 语义不支持单 skill | 使用 staging project 或传 skill 目录，并维护映射 |

---

## 最终交付物

- 修复后的：
  - `scripts/skillopt-runner/skillopt_runner.py`
  - `scripts/skillopt-runner/config.yaml`
  - `scripts/skillopt-runner/README.md`（如配置/运行方式变更）
  - `scripts/skillopt-runner/tests/test_skillopt_runner.py`
- 测试输出：`pytest` 全绿。
- 部署验证：runtime dry-run 不再 harvest 0。
- cron 验证：`skillopt-nightly-run` 手动触发后不再空跑。
