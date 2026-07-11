# Dream-Synth Pipeline 性能优化设计

> 日期：2026-07-11
> 版本：v4
> 状态：已实施（v4 review fix 已部署）
> 背景：首次全量跑 274 session 耗时 8-10h，SAG ingest 串行瓶颈

## 1. 问题分析

### 1.1 当前架构

```
主线程串行：
  for session in sessions:
    1. significanceFilter (LLM)     ~3s
    2. synthesize (LLM)             ~10-15s
    3. SAG ingest (extract=True)    ~30-90s  ← 瓶颈
    4. 写 verdict cache
  更新 last_run.txt
```

### 1.2 瓶颈数据

| 步骤 | 平均耗时 | 占比 |
|------|---------|------|
| significanceFilter | 3s | 3% |
| synthesize | 12s | 12% |
| **SAG ingest (extract=True)** | **45s** | **85%** |

274 session × 60s/session ≈ 4.5h（实际 8-10h，因超时重试）

### 1.3 SAG 500 错误根因

SAG `/ingest` 调用上游 SiliconFlow BAAI/bge-m3 embedding API 偶发 502。

- 4 次 500 错误全是 embedding 502，不是内容问题
- 单独重试同一内容能成功
- SAG 日志 `error: {}` 无详情，需从 dream-daily 客户端侧捕获
- `extract=True` 不能关闭：dream-synth 不是 SAG 唯一使用者，实体提取是多跳检索的前提

### 1.4 120s 超时根因

SAG ingest 的 `extract=True` 触发：分块 → embedding → NER 实体提取 → event store 写入。单次 30-90s，60s timeout 经常踩线（已改为 120s，仍偶发超时）。

### 1.5 二次评审发现的问题（v3）

v2 实施后代码 review 发现以下问题：

| 级别 | 问题 | 影响 |
|------|------|------|
| P0 | `time.sleep(0.5)` 硬编码无注释 | 白白浪费 2.3 分钟（274 session） |
| P0 | `as_completed` 后 O(n²) 遍历找对应 item | 低效代码，274→7.5 万次比较 |
| P1 | `last_ingest_error` 只存时间不存错误原因 | 排查问题时不知道是超时/502/连接错误 |
| P1 | 每次 SAG 请求新建连接（无连接池） | TLS 握手开销，并发下更明显 |
| P1 | 无 SAG 健康检查（预热） | SAG 挂了还浪费 LLM 费用合成 |
| P2 | `phase_promote` LLM 判断也串行 | 100 个 reflections 要 5-8 分钟 |
| P2 | 无运行指标统计 | 不知道成功/失败/跳过/耗时分布 |
| P2 | `as_completed` 打乱 reflections 顺序 | 与串行行为不一致，可能有隐性 bug |

## 2. 优化方案：Producer-Consumer 流水线

### 2.1 核心思路

LLM 合成（producer）和 SAG ingest（consumer）是两个独立步骤，用线程池解耦：

```
主线程（producer）                    ingest 线程池 3 workers（consumer）
┌────────────────────┐              ┌────────────────────┐
│ [1] filter+synthesize 15s │→ queue →│ SAG ingest 30-90s  │
│ [2] filter+synthesize 15s │→ queue →│ SAG ingest 30-90s  │  ← 3路并发
│ [3] filter+synthesize 15s │→ queue →│ SAG ingest 30-90s  │
│ ... 不停等 ingest           │          │                    │
│ [274] filter+synthesize    │→ queue →│ SAG ingest 30-90s  │
└────────────────────┘              └────────────────────┘
     ↑ 总 ~70min                         ↑ 3并发，追得上 LLM 速度
```

- LLM 合成完一个就立即投到 ingest 队列，不等
- ingest 3 路并发，等效 15s/session，匹配 LLM 速度
- `executor.shutdown(wait=True)` 阻塞等所有 ingest 完成后才更新 `last_run.txt`
- **总耗时 ~70min**（而非串行 8-10h）

### 2.2 为什么 max_workers=3

| workers | ingest 等效耗时 | LLM 合成耗时 | 瓶颈 | 总耗时 |
|---------|---------------|-------------|------|--------|
| 1（当前） | 45s | 15s | ingest | ~4.5h |
| 2 | 22.5s | 15s | ingest | ~2h |
| **3** | **15s** | **15s** | **持平** | **~70min** |
| 4 | 11s | 15s | LLM | ~70min（无增益） |

