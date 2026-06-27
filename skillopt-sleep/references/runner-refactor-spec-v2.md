# SPEC: SkillOpt-Runner 数据分离 + 重试池重构

**项目**：skillopt-runner + skillopt-sleep（共改 ~60 行）
**版本**：v2（含 review 修复）
**状态**：草稿

---

## 1. 验收标准

| # | 标准 | 测量方式 | 改前 | 改后 |
|---|------|---------|------|------|
| AC1 | 每个 skill 只用自己关联的 session 提 task | `rank_skills` 返回 `skill_sessions`，按 skill 名验证 | 5 个 skill 共用同一批 task | `skill_a` 的 task 中不含 `skill_b` 的 session 内容 |
| AC2 | 重试池不被同轮后续 batch 覆盖 | state.json 的 `failed_tasks[]` 追加而非赋值 | 永远赋值覆盖 | 同一轮内不同 batch 的 task 各自保留 |
| AC3 | 无新 session 时重试池仍可执行 | 手动 mock 空 digests + 有 retry pool → 不进 skip | pop 后跳过 | 跳过 digest check，进入 batch loop |
| AC4 | timeout task 不参与 gate 计分 | aggregate 排除 `fail_reason` 非空 → baseline 更准 | timeout task 0.0 拉低平均 | 只算有效 task |
| AC5 | retry_count 正确递增 | 同一 task 连 fail 3 次 → 被淘汰 | 永远卡在 1 | 第 4 次加载时因 retry=4 被过滤 |
| AC6 | 旧 state 向后兼容 | 已有 `failed_tasks` 无 `retry_count` 字段 → 加载正常 | N/A | `from_dict` 过滤未知字段 → 安全降级 |

---

## 2. 改动顺序（依赖关系）

```
改动 1（rank_skills 返回 skill_sessions） ← 无依赖
    ↓
改动 2（main/_phase_optimize 接入 skill_sessions） ← 依赖改动 1
    ↓
改动 3（_optimize_one_skill 跳过 digest check + append + retry_map） ← 依赖改动 2
    ↓
改动 4（aggregate_scores 过滤 timeout） ← 无依赖，可最后或并行
```

**部署顺序**：
1. 先改 `skillopt-runner`（改动 1+2+3）→ 部署
2. 再改 `skillopt-sleep`（改动 4）→ 部署

**注意**：改动 4 虽然无依赖，但先部署 runner（不含改动 4）不影响运行（只是 gate 不排除 timeout）。
先部署 sleep（改动 4）不影响已有功能（只是 `aggregate_scores` 更严格）。
两者互不阻塞。但建议**先 runner 再 sleep**，因为 runner 上线后若 gate 有变化可先隔离问题。

---

## 3. 代码变更

### 改动 1：`rank_skills` 收集 `skill_sessions`（+12 行）

**文件**：`scripts/skillopt-runner/skillopt_runner.py`
**函数**：`rank_skills()` (line 490-586)

**签名变更**：
```python
# 改前
def rank_skills(eligible, digests, state, top_k) -> Tuple[list, dict]:
# 改后
def rank_skills(eligible, digests, state, top_k) -> Tuple[list, dict, Dict[str, list]]:
```

**代码变更**：

① 函数 `rank_skills` 前部，获取 `eligible_names` 后追加：

```python
# 改前：无
# 改后：
skill_sessions: dict[str, list[SessionDigest]] = {n: [] for n in eligible_names}
```

② 在 `for name in eligible_names:` 循环内，收集 session（**只收集负反馈**）：

```python
# 改前：
if message_mentions_skill(prompt, name):
    skill_total[name] += 1
    if has_neg:
        skill_neg[name] += 1
        new_neg += 1

# 改后：
if message_mentions_skill(prompt, name):
    skill_total[name] += 1
    if has_neg:
        skill_neg[name] += 1
        new_neg += 1
        if (not skill_sessions[name] or
            skill_sessions[name][-1].session_id != d.session_id):
            skill_sessions[name].append(d)
```

③ 返回值：

```python
# 改前：
return top, state
# 改后：
return top, state, skill_sessions
```

**测试覆盖**：`test_rank_skills_returns_skill_sessions()`

---

### 改动 2：`_phase_optimize` 按 skill 分发（+15 行）

**文件**：`scripts/skillopt-runner/skillopt_runner.py`
**函数**：`_phase_optimize()` (line 822-878)

**签名变更**：
```python
# 改前
def _phase_optimize(top_scored, skill_last_run, state, all_digests, sleep_cfg, runner_cfg, *, dry_run):
# 改后
def _phase_optimize(top_scored, skill_last_run, state, skill_sessions, sleep_cfg, runner_cfg, *, dry_run):
```

**代码变更**（对应 `scripts/skillopt-runner/skillopt_runner.py:840-861`）：

