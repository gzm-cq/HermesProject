# SkillOpt-Runner 重构方案 v2

## 问题清单

1. **数据不分 skill**：5 个 skill 共享同一批 mined tasks，训练信号无区分度
2. **重试池同轮覆盖**：retry pool 被后来的 fresh batch 覆盖（`failed_tasks[name] = [...]` 永远赋值）
3. **retry pool 被 digest check 阻塞**：无新 session 时 retry pool 加载后被 pop 但跳过了
4. **timeout task 污染 aggregate**：0.0 分拉低 baseline，导致 gate 假阳性
5. **并发写入 state**：5 个 skill 通过 ThreadPoolExecutor 共享 state dict

## 改动清单（5 项，~55 行）

### 改动 1：`rank_skills` 收集 skill→session 映射

**文件**：`skillopt_runner.py`，函数 `rank_skills()`
**目标**：把已存在的 skill→session 匹配结果留存下来，而非只计数后丢弃

```python
# 新增返回参数
def rank_skills(...) -> Tuple[List, dict, Dict[str, List[SessionDigest]]]:
    # ... 原有逻辑不变 ...

    # 新增：收集每个 skill 关联的 session
    skill_sessions: Dict[str, List[SessionDigest]] = {n: [] for n in eligible_names}

    for name in eligible_names:
        for prompt, has_neg in prompt_feedback:
            if message_mentions_skill(prompt, name):
                skill_total[name] += 1
                if has_neg:
                    skill_neg[name] += 1
                    new_neg += 1
                # ── 收集 session（去重） ──
                if (not skill_sessions[name] or
                    skill_sessions[name][-1].session_id != d.session_id):
                    skill_sessions[name].append(d)

    # ... 评分逻辑不变 ...

    # 返回多一个值
    return top, state, skill_sessions
```

### 改动 2：`_phase_optimize` 按 skill 分发自己的数据

**文件**：`skillopt_runner.py`，函数 `_phase_optimize()`
**目标**：每个 skill 只 mine 自己关联 session 的 task

```python
def _phase_optimize(
    top_scored: list,
    skill_last_run: dict,
    state: dict,
    skill_sessions: Dict[str, List[SessionDigest]],  # ← 新增参数
    sleep_cfg: dict,
    runner_cfg: dict,
    *,
    dry_run: bool,
) -> int:
    batch_size = runner_cfg.get('batch_size', 100)

    with ThreadPoolExecutor(max_workers=len(top_scored)) as ex:
        futures = {}
        for rank, (skill_name, *_) in enumerate(top_scored, 1):
            # ── 从自己的 session 中 mine ──
            digests = skill_sessions.get(skill_name, [])
            since = skill_last_run.get(skill_name)
            digests = filter_digests_by_since(digests, since)

            # ── mine ──
            all_tasks = mine(digests, max_tasks=len(digests))
            batches = [all_tasks[i:i + batch_size]
                       for i in range(0, len(all_tasks), batch_size)]

            print(f'  [{skill_name}] {len(digests)} sessions → '
                  f'{len(all_tasks)} tasks → {len(batches)} batches')

            fut = ex.submit(
                _optimize_one_skill,
                skill_name, skill_last_run, state,
                batches, sleep_cfg, dry_run=dry_run,
            )
            futures[fut] = skill_name

        # ... as_completed 部分不变 ...
```

### 改动 3：`_optimize_one_skill` 重试池支持（跳过 digest check + 放最后）

**文件**：`skillopt_runner.py`，函数 `_optimize_one_skill()`
**目标**：重试池不再被 digest check 阻塞，且放最后执行