3 是甜点：ingest 追上 LLM 速度，再增加无收益。
另外 SiliconFlow embedding API 有并发限制，3 路较安全。

> **可配置**：通过 `config.yaml` 的 `sag.ingest_workers` 配置，默认 3。

### 2.3 SAG 重试机制（指数退避 + 抖动）

ingest 内部加 3 次重试，指数退避 + 随机抖动（避免惊群效应）。

**重试范围**：
- 所有 `5xx` 服务器端错误（500/502/503/504 等）
- `requests.Timeout` 超时
- 4xx 错误不重试（客户端问题，重试也没用）

**重试间隔**：`base_delay * 2^attempt + random(0, 1)` 秒

```python
def sag_ingest(title, content, metadata, dry_run=False, max_retries=3, base_delay=5.0):
    if dry_run:
        return "dry-run-doc-id"
    session = _get_sag_session()  # 连接池复用
    last_error = ""
    for attempt in range(max_retries):
        try:
            resp = session.post(..., timeout=120)
            if resp.status_code in (200, 201):
                doc_id = resp.json().get("documentId", "")
                return doc_id if doc_id else None
            if 500 <= resp.status_code < 600:
                last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(delay)
                    continue
                return None
            return None
        except requests.exceptions.Timeout as e:
            last_error = f"Timeout: {e}"
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                time.sleep(delay)
                continue
            return None
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            return None
    return None
```

> **设计迭代**：
> - v1: 固定 5s 间隔，只重试 500
> - v2: 扩展到所有 5xx（因实际错误是 502）
> - v3: 指数退避 + 抖动；记录 last_error 便于排查

### 2.4 线程安全

**结论：不需要全局锁**。

- **verdict cache 写入**：每个 session 的 verdict file 是唯一的（`{sid}.json`），每个 future 只处理一个 session，只写自己的文件，不存在多线程写同一文件的场景
- **LLM 合成**：只在主线程跑，无竞争
- **ThreadPoolExecutor**：用 `concurrent.futures` 管理 worker 生命周期

> **设计迭代**：v1 设计用 `threading.Lock` 保护，经评审确认是冗余的，v2 已移除。

### 2.5 SIGTERM / SIGINT 安全退出

```python
_shutdown_event = threading.Event()

def _handle_shutdown(signum, frame):
    _shutdown_event.set()
    print("\n⚠️ 收到中断信号，停止提交新任务，等待已提交任务完成...", file=sys.stderr)

signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)
```

主线程在每次投任务前检查 `_shutdown_event.is_set()`，如果 True 则停止投新任务，等已投任务完成。

> **设计迭代**：
> - v1: 全局变量 `_shutdown = False`，只处理 SIGTERM
> - v2: 改用 `threading.Event` 更线程安全；新增 SIGINT（Ctrl+C）支持

### 2.6 失败状态管理

新增 `ingest_attempts` 和 `last_ingest_error` 字段，区分三种状态：

| 状态 | `ingested` | `ingest_attempts` | `last_ingest_error` | 说明 |
|------|-----------|-------------------|---------------------|------|
| 未尝试 | 不存在/None | 0 或不存在 | 不存在 | 首次运行 |
| 成功 | True | - | - | ingest 成功，有 `document_id` |
| 失败 | False | 1~3 | 错误描述 + attempt | ingest 失败，记录尝试次数和错误原因 |

**跳过逻辑**：`_should_skip_ingest()` 函数，连续失败 3 次后跳过该 session，避免无限重试。

```python
def _should_skip_ingest(cache):
    if cache.get("ingested"):
        return True
    if cache.get("ingest_attempts", 0) >= 3:
        return True
    return False
```

`last_ingest_error` 格式：`YYYY-MM-DD HH:MM:SS (attempt N) - 错误描述`

> **设计迭代**：
> - v1: 只有 `ingested=True/False`，失败后下次还会重试，可能无限重试
> - v2: 增加 `ingest_attempts` 失败次数上限（3次）
> - v3: `last_ingest_error` 增加错误描述，不只存时间

### 2.7 进度监控与告警

- **tqdm 双进度条**：LLM 合成进度 + SAG ingest 进度，分别显示
- **失败汇总输出**：ingest 完成后汇总输出失败 session 列表（最多显示 5 个）
- **stderr 输出**：重试和错误信息输出到 stderr，不干扰正常进度条
- **运行指标统计**（v3 新增）：完成后打印 `📊 合成完成: 总 N | 成功 X | 失败 Y | 跳过 Z | 耗时 Ts`