```python
# 改前：共享 mine
all_tasks = mine(all_digests, max_tasks=len(all_digests))
batches = [all_tasks[i:i + batch_size] for i in range(0, len(all_tasks), batch_size)]

for rank, (skill_name, *_) in enumerate(top_scored, 1):
    fut = ex.submit(_optimize_one_skill, skill_name, ..., batches, ...)

# 改后：按 skill 单独 mine
for rank, (skill_name, *_) in enumerate(top_scored, 1):
    digests = skill_sessions.get(skill_name, [])
    since = skill_last_run.get(skill_name)
    digests = filter_digests_by_since(digests, since)
    all_tasks = mine(digests, max_tasks=len(digests))
    batches = [all_tasks[i:i + batch_size] for i in range(0, len(all_tasks), batch_size)]
    print(f'  [{skill_name}] {len(digests)} sessions → {len(all_tasks)} tasks → {len(batches)} batches')
    fut = ex.submit(_optimize_one_skill, skill_name, ..., batches, ...)
```

**`main()` 对应变更**（line 911-919）：

```python
# 改前
top_scored = _phase_rank(eligible, all_digests, state, runner_cfg)
_phase_optimize(top_scored, ..., all_digests, ...)

# 改后
top_scored, skill_sessions = _phase_rank(eligible, all_digests, state, runner_cfg)
_phase_optimize(top_scored, ..., skill_sessions, ...)
```

**`_phase_rank` 对应变更**（line 709-726）：

```python
# 改前
def _phase_rank(...) -> list:
    top_scored, state = rank_skills(...)
    return top_scored

# 改后
def _phase_rank(...) -> Tuple[list, dict]:
    top_scored, state, skill_sessions = rank_skills(...)
    save_state(state)
    if not top_scored:
        return [], {}
    return top_scored, skill_sessions
```

---

### 改动 3：`_optimize_one_skill` 重试池重构（+20 行）

**文件**：`scripts/skillopt-runner/skillopt_runner.py`
**函数**：`_optimize_one_skill()` (line 729-819)

**签名变更**：`all_digests` 参数移除（不再需要，digest check 下沉到调用方）

```python
# 改前
def _optimize_one_skill(skill_name, skill_last_run, state, all_digests, batches, sleep_cfg, *, dry_run):
# 改后
def _optimize_one_skill(skill_name, skill_last_run, state, batches, sleep_cfg, *, dry_run):
```

**代码变更**：

① 加载重试池 + retry_map（替换原来 line 747-763）：

```python
# ── Load failed tasks from retry pool ──
failed_tasks = state.setdefault('failed_tasks', {})
saved = failed_tasks.pop(skill_name, [])
retry_map: dict[str, int] = {}
if saved:
    retry_map = {t['id']: t.get('retry_count', 0)
                 for t in saved if isinstance(t, dict)}
    saved_ids = set(retry_map.keys())
    seen = set(saved_ids)
    deduped = []
    for batch in batches:
        fresh = [t for t in batch if t.id not in seen]
        seen.update(t.id for t in fresh)
        if fresh:
            deduped.append(fresh)
    restored = [TaskRecord.from_dict(t) for t in saved]
    # ── 重试池放最后（fresh tasks 先跑） ──
    deduped.append(restored)
    batches = deduped
    print(f'  [{skill_name}] 恢复 {len(restored)} 重试 task（放最后），'
          f'共 {sum(len(b) for b in batches)} 个 task')
```

② 跳过 digest check（替换原来 line 765-769）：

```python
# ── 无重试池 且 无 batch → skip ──
if not bool(saved) and not batches:
    print(f'  [{skill_name}] 无 session 数据 + 无重试池，跳过')
    return skill_name, False
```

③ SKILL.md 检查 + per_cfg 保持不变（line 771-777）

④ Batch 循环：gate accept → 继续而非 return（修改原来 line 794-815）：

```python
if not result.report.accepted:
    # ── Gate reject → append（含去重+cap+retry_count 递增） ──
    new_tasks = [t.to_dict() for t in batch_tasks if hasattr(t, 'to_dict')]
    existing = failed_tasks.get(skill_name, [])
    existing_ids = {t.get('id') for t in existing if isinstance(t, dict)}
    merged = list(existing)
    for nt in new_tasks:
        nt_id = nt.get('id')
        if nt_id not in existing_ids:
            base = retry_map.get(nt_id, 0)  # fresh task → 0, retry → 过往次数
            nt['retry_count'] = base + 1     # 正确递增
            merged.append(nt)
    # cap
    MAX_RETRY_POOL = 200
    merged = merged[-MAX_RETRY_POOL:]
    # 淘汰超限重试
    merged = [t for t in merged if t.get('retry_count', 0) <= 3]
    failed_tasks[skill_name] = merged
    save_state(state)
    print(f'  [{skill_name}] → Gate 拒绝，重试池当前 {len(merged)} 个 task')
    continue

# ── Gate accept — clear retry pool ──
failed_tasks.pop(skill_name, None)
save_state(state)

if dry_run:
    print(f'  [{skill_name}] → Dry-run: {len(result.report.edits)} edits accepted')
    continue

# Apply edits（不 return，继续跑下一个 batch）
print(f'  [{skill_name}] ✅ Batch {batch_idx} passed!')
for edit in result.report.edits:
    ok = patch_skill_hermes(skill_name, edit.content, state)
    if ok:
        print(f'  [{skill_name}] ✅ Applied: {edit.target}/{edit.op}')
        skill_last_run[skill_name] = datetime.now(timezone.utc).isoformat()
        break  # 一个 batch 多个 edit 只推进一次时间戳
    else:
        print(f'  [{skill_name}] ❌ Apply failed: {edit.target}/{edit.op}')
```