```python
def _optimize_one_skill(
    skill_name: str,
    skill_last_run: dict,
    state: dict,
    batches: list[list],          # ← 外部已分好 skill 的 batch，可能为空
    sleep_cfg: dict,
    *,
    dry_run: bool,
) -> tuple[str, bool]:

    # ── 加载重试池 ──
    failed_tasks = state.setdefault('failed_tasks', {})
    saved = failed_tasks.pop(skill_name, [])
    has_retry = bool(saved)

    if saved:
        saved_ids = {t.get('id') for t in saved if isinstance(t, dict)}
        seen = set(saved_ids)
        deduped = []
        for batch in batches:
            fresh = [t for t in batch if t.id not in seen]
            seen.update(t.id for t in fresh)
            if fresh:
                deduped.append(fresh)
        restored = [TaskRecord.from_dict(t) for t in saved]
        # ── 重试池放最后 ──
        deduped.append(restored)
        batches = deduped
        print(f'  [{skill_name}] 恢复 {len(restored)} 重试 task '
              f'(放最后)，共 {sum(len(b) for b in batches)} 个 task')

    # ── 无重试池 且 无 batch → skip ──
    if not has_retry and not batches:
        print(f'  [{skill_name}] 无 session 数据 + 无重试池，跳过')
        return skill_name, False

    # ── SKILL.md 检查 ──
    p = get_skill_path(skill_name)
    if not p:
        print(f'  [{skill_name}] 找不到 SKILL.md，跳过')
        return skill_name, False

    per_cfg = dict(sleep_cfg)
    per_cfg['projects'] = [str(p.parent)]

    # ── Batch 循环 ──
    for batch_idx, batch_tasks in enumerate(batches, 1):
        if not batch_tasks:
            continue
        cfg = load_config(**per_cfg)
        result = run_sleep_cycle(cfg=cfg, seed_tasks=batch_tasks, dry_run=dry_run)

        print(f'  [{skill_name}] Batch {batch_idx}/{len(batches)}: '
              f'replayed={result.report.n_replayed} '
              f'baseline={result.report.baseline_score:.3f} '
              f'candidate={result.report.candidate_score:.3f} '
              f'gate={result.report.gate_action}')

        if not result.report.accepted:
            # ── Gate reject → append 到重试池（含去重+cap） ──
            new_tasks = [t.to_dict() for t in batch_tasks if hasattr(t, 'to_dict')]
            existing = failed_tasks.get(skill_name, [])
            existing_ids = {t.get('id') for t in existing if isinstance(t, dict)}
            merged = list(existing)
            for nt in new_tasks:
                if nt.get('id') not in existing_ids:
                    nt['retry_count'] = nt.get('retry_count', 0) + 1
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

        # ── Gate accept → 清空重试池 ──
        failed_tasks.pop(skill_name, None)
        save_state(state)

        if dry_run:
            print(f'  [{skill_name}] → Dry-run: {len(result.report.edits)} edits accepted')
            continue

        print(f'  [{skill_name}] ✅ Batch {batch_idx} passed!')
        for edit in result.report.edits:
            ok = patch_skill_hermes(skill_name, edit.content, state)
            if ok:
                print(f'  [{skill_name}] ✅ Applied: {edit.target}/{edit.op}')
                skill_last_run[skill_name] = datetime.now(timezone.utc).isoformat()
                return skill_name, True
            else:
                print(f'  [{skill_name}] ❌ Apply failed: {edit.target}/{edit.op}')

    return skill_name, False
```

### 改动 4：`main()` 接入新参数

**文件**：`skillopt_runner.py`，函数 `main()`

```python
# Phase 2: rank（多返回 skill_sessions）
top_scored = _phase_rank(eligible, all_digests, state, runner_cfg)
# ↓ 改为
top_scored, skill_sessions = _phase_rank(eligible, all_digests, state, runner_cfg)

# Phase 3: per-skill optimize（传 skill_sessions）
_phase_optimize(
    top_scored, skill_last_run, state,
    skill_sessions,  # ← 新增
    sleep_cfg, runner_cfg, dry_run=args.dry_run,
)
```

### 改动 5：`aggregate_scores` 排除 timeout/error task

**文件**：`skillopt_sleep/replay.py`，函数 `aggregate_scores()`

```python
def aggregate_scores(pairs):
    if not pairs:
        return 0.0, 0.0
    # 排除因超时/API 错误导致无效分数的 task
    valid = [(t, r) for t, r in pairs if not r.fail_reason]
    if not valid:
        return 0.0, 0.0
    hard = sum(r.hard for _, r in valid) / len(valid)
    soft = sum(r.soft for _, r in valid) / len(valid)
    return hard, soft
```

## 改动后数据流

```
Phase 1: harvest → all_digests
Phase 2: rank → 对每个 session 逐条消息匹配 skill 名
               → top 5 skills + skill_sessions（每个 skill 的关联 session 列表）
Phase 3: per-skill
  Skill A:
    digests = skill_sessions["skill_a"]
    digests = filter_by_since(digests, last_run)
    tasks = mine(digests)           ← 只 mine 自己的 session
    retry pool 加载 → 放最后
    batches = [fresh_b1, retry_b]
    loop batches:
      run_sleep_cycle(seed_tasks)
      gate accept → clear retry + update last_run
      gate reject → retry pool append + cap

  Skill B: 同样流程，但基于 skill_sessions["skill_b"]

  （并行安全：skill A 和 B 的 task 数据不同）
```

## 改动影响范围

- 仅修改 `skillopt_runner.py` + `replay.py`（sleep 基础库）
- 不修改 `cycle.py`、`consolidate.py`、`types.py` — 外部 API 不变
- `aggregate_scores` 改动会影响 consolidate 内的 baseline/candidate 计算（gate 行为变严格）

---

## Review 验证结果

### ✅ 5 项改动逐项验证通过