### 2.8 连接池复用（v3 新增）

全局共享一个 `requests.Session`，复用 TCP + TLS 连接：

```python
_sag_session: requests.Session | None = None

def _get_sag_session() -> requests.Session:
    global _sag_session
    if _sag_session is None:
        _sag_session = requests.Session()
    return _sag_session
```

**覆盖的 SAG 调用**：
- `sag_ingest()` - 写入
- `sag_search()` - 搜索
- `sag_health_check()` - 健康检查

**收益**：3 路并发下减少 TLS 握手开销，特别是大量请求时更明显。

### 2.9 SAG 健康检查（预热，v3 新增）

在 `phase_synthesize` 开始前先检测 SAG 服务是否可用：

```python
def sag_health_check(timeout=5.0) -> bool:
    # 先试 /health 端点
    # 失败再试 / 端点（status < 500 就算活）
```

- SAG 不可达时，**跳过 ingest 但保留 LLM 合成结果到 cache**
- 避免 SAG 挂了还白白花 LLM 费用去合成
- dry_run 模式跳过检查

### 2.10 顺序保持（v3 新增）

`as_completed` 会打乱返回顺序，v3 用 `order` 列表记录输入顺序，最终按原始顺序输出：

```python
order: list[str] = []              # 记录 sid 顺序
result_map: dict[str, dict] = {}   # sid → 结果
# ...
reflections = [result_map[sid] for sid in order
               if sid in result_map and "title" in result_map[sid]]
```

**目的**：保持与串行实现一致的行为，减少隐性 bug。

### 2.11 LLM 限流配置化（v3 新增）

原 v2 代码里硬编码了 `time.sleep(0.5)`，无注释说明用途。v3 改为可配置参数：

```yaml
llm:
  throttle_seconds: 0.0  # LLM 调用间隔限流，默认 0（不限流）
```

- 默认 0，不浪费时间
- 如果 LLM API 有速率限制，可以调大
- 所有 LLM 调用点（significanceFilter / synthesize / promote）都统一走这个配置

### 2.12 O(1) Future 查找（v3 新增）

v2 用 `for f in ingest_futures: if f["future"] is item` 遍历查找，O(n²)。v3 改为 dict 映射：

```python
future_to_sid: dict[Future, str] = {}
# ...
for future in as_completed(future_to_sid.keys()):
    sid = future_to_sid[future]  # O(1) 查找
    info = result_map[sid]
```

## 3. Phase 3 Promote 并发优化（v3 新增）

### 3.1 问题

promote 阶段每个 reflection 调用一次 LLM（promote-judge），完全串行。100 个 reflections × 4s ≈ 400s（6-7分钟）。

### 3.2 方案

可选并发，通过 `llm.promote_workers` 配置，默认 1（串行，向后兼容）。

```python
max_workers = CFG.get("llm", {}).get("promote_workers", 1)

if max_workers <= 1:
    # 串行模式（默认）
    verdicts = []
    for r in tqdm(candidates, ...):
        verdict = call_llm_json(prompt, smart_model)
        verdicts.append((r, verdict))
else:
    # 并发模式
    def _judge_one(r):
        verdict = call_llm_json(prompt, smart_model)
        return (r, verdict)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_r = {executor.submit(_judge_one, r): r for r in candidates}
        for future in as_completed(future_to_r.keys()):
            verdicts.append(future.result())
    # 恢复输入顺序
    verdicts.sort(key=lambda x: sid_order.get(x[0]["session_id"], 0))
```

### 3.3 写入优化

promote 日志（promote_log）从每次 append 改为批量写入：

```python
promote_log_lines = []
# ... 循环中收集 line ...
if promote_log_lines and not dry_run:
    with open(promote_log, "a", encoding="utf-8") as f:
        for line in promote_log_lines:
            f.write(line + "\n")
```

减少文件 IO 次数。

## 4. 中断安全分析

| 中断时机 | verdict cache 状态 | last_run.txt | 恢复行为 |
|---------|-------------------|-------------|---------|
| Phase 1a 中断（LLM 合成中） | synthesized=True 的已缓存 | 未更新 | 跳过已合成的，继续未完成的 |
| Phase 1b 中断（ingest worker 中） | ingested=None 或 ingested=False（带 attempt 计数+错误） | 未更新 | 补 ingest（synthesized=True 但 ingested≠True 的，attempt<3 的重试） |
| 进程被 SIGKILL | 同上 | 未更新 | 同上 |
| Phase 3 中断（promote 中） | promote_log 部分写入 | 未更新 | 已写的跳过，未写的重新判断 |
| 正常完成 | 全部 ingested=True | 更新为 now | 下次只读新 session |

