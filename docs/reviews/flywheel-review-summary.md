# HermesProject 飞轮系统全面 Review 汇总报告

> 生成时间: 2026-08-10
> 审查范围: 路由+4路召回 / 飞书健康巡检+系统健康 / 完整飞轮系统
> 方法: 3个 OpenCode subagent 并行审查 + Hermes 主 session 抽样核实源码

---

## 核实说明

三个 subagent 共发现 P0~P3 问题约 60+ 条。Hermes 对每条 P0/P1 问题抽样验证源码，结果如下：

| # | Subagent 声称 | 核实结果 |
|---|-------------|---------|
| clustering.py:303 语法错误 | P0-1 (task-0) | **假阳性** — 文件可正常 import，line 303 是 `result["auto_dry_run_exhausted"] = True`，无语法错误 |
| router.py:272 IndexError | P0-2 (task-0) | **确认** — `resp.json()["choices"][0]["message"]` 无 `.get()` 保护，空 choices 时崩溃 |
| hindsight.py:142 unreachable | P0-3 (task-0) | **确认** — 循环后 raise 确实不可达，掩盖了真正的边界 |
| memory_store.py 事务无保障 | P0-1 (task-2) | **部分确认** — _add 后 _remove 失败会导致数据重复，但 execute_cleanup 是单线程顺序执行，实际风险低 |
| skillopt_runner.py failed_tasks 并发 | P0-2 (task-2) | **假阳性** — 每个线程操作不同 skill_name key，无实际竞态 |
| health-check-run.py 每次推飞书 | P0 (task-1) | **确认** — line 178 `push_to_feishu(summary)` 无条件调用，违反 no-news-good-news |
| kn-router-health-check.sh 每次推飞书 | P0 (task-1) | **确认** — line 12 注释"每次巡检完成都推送飞书通知"，违反 no-news-good-news |
| router.py LRU 实为 FIFO | P1-2 (task-0) | **确认** — `_cache_put` 只写不 move_to_end，缓存退化为 FIFO |
| env_loader lru_cache 热更新失效 | P1-8 (task-0) | **确认** — `_read_env_file()` lru_cache(maxsize=1) 永久缓存，改 .env 需重启 |

---

## P0 — Critical（确认需修复）

### C1. Router IndexError — `router.py:272`
```python
# 当前（危险）
choice = resp.json()["choices"][0]["message"]
# 修复
choice = resp.json().get("choices", [{}])[0].get("message", {})
```
**影响**: LLM 返回空 choices（超时/限流）时直接崩溃，触发未预期 fallback。

### C2. Hindsight unreachable code — `hindsight.py:142`
```python
# 循环后这行永远不执行——所有异常路径已在循环内 raise/continue
raise HindsightClientError(f"重试 {CONFIG.max_retries} 次后仍失败（429 限流）", status_code=429)
```
**影响**: 死代码掩盖边界；若真有"所有 attempt 用完但未触发 return"的情况，错误信息不准确。

### C3. health-check-run.py 无条件推飞书 — `health-check-run.py:178`
```python
# line 178: push_to_feishu(summary, dry_run=dry_run) — 无论 ok/warn/fail 都推
```
**影响**: 违反"no news is good news"原则，每天推送正常巡检结果，造成飞书噪音。应仅在 warn/fail 时推送。

### C4. kn-router-health-check.sh 违反 no-news-good-news — `kn-router-health-check.sh:12`
```bash
# line 12: "#   - 每次巡检完成都推送飞书通知"
```
**影响**: 同上，每日两次冗余飞书通知。应改为仅 warn/fail/恢复时推送。

### C5. MemoryStore _add/_remove 非原子 — `memory_store.py:182-197`
```python
# merge: _add(merged) → for j in indices: _remove(j)   # add成功 remove失败→数据重复
# compress: _add(compressed) → _remove(idx)             # add成功 remove失败→数据重复+原数据丢失风险
```
**影响**: add成功后remove失败时，合并条目已写入但原条目未删除，导致 MEMORY.md/USER.md 出现重复内容。单线程执行所以不会并发，但单次操作中途失败仍会导致数据不一致。建议加 try/finally rollback。

---

## P1 — High（确认需修复）