| # | 改动 | 结论 | 说明 |
|---|------|------|------|
| 1 | rank_skills 收集 skill_sessions | ✅ 干净 | 外层循环天然互斥（for name in eligible_names），去重逻辑正确 |
| 2 | _phase_optimize 按 skill 分发 | ✅ 干净 | mine 空列表返回 []，skip 正确 |
| 3 | retry pool 跳过 digest check | ✅ chain 正确 | has_retry=True → 跳过 early return → 直接进 batch loop |
| 4 | main 接线 | ✅ 机械改 | 拆包类型正确 |
| 5 | aggregate_scores 排除 timeout | ✅ 有效 | 但 consolidate 内 failures 分割未过滤（见下） |

### 🐛 发现的漏洞

#### 🐛B（P0 — 必须修）：retry_count 在 from_dict/to_dict 轮转中丢失

```python
# 保存路径：
nt = t.to_dict()           # asdict(TaskRecord) → 有 id/intent/... 无 retry_count
nt['retry_count'] = base + 1  # 手动加上 → state.json 有该字段
merged.append(nt)          # 保存到 failed_tasks[skill]

# 下次加载路径：
restored = [TaskRecord.from_dict(t) for t in saved]
            # ↑ from_dict: cls(**{k: v for k, v in d.items() if k in known})
            # known = TaskRecord 的 dataclass 字段
            # "retry_count" 不在其中 → 被过滤 → restored TaskRecord 丢失了 retry_count

# 再保存路径（再次 reject 时）：
for nt in new_tasks:
    nt_id = nt.get('id')
    if nt_id not in existing_ids:
        nt['retry_count'] = nt.get('retry_count', 0) + 1
        # ↑ nt 来自 t.to_dict()，没有 'retry_count' 键
        # → 永远是 0 + 1 = 1，永远递增不到 3
```

**后果**：`retry_count` 卡在 1，`max_retries` 淘汰机制永远不触发。

**修复**：在加载时把 retry_count 提取到外部 map

```python
# 改动 3 加载段中，after pop
saved = failed_tasks.pop(skill_name, [])
retry_map = {t['id']: t.get('retry_count', 0) for t in saved if isinstance(t, dict)}

# 改动 3 保存段中，for nt in new_tasks 循环
for nt in new_tasks:
    nt_id = nt.get('id')
    if nt_id not in existing_ids:
        base = retry_map.get(nt_id, 0)  # 从加载时的 map 取，fresh task 取到 0
        nt['retry_count'] = base + 1     # 正确：1（fresh）→ 2（重试一次）→ 3
        merged.append(nt)
```

改动量：+3 行。

#### ⚠️A（P2 — 优化项）：gate accept 后跳过剩余 batch

```
batches = [fresh_b1(100), fresh_b2(100), retry_pool(100)]

fresh_b1: accept → 应用 edit → return  ← 剩余 batch 全部丢掉
```

重试池如果放在最后一个 batch，会被 gate accept 后的 early return 截断。当前方案中重试池放最后，意味着**重试池几乎永远不会被执行**——因为前面的 fresh batch 只要有一个通过就 return 了。

**追加修复**：gate accept 后不立即 return，而是清空已通过的 batch 但继续跑剩余 batch（用更新后的 skill 重新跑）

```python
# batch 循环改为：继续而非 return
if result.report.accepted:
    failed_tasks.pop(skill_name, None)
    if dry_run:
        continue  # 继续跑剩余 batch
    for edit in result.report.edits:
        ok = patch_skill_hermes(skill_name, edit.content, state)
        if ok:
            print(f'  [{skill_name}] ✅ Applied: {edit.target}/{edit.op}')
            skill_last_run[skill_name] = now
            # 不 return → 继续下一个 batch（skill 已更新）
            break
    # continue to next batch
    continue
```

改动量：~5 行。

#### ⚠️C（P2 — 可选优化）：只收集有负反馈的 session

当前 `skill_sessions` 收集所有提到该 skill 的 session。正反馈 session 加入后会拉高 baseline，降低 gate 通过率。

**修复**：只收集 `has_neg=True` 的 prompt → 对应的 session。

```python
if message_mentions_skill(prompt, name):
    skill_total[name] += 1
    if has_neg:
        skill_neg[name] += 1
        new_neg += 1
        # ── 只有负反馈才收集 session ──
        if (not skill_sessions[name] or
            skill_sessions[name][-1].session_id != d.session_id):
            skill_sessions[name].append(d)
```

改动量：~2 行（缩进改变）。

### 修正后的改动清单

| # | 改动 | 行数 | 优先级 |
|---|------|------|--------|
| 1 | rank_skills 收集 skill_sessions（只收负反馈 session） | ~12 | P0 |
| 2 | _phase_optimize 按 skill 分发数据 | ~15 | P0 |
| 3 | _optimize_one_skill（跳过 digest check + 继续而非 return + retry_map 修复 🐛B） | ~20 | P0 |
| 4 | main 接线 | ~3 | P0 |
| 5 | aggregate_scores 排除 timeout | ~6 | P0 |
| 6 | 失败/成功分割也过滤 timeout（可选，省 token） | ~2 | P2 |

**总计 ~58 行，全部在 runner + replay.py。**