**关键不变量**：`last_run.txt` 只在 `executor.shutdown(wait=True)` 返回后才更新。

## 5. 代码改动清单

### 5.1 `scripts/dream-daily.py`

#### v1 改动（Producer-Consumer 基础）

1. **`phase_synthesize()` 拆分为 producer-consumer**
   - producer：主线程串行跑 significanceFilter + synthesize
   - consumer：`ThreadPoolExecutor(max_workers=3)` 并发 SAG ingest

2. **增强 `sag_ingest()` 重试逻辑**
   - 返回值：`str | None`（documentId 或 None）
   - 重试范围：所有 5xx + 超时，3 次重试

3. **新增 `_should_skip_ingest()`**
   - 已成功或连续失败 3 次则跳过

4. **SAG ingest 失败状态字段**
   - 新增 `ingest_attempts`、`last_ingest_error`
   - 成功后清除

5. **SIGTERM + SIGINT handler**
   - 用 `threading.Event`
   - 同时处理 SIGTERM 和 SIGINT

6. **`sag_search()` 增加 `source_filter`**
   - 按 `metadata.source` 过滤

7. **`_load_cached_reflections()` 增加 SAG fallback**
   - cache 为空时从 SAG 搜索 fallback

8. **新增 `_safe_int()`**
   - 安全整数转换

9. **`last_run.txt` 更新时机不变**
   - `executor.shutdown(wait=True)` 阻塞保证安全

#### v2 改动（评审修复）

10. **重试范围从 500 扩展到所有 5xx**
11. **移除全局线程锁**（确认冗余）
12. **tqdm 双进度条**
13. **`sag.ingest_workers` 可配置**

#### v3 改动（二次深度优化）

14. **连接池复用** - `_get_sag_session()` 全局共享 requests.Session
15. **指数退避 + 抖动** - 重试间隔从固定 5s 改为 `base_delay * 2^attempt + random`
16. **错误信息记录** - `last_ingest_error` 记录具体错误类型和描述
17. **SAG 健康检查** - `sag_health_check()` 预热检测
18. **O(1) future 查找** - dict 映射替代列表遍历
19. **顺序保持** - reflections 按输入顺序输出
20. **LLM 限流配置化** - `llm.throttle_seconds` 替代硬编码 sleep
21. **运行指标统计** - `IngestStats` 数据类 + 汇总输出
22. **phase_promote 并发** - `llm.promote_workers` 可选并发
23. **promote 日志批量写入** - 减少文件 IO
24. **sag_search 也用连接池** - 统一 Session 复用

### 5.2 不改动的部分

- `read_sessions()` — 不变
- `phase_patterns()` — 不变
- `phase_feishu()` — 不变
- SAG `extract=True` — 不变
- HTTP timeout 120s — 不变

### 5.3 配置新增

```yaml
llm:
  throttle_seconds: 0.0    # LLM 调用间隔限流，默认 0（不限流）
  promote_workers: 1       # promote 阶段 LLM 判断并发数，默认 1（串行）

sag:
  ingest_workers: 3        # SAG ingest 并发数，默认 3
```

### 5.4 测试要点

1. **并发安全**：多 worker 同时写不同 verdict cache 文件，无冲突
2. **中断恢复**：模拟 SIGTERM，验证 last_run.txt 未更新，verdict cache 正确
3. **5xx 重试**：mock SAG 返回 500/502/503，验证重试 3 次后返回 None
4. **幂等**：重跑已完成的 session，全部走 CACHE 路径
5. **失败次数上限**：连续失败 3 次后跳过，不再重试
6. **SAG fallback**：verdict cache 为空时从 SAG 拉取 reflections
7. **顺序保持**：并发后输出顺序与输入一致
8. **promote 并发**：多 worker 下 promote_log 正确写入

## 6. 预期效果