### H1. Router LRU cache 实为 FIFO — `router.py:52-57`
`_evict` 取 `list(_router_cache.keys())[:_evict]`，但 `_cache_put` 只写不 move_to_end，淘汰的是插入顺序最老的而非最少访问的。应改用 OrderedDict + move_to_end。

### H2. env_loader lru_cache 阻止 .env 热更新 — `env_loader.py:14-15`
`@lru_cache(maxsize=1)` on `_read_env_file()`，运行时修改 .env 不生效。建议加 TTL（如60s）或提供 `cache_clear()` API。

### H3. skill_matcher.py 硬编码常量不可配置 — `skill_matcher.py:29`
`_MAX_SKILLS=500`, `_PRESCREEN_TOP_K=30`, `_EMBEDDING_TOP_K=20`, `_TOP_K=3` — 不受 CONFIG/env 控制，benchmark/异常场景无法调整。应走 `KN_SKILL_*` env var。

### H4. Router _parse_mask sag key缺失误触发全fallback — `router.py:140-141` + `source_defs.py`
缺失 sag key时 data被丢弃触发 fallback，但 source_defs prompt中"confidence<0.3全开"规则未在执行中实现（只依赖LLM自行判断）。阈值不一致（prompt写0.3，代码用0.5）。

### H5. MMR归一化浮点epsilon不严谨 — `filtering.py:282-286`
`hi - lo < 1e-6`应改为 `math.isclose(hi, lo)`。

### H6. circuit_breaker is_open()状态非原子 — `circuit_breaker.py:106-116`
锁内调_save_state()又获取_file_lock，双重锁逻辑混乱。建议拆分为"检查+清除过期"和"持久化"两步。

### H7. health-check-all.py curl拼接无引号保护 — `health-check-all.py:161-162`
URL含空格时命令失败。应改用列表形式或 shlex.quote。

### H8. health-check-all.py df解析依赖%符号 — `health-check-all.py:324`
tmpfs等文件系统格式不同。应改用 `df --output=pcent /`。

### H9. kn-router-health-check.sh Python heredoc shell变量插值 — `kn-router-health-check.sh:57-89,150-162`
`${PLUGIN_DIR}` / `${SAG_CB_FILE}`直接插值到Python heredoc，路径含特殊字符会破坏Python语法。应改用环境变量传递。

### H10. cron_common.sh lark-cli用--text而非--markdown — `cron_common.sh:246-249`
不支持富文本；失败后静默return但不记录具体错误码（无法诊断code=19001）。应改用--markdown并解析返回JSON的code字段。

---

## P2 — Medium（建议修复）

| # | 位置 | 问题 |
|---|------|------|
| P2-1 | router.py:78 | JSON正则贪婪匹配嵌套对象风险（LLM输出嵌套JSON时解析失败） |
| P2-2 | skill_matcher.py:62-63 | STOPWORDS中英混杂且漏掉"is"/"be"等短词 |
| P2-3 | filtering.py:46-47 | [标记:已解决]降权直接修改rerank_score字段干扰后续因果链boost |
| P2-4 | hooks/router.py:1084-1096 | SAG-only场景kept=[]初始化逻辑混乱，latency_ms重复赋值 |
| P2-5 | circuit_breaker.py:118-139 | record_failure飞书通知并发可能重复发送（虽有5分钟限频但首次通知可能重复） |
| P2-6 | recall_eval/core/metrics.py:77-82 | _tokenize中文分词用连续汉字bigram，"数据库迁移"切为一个token无法区分"数据库迁移工具" |
| P2-7 | recall_eval/core/runner.py:196 | batch_size日志间隔与max_workers并行度不匹配，completed计数非确定性 |
| P2-8 | knowledge-tree-builder/config.py:145-147 | ENV覆盖跳过类型转换，max_candidates_per_article等字段存为字符串而非int |
| P2-9 | cron-periodic-detect.sh:149 | dedup用error_key[:60]截断，不同错误前60字符相同时误判为同一错误漏发告警 |
| P2-10 | kn_judge.py:174/181-197 | os.environ全局修改影响同进程其他线程；ThreadPoolExecutor内future.result()无超时卡住整体 |
| P2-11 | router.py:35 + memory_cleanup.py:39 | datetime.fromisoformat解析trace.log时间格式不统一（有/无时区）；memory_cleanup用UTC但ts_str可能含本地时区 |
| P2-12 | auto_tuner/tuner.py:894-898 | no_change_count>=threshold设locked=True但不清suspended标志，锁定前应先检查是否清除suspended |