---

### 改动 4：`aggregate_scores` 过滤 timeout（+6 行）

**文件**：`skillopt-sleep/skillopt_sleep/replay.py`
**函数**：`aggregate_scores()` (line 129-134)

```python
# 改前
def aggregate_scores(pairs):
    if not pairs:
        return 0.0, 0.0
    hard = sum(r.hard for _t, r in pairs) / len(pairs)
    soft = sum(r.soft for _t, r in pairs) / len(pairs)
    return hard, soft

# 改后
def aggregate_scores(pairs):
    if not pairs:
        return 0.0, 0.0
    valid = [(t, r) for t, r in pairs if not r.fail_reason]
    if not valid:
        return 0.0, 0.0
    hard = sum(r.hard for _, r in valid) / len(valid)
    soft = sum(r.soft for _, r in valid) / len(valid)
    return hard, soft
```

---

## 4. 测试方案

### 新增测试（test_skillopt_runner.py）

| 测试函数 | 覆盖前提 | 验证点 |
|---------|---------|--------|
| `test_rank_skills_returns_skill_sessions` | mock 3 个 session，其中 session A 提到 skill_a（负反馈），session B 提到 skill_b | `skill_sessions["skill_a"]` 含 session A 不含 B |
| `test_rank_skills_skill_sessions_empty_when_no_neg` | session 提到 skill 但无负反馈 | `skill_sessions["skill"]` 为空列表 |
| `test_retry_pool_skips_digest_check` | mock `_optimize_one_skill`，传入 empty batches + retry pool | 函数不进 early return，进入 batch loop |
| `test_retry_pool_append_not_overwrite` | 2 个 batch 都 reject | `failed_tasks[skill]` 含 2 个 batch 的 task |
| `test_retry_count_increments` | 同一 task 连 fail 3 轮 | retry_count = 1, 2, 3；第 4 轮被淘汰 |
| `test_retry_count_roundtrip` | task 经 to_dict → state.json → from_dict → to_dict | retry_count 不变 |

### 现有测试改动

`test_rank_skills` 系列的 mock 返回值需匹配新签名（加 `skill_sessions` 解包）。

### 无新增依赖

所有 mock 在 `conftest.py` 中已存在（MockSessionDigest, MockTaskRecord）。

---

## 5. Manifest 变更

### skillopt-runner manifest（`deploy/manifests/skillopt-runner.manifest`）

**无变化**。改动 1~3 都在 `skillopt_runner.py` 这个文件内。当前 manifest 已包含：

```
skillopt_runner.py
config.yaml
!__pycache__/**
!state.json
!allowlist.yaml
```

### skillopt-sleep manifest（`deploy/manifests/skillopt-sleep.manifest`）

需确认当前 manifest 内容。改动 4 在 `replay.py`，如果当前 manifest 已覆盖该文件则无变化。

---

## 6. 部署顺序

```
Step 1: ./deploy/deploy.sh plan skillopt-runner    → 确认文件列表
Step 2: ./deploy/deploy.sh deploy skillopt-runner   → 部署 runner
Step 3: python -m pytest tests/test_skillopt_runner.py -x -v  → 验证测试通过
Step 4: ./deploy/deploy.sh plan skillopt-sleep      → 确认文件列表
Step 5: ./deploy/deploy.sh deploy skillopt-sleep    → 部署 sleep
Step 6: python -m pytest tests/ -x -v               → 验证所有测试通过
Step 7: 手动触发 skillopt-runner --dry-run          → 验证数据流
```

---

## 7. 回滚方案

```bash
# 回滚 skillopt-runner 到上一个版本
./deploy/deploy.sh rollback skillopt-runner

# 回滚 skillopt-sleep 到上一个版本
./deploy/deploy.sh rollback skillopt-sleep
```

回滚后手动验证：
1. `python -m pytest tests/ -x -v`
2. 检查 state.json 中的 `failed_tasks` 格式是否为旧格式（无 retry_count 字段）
3. `skillopt_runner.py --dry-run` 确认能正常运行