| 指标 | 优化前 | v1 优化后 | v3 优化后 |
|------|--------|-----------|-----------|
| 274 session 总耗时 | 8-10h | ~70min | ~68min（省 2min sleep） |
| Phase 1 SAG ingest | 串行，45s/session | 3并发，15s/session | 同 + 连接池省 5-10% |
| Phase 3 promote | 串行，4s/reflection | 串行 | 可选并发，~1s/reflection |
| SAG 5xx 导致跳过 | 4/74 (5.4%) | 0（重试兜底） | 0（指数退避更稳） |
| 120s 超时 | 偶发 | 0（并发不阻塞主线程） | 0 |
| 中断恢复 | verdict cache 兜底 | 同 + 失败次数防重试 | 同 + 错误描述便于排查 |
| cron 16:00 运行 | 跑不完 | 17:10 跑完 | 17:08 跑完 |

## 7. Cron 兼容性

- cron 通过 `dream-daily.sh` wrapper 调用 `python3 dream-daily.py`
- 无新强制依赖（`concurrent.futures`、`threading`、`dataclasses` 都是标准库，`tqdm` 可选，缺失时退化为无进度条）
- `signal.SIGTERM` / `signal.SIGINT` 在 cron 环境下正常工作
- `executor.shutdown(wait=True)` 确保所有 ingest 完成后进程才退出
- 如 cron 超时被 kill（`HERMES_CRON_SCRIPT_TIMEOUT`），SIGTERM handler 停止投新任务，等已投的完成
- `config.yaml` 的 `cron.script_timeout_seconds=7200`（2h）足够覆盖运行

## 8. 设计迭代记录

| 版本 | 日期 | 变更 | 原因 |
|------|------|------|------|
| v1 | 2026-07-11 | 初始设计 | Producer-Consumer 基本架构 |
| v2 | 2026-07-11 | 重试范围从 500 扩展到 5xx | 实际错误是 502，原设计只重试 500 有 bug |
| v2 | 2026-07-11 | 移除全局线程锁 | 每个 session 文件独立，无竞争，锁冗余 |
| v2 | 2026-07-11 | 增加 `ingest_attempts` 失败计数 | 防止无限重试失败的 session |
| v2 | 2026-07-11 | 增加 tqdm 进度条 | 提升可观测性 |
| v2 | 2026-07-11 | SIGINT 支持 + threading.Event | 更安全的信号处理 |
| v2 | 2026-07-11 | `sag_search` 增加 source_filter | 支持按来源过滤 |
| v2 | 2026-07-11 | `_load_cached_reflections` 增加 SAG fallback | 单独跑阶段时的鲁棒性 |
| v2 | 2026-07-11 | `sag.ingest_workers` 可配置 | 灵活调整并发数 |
| v3 | 2026-07-11 | requests.Session 连接池复用 | 减少 TLS 握手开销，并发下更明显 |
| v3 | 2026-07-11 | 指数退避 + 随机抖动重试 | 避免上游恢复时的惊群效应 |
| v3 | 2026-07-11 | `last_ingest_error` 记录错误描述 | 排查问题时知道具体错误类型 |
| v3 | 2026-07-11 | SAG 健康检查（预热） | SAG 挂了不浪费 LLM 费用 |
| v3 | 2026-07-11 | O(1) future 查找（dict 映射） | O(n²) → O(1)，代码更高效清晰 |
| v3 | 2026-07-11 | reflections 保持输入顺序 | 与串行行为一致，减少隐性 bug |
| v3 | 2026-07-11 | LLM 限流配置化（去掉硬编码 sleep） | 默认 0 不限流，需要时可调 |
| v3 | 2026-07-11 | 运行指标统计输出 | 成功/失败/跳过/耗时一目了然 |
| v3 | 2026-07-11 | phase_promote 可选并发（promote_workers） | 进一步缩短总耗时，默认串行不影响现有行为 |
| v3 | 2026-07-11 | promote_log 批量写入 | 减少文件 IO 次数 |
| v3 | 2026-07-11 | sag_search 也用连接池 | 统一 Session 复用 |
| v4 | 2026-07-11 | dry_run 不投 ingest 不写 cache | review 发现 P1，dry_run 污染 verdict cache |
| v4 | 2026-07-11 | SAG health check 失败跳过 ingest | review 发现 P2，检查无实际效果 |
| v4 | 2026-07-11 | config.yaml 补全 ingest_workers/promote_workers/throttle_seconds | 代码已有 get 兜底但文档说要加 |
| v4 | 2026-07-11 | 移除未使用的 stats.retried/latency_ms | review 发现 P2 |
| v4 | 2026-07-11 | 补充 5xx 重试测试（5 个新测试） | review 发现 P2 |