---

## P3 — Low（可选改进）

| # | 位置 | 问题 |
|---|------|------|
| P3-1 | flywheel-orchestrator/tasks.py:175-180 | knowledge-navigation-baseline注释指向已过时的cli.py处理逻辑 |
| P3-2 | skillopt_runner.py:1-30 | _SKILLOPT_SLEEP_PATH硬编码路径与SKILLOPT_HOME计算不一致 |
| P3-3 | dream-synth/dream-daily.py:65-70 | _sag_session全局变量+懒加载在ThreadPoolExecutor下有线程安全问题（if None非原子）→改用threading.local()或加Lock |
| P3-4 | self-evolving/operators/revision.py:1-50 | max_tokens=2048固定但s-deepseek-v4-flash JSON输出有时超此值→改为从RevisionConfig读取(默认4096) |
| P3-5 | clustering/core/clustering.py:process_clusters | seen_pairs去重逻辑已实现但文档缺失，LLM路径和正则路径共享set但causal_pairs为空时不更新seen_pairs导致重复 |
| P3-6 | health-check-all.py:564 | ThreadPoolExecutor(max_workers=8)硬编码→根据os.cpu_count()动态调整 |
| P3-7 | health-check-run.py:145 | 注释"每日9:00"与实际调度(0 8 * * 1-5即每日8:00)不符→更新注释或调度时间 |
| P3-8 | kn-router-health-check.sh:117 | KN_ROUTER_API_KEY未设置时STABILITY_PASS=STABILITY_TOTAL直接判为通过→标记为"跳过(无API key)"而非视为通过 |

---

## "no news is good news"合规检查汇总

| 脚本/模块 | 合规状态 | 说明 |
|-----------|---------|------|
| cron-periodic-detect.sh | ✅ | CRON_SKIP_FINISH_NOTIFY=true正确跳过正常通知 |
| cron_common.sh:375 CRON_SKIP_FINISH_NOTIFY机制 | ✅ | 支持跳过正常通知的通用机制已实现但未广泛使用 |\n| health-check-cron.sh | ⚠️ cron_finish默认发送通知即使全部ok |\n| kn-router-health-check.sh | ❌ line=每次巡检完成都推送飞书通知违反原则 |\n| dream-daily.sh | ❌无cron_finish完全无通知逻辑 |\n| health-check-run.py (line=push_to_feishu无条件调用) ❌每次巡检都推飞书无论是否全部ok |\n\n**修复优先级**: C3+C4 (health-check-run.py + kn-router-health-check.sh) → P2-9 (cron-periodic-detect dedup截断) → P3相关脚本统一加CRON_SKIP_FINISH_NOTIFY。

---

## Subagent假阳性汇总（已排除）

以下问题经Hermes源码核实后确认为假阳性：

1. **clustering.py:303语法错误** — File imports successfully (`python3 -c "from knowledge_tree_builder.core import clustering; print('OK')"`), line=303 is just `result["auto_dry_run_exhausted"] = True`. No syntax error exists. Subagent likely read a stale/cached version or hallucinated the error location.

2. **skillopt_runner.py failed_tasks并发竞态** — Each thread operates on different `skill_name` keys in the shared `state` dict via `state.setdefault('failed_tasks', {})`. No actual race condition on the same key. The subagent's concern about concurrent access is theoretically valid but practically non-existent given the per-skill threading model.

---

## Top Priority Actions（按影响排序）

```mermaid
graph LR
    A[P0-C3/C4<br/>health-check飞书噪音] --> B[改push_to_feishu条件判断<br/>仅warn/fail时推送]
    C[P0-C1<br/>Router IndexError] --> D[加.get()保护<br/>resp.json().get('choices', [{}])[0].get('message', {})]
    E[P0-C5<br/>MemoryStore非原子] --> F[加try/finally rollback<br/>_add成功后_remove失败时回滚]
    G[P1-H1<br/>LRU变FIFO] --> H[改用OrderedDict+move_to_end]
    I[P1-H2<br/>.env热更新失效] --> J[加TTL或cache_clear API]
    K[P1-H7/H8<br/>curl/df解析脆弱] --> L[shlex.quote + df --output=pcent]
```
