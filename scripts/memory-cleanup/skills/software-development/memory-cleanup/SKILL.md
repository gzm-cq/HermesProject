---
name: memory-cleanup
description: MEMORY.md & USER.md 清理维护管线 — LLM JSON mode 分批并行分类→四数组(merge/remove/compress/flagged)→纯 LLM 验证(session辅助)→用户确认执行。纯平铺记事本结构，不添加分类/索引/元数据。MEMORY/USER 独立 prompt，多轮投票(--vote)降低方差。
version: 6.1.1
related_skills: [hermes-agent, knowledge-navigation, memory-maintenance, memory-file-management]
---

# MEMORY.md & USER.md 清理管线

> 核心原则：MEMORY.md 是纯平铺记事本（§ 分隔、无元数据、无时间戳），**不接受添加结构（分区/索引/分类头）**。USER.md 同理。

## 一、数据源

| 数据源 | 位置 | 用途 |
|--------|------|------|
| MEMORY.md | `~/.hermes/memories/MEMORY.md` | Agent 高频固定事实（环境/配置/偏好/教训） |
| USER.md | `~/.hermes/memories/USER.md` | 用户档案（偏好/风格/习惯） |
| Hindsight PG | `$CLUSTERING_DB_URL`（env var） | 查已存档的对话碎片（12K+ 条） |
| Hindsight recall API | `POST /v1/default/banks/hermes/memories/recall` | 语义搜索验证能否召回 |
| Session DB | `~/.hermes/state.db` (SQLite FTS5) | 对话历史毫秒级搜索 |

## 二、Phase 0：程序化清理

可直接执行的规则，无需 LLM：
- **规则 6 — 精确去重**：标准化后（去空格、转小写、去标点）内容完全相同的条目，只保留最新一条
- **规则 10 — 噪音清理**：长度过短（≤3字符）、纯符号（仅§）、应答词（好的/收到/嗯/哈哈）
- **清理自身记录**：当前清理流程产生的临时条目（清理原则、三阶段流程、V5方案）→ 直接 remove
- **空条目**：仅 `§` 的 → 直接 remove
- **merge/compress 已替代**：被合并或压缩版替代的原条目 → 直接 remove（替代版执行 add/replace）
- 以上三类不需要 LLM 二次判断

## 三、Phase 1：LLM 分批并行分类 → 三数组

### 关键设计

- **不要一次性送全部条目**（141 条 × 500 字符 = 70K+ → 超时）。用 `ThreadPoolExecutor(max_workers=8)` + `batch_size = 10`（MEMORY）/ `user_batch_size = 10`（USER）
- **JSON mode**：`response_format: {"type": "json_object"}`，大幅减少解析失败率（~15% → ~2%）
- **四路径 JSON 解析**：正常 json.loads → strip/clean → 栈匹配嵌套 JSON → 正则提取回退（`_regex_fallback_parse`）
- **MEMORY.md 和 USER.md 用不同 prompt**：USER.md 的 compress 必须 "句式精简不做含义丢弃"，不能设 60 字符硬限
- **8 个数组输出**：`mem_merge / mem_remove / mem_compress / mem_flagged` + `user_merge / user_remove / user_compress / user_flagged`
- **批失败单条重试**：整批失败后逐条调用 LLM，一条失败不污染其他条目；仍失败的加入 `flagged` 列表
- **多轮投票**：`--vote N` 跑 N 轮；remove 取并集后交给 Phase 2 验证，compress/merge/hindsight 取交集，降低 LLM 方差

### LLM 输出格式

```json
{
  "merge": [
    {"indices": [16, 21], "合并为": "合并后的精简文本——必须在索引中给出具体内容"}
  ],
  "remove": [
    {"index": 6, "原因": "过程记录，非当前有效配置"}
  ],
  "compress": [
    {"index": 18, "精简为": "压缩后的精简文本——保留核心要点"}
  ]
}
```

- **merge**：同主题碎片合并，LLM 输出合并文本 → `memory(add)` 合并版 + `memory(remove)` 原条目
- **remove**：不应该留在 MEMORY.md/USER.md 的条目
- **compress**：有用但太长，LLM 输出压缩版 → `memory(replace)` 用压缩版替代
- 未出现在任何数组的条目默认保留

### MEMORY.md 分类标准

**绝对不能标 remove**（只能 compress 或不管）：
- 工具特性/限制/坑（Dify DSL导入格式、tsvector限制、sensenova-u1-fast限制、Wiki MCP限制）
- 经验教训/根因（UTC时间陷阱、API key验证方法、config.yaml截断教训、session_search用法）
- 架构约定/决策（Profile=岗位定义、Gateway管理方式、质量闭环三要素、Provider配置规范）
- 环境配置（LiteLLM网关地址、PG端口、base_url）
- 用户偏好/工作习惯（不要并发、先排事情再排时间、不要轮询）

**应该标 remove**：
- 业务数据（金额、MES时间线、NC-ERP数据、部门数、集团数据）→ 放 Hindsight
- 个人陈述/文档素材（文档状态、审计评分、文件路径、案例叙述）
- 论文信息（投稿目标、期刊名、引用编号、投稿流程）
- 纯过程记录（"2026-05-XX 做了什么操作"，非当前有效配置/教训）
- 已被后续条目替代的过时信息
- 项目计划（时间锚点、里程碑等定期变动的数据）

### USER.md 分类标准（和 MEMORY.md 完全不同）

**绝对不能标 remove**：
- 用户个人背景（公司、岗位、联系方式）
- 工作习惯/偏好（沟通方式、审计偏好、项目计划方式、配图方式）
- 技术能力
- 用户明确告知的规则（xxx1/xxx2 占位符、使用三数据源）
- 项目状态

**compress 要求**：保持完整含义的前提下精简句式，**不能丢弃任何核心信息**。不能设 60 字符硬限，不能缩成关键词片段。

### Prompt 经验

- 不能写"至少 40% 应标记 remove"——会误伤工具特性/经验教训
- 必须举例说明哪些条目绝对不能标 remove
- 结尾写"宁少标勿错标"而不是"宁缺毋滥"——"宁缺毋滥"会让 LLM 全部输出空
- LLM 输出需要去重（按 index 保留首个），同一 index 可能被不同 batch 重复输出

### 并行代码模式

```python
with ThreadPoolExecutor(max_workers=8) as executor:
    futures = [executor.submit(classify_batch, entries[s:s+10], s, source_type) 
               for s in range(0, len(entries), 10)]
```

批失败后自动单条重试：

```python
if "error" in result:
    for i, entry in enumerate(batch):
        single_result = llm_client.classify_batch([entry], offset + i, source_type, single_prompt)
        if "error" not in single_result:
            all_merge.extend(single_result.get("merge", []))
            ...
        else:
            all_flagged.append({"range": [idx, idx], "count": 1, "reason": ...})
```

### 重试机制

LLM 调用最多重试 3 次，使用指数退避 + jitter：`delay = 1.0 × 2^attempt + random(0,1)`（1s → 2s → 4s + jitter）。3 次全失败后该 batch 隐式保留（不产生 merge/remove/compress 条目，不丢数据）。失败时输出受影响索引范围：`批失败 [first_idx-last_idx]: error`。

```python
def _call(prompt):
    base_delay = 1.0
    for attempt in range(3):
        try:
            return requests.post(url, json={"messages": prompt})
        except Exception as e:
            if attempt < 2:
                delay = base_delay * (2**attempt) + random.uniform(0, 1)
                time.sleep(delay)
                continue
            return None
```

## 四、Phase 2：纯 LLM 验证（session_search 辅助）

### 过滤逻辑

从 remove 候选**排除以下三类直接删**，不进 Phase 2：
1. 清理自身产生的记录（含"清理"、"V5/V6方案"、"方法论"、"Memory cleaning methodology"等）
2. 空条目（仅 §）
3. merge/compress 已覆盖的原条目（从 merge/compress 数组可推导）

这三类硬逻辑删除，不需要 LLM 验证。

### 验证流程

**所有剩余 remove 候选都走 LLM 验证**（无 confidence 跳过门限）。session_search 仅提供辅助证据：

```
对每条 remove 候选：
    session_search(关键词) → 取原始对话片段 + confidence 分数
    ├─ confidence >= 0.3: LLM 收到 [条目] + [session snippet] + [原因]
    └─ confidence < 0.3:  LLM 仅收到 [条目] + [原因]（保守策略）
    LLM 判断正确性：
    ├─ correct → hindsight_retain(原文) → memory(remove)
    ├─ corrected → Python 校验 corrected_text → retain → remove
    └─ keep → 放回保留列表（LLM 判定不该删）
```

**retain 失败 → 不执行 remove，不丢数据。**

### 双 prompt 策略

- **有 session 上下文**（confidence ≥ 0.3）：prompt 含条目原文 + session snippet + 移除原因，要求判断事实性偏差
- **无 session 上下文**（confidence < 0.3）：prompt 仅含条目原文 + 移除原因，采取保守策略——不确定时一律 keep

### session_search confidence 计算

```python
# 关键词重叠得分
kw = re.findall(r'[\u4e00-\u9fff]{4,}|[a-zA-Z_]{4,}', text)
query = " AND ".join(kw[:3])
# FTS5 查询返回 snippet，confidence = 匹配的关键词数 / 总关键词数
```

### 时间窗口软降权

当 session 时间与条目中日期差异 > 90 天时，confidence × 0.5：

```python
if days_diff > 90:
    confidence = round(confidence * 0.5, 2)
```

### LLM 验证调用

- **最多重试 3 次**（和 Phase 1 一致，指数退避 1s→2s→4s + jitter），3 次全失败默认 keep
- JSON mode：`response_format: {"type": "json_object"}`
- prompt 包含：`条目原文[:400] + (session snippet[:200] | 无) + 移除原因`
- 返回格式：`{"verdict": "correct"/"corrected"/"keep", "corrected_text": "...", "note": "..."}`

### 验证后结果汇总

```python
# Phase 2 输出
{
  "correct": [{"index": 0, "original": "...", ...}],     # 提炼正确 → retain→remove
  "corrected": [{"index": 5, "original": "...", "corrected_text": "..."}],  # 有偏差 → retain修正版→remove
  "keep": [{"index": 3, "original": "...", "note": "..."}]  # 不应删 → 放回保留
}
```

### ✅ corrected_text 校验已前置到 verifier.py

corrected_text 校验现在在 `verifier.py` 的 `_verify_one()` 中完成（Phase 2 阶段），不再延迟到 `execute_cleanup`：

```python
# verifier.py _verify_one():
orig_kw = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", text))
corr_kw = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", corrected))
kw_overlap = len(orig_kw & corr_kw) / max(len(orig_kw), 1) if corrected else 0

has_real_fix = (
    corrected
    and len(corrected) > 10
    and corrected != text[:len(corrected)]
    and "修正" not in corrected[:20]
    and "需补充" not in corrected[:20]
    and kw_overlap > 0.2
)
if not has_real_fix:
    result["verdict"] = "correct"  # 降级为 correct
    result["note"] = f"corrected_text 无效（kw_overlap={kw_overlap:.2f}），降级为 correct"
```

效果：Phase 2 输出的 corrected 列表只包含通过校验的有效修正，execute_cleanup 可直接信任。

不能把空 corrected_text 降级为 keep（不动）——LLM 说"无法验证"不代表条目错了，只是 session_search 没找到对应对话。**应当视为 correct 处理**：retain 原文到 Hindsight 后 remove MEMORY.md 版。

## 五、三存储定位

| 存储 | 适合存放 | 不适合 |
|------|----------|--------|
| **MEMORY.md** | 每次对话必须知道的高频事实（端口、URL、偏好、教训） | 业务数据、过程记录、临时状态 |
| **Hindsight** | 可被 recall 召回但不需每轮占提示词的（项目金额、MES时间线、审计状态） | 结构化文档（放 Wiki） |
| **Wiki (Axiom)** | 需格式化阅读的文档、流程说明 | — |

## 六、注意事项（Pitfalls）

### ⚠️ 不要硬编码 PG 连接信息
必须从 `CLUSTERING_DB_URL` 环境变量读取，不写死在脚本里。

### ⚠️ 纯英文条目关键词匹配不到
ILIKE 只搜中文关键词片段。纯英文条目不会命中 PG，不代表 PG 中无副本。

### ⚠️ 先读原文件，再决策
不要凭印象描述 MEMORY.md 的结构。必须先用 read_file 或解析脚本读取原始文件内容。

### ⚠️ 不要添加结构骨架
用户明确确认：MEMORY.md 不加分区、不加 _index 目录、不加分类标签。

### ⚠️ 过程记录 ≠ 过期配置
条目如 `2026-05-08 方案C完成` 描述的是操作过程（应 remove），但 `shared-postgres pg_hba.conf 已配 0.0.0.0/0 trust` 是当前生效配置（应 keep）。

### ⚠️ 用户偏好：减少人的判断
分批让 LLM 做筛查，人只看最后确认结果，不从 140 条逐条判断。

### ⚠️ 先搞清楚文档结构，再动手清理
不要一上来就跑分类脚本。先查看原始文件的分隔符模式、条目数量、字符上限。

### ⚠️ 不要一次性送全部条目给 LLM
分批并行：10 条/批 × 8 线程，总耗时 ~60 秒。一次性送 141 条会超时。

### ⚠️ session_search 快但中文匹配率不稳定
session_search：毫秒级（本地 SQLite FTS5），但中文关键词切分不稳定导致匹配率低
Hindsight recall：10~15 秒/条（HTTP + embedding + reranker）
Phase 2 已改为纯 LLM 验证：session_search 仅提供辅助证据（confidence ≥ 0.3 时传 snippet），不决定验证路径。

### ⚠️ USER.md compress 不能丢含义
USER.md 的 compress 和 MEMORY.md 策略不同。USER 偏好必须完整可读，不能压缩到只有关键词片段。prompt 应明确"保持完整含义，精简句式"。

### ⚠️ 两条 MEMORY.md 和 USER.md 没有重复项
精确重复检查结果为 0。两文件虽有主题交叉（审计方法论、论文信息），但存的是不同视角（MEMORY=技术细节，USER=用户偏好），互补而非重复。
### ⚠️ 用户确认的规则不可用

以下规则因 MEMORY.md 缺少元数据而无法实现：
- 到期清理（无到期时间字段）
- 一次性事件过期（无记录时间戳）
- 长期未强化偏好（无最后更新时间）
- 否定覆盖清理（无同属性关联）
- 同属性冲突（无属性字段）
- 依赖联动（无条目间引用）
- 瞬时内容（无法区分验证码/临时链接）

### ⚠️ Phase 2 不要验证三类直接删条目

清理自身记录（"清理"、"V5方案"、"方法论"等）、空条目（仅 §）、merge/compress 已覆盖的原条目直接从 remove_list 排除，不进 Phase 2。

### ⚠️ retain 失败不能走 remove

`hindsight_retain` 失败时必须跳过 `memory(remove)`，不能丢数据。日志记录失败原因，标记 failed。

### ⚠️ `_verify_one` 必须先初始化 `data` 变量

```python
def _verify_one(entry_info: tuple) -> dict:
    # data 必须在 for attempt 循环之前初始化
    data = {"verdict": "keep", "note": "LLM response unparseable after 3 attempts"}
    for attempt in range(3):
        ...
```

如果 `data` 只在 try/except 和 `for...else` 块内赋值，当所有解析路径都失败（LLM 返回格式完全不对、且 append-only 入 `for...else` 的 regex rescue 也失败），`data` 会在最终的 `return` 处 `UnboundLocalError`。先初始化一个默认 keep 兜底。

### ⚠️ Step 6 直接删阶段会重复删除 merge/compress 已删的条目

`calc_remove_candidates()` 把 merge/compress 覆盖的索引放入 `remove_direct`。Step 1（merge）已经删掉了这些条目，Step 6 在 `remove_direct` 中遍历时又调 `_remove()` → `store.remove()` 返回 `"No entry matched"`。

**修复**：在 `_remove()` 中动态追踪已被成功删除的索引，Step 6 跳过：

```python
removed_already = set()  # 已被 merge/compress 成功删除的索引

def _remove(idx):
    r = store.remove(target, entries[idx][:80])
    if r.get("success"): 
        removed_already.add(idx)
        ...
    ...

# Step 6: 跳过已在 merge/compress 步骤中删除的索引
for r in remove_list:
    idx = r.get("index", -1)
    if idx in removed_already: continue  # 跳过已删的
    ...
```

动态追踪比事前计算 merge_compressed 更可靠——merge/compress 删除可能失败（如条目已不存在），动态追踪只跳过真正删除成功的索引。

不影响数据安全（store.remove 失败只返回 error，不误删），但 inflate fail 日志。

### ⚠️ `_retain()` 重试参数：2 次 × 120s timeout

Hindsight daemon 的 retain 时间主要消耗在 LLM 事实提取（`retain_extract_facts`），DeepSeek 调用最慢可达 **67 秒**。同步模式下的最佳经验参数：

```python
def _retain(content: str) -> bool:
    """retain 到 Hindsight（单条，2 次重试）。"""
    for attempt in range(2):                 # 2 次（原来 3 次，缩短总等待时间）
        try:
            req = urllib.request.Request(hindsight_url, ...)
            with urllib.request.urlopen(req, timeout=120): return True  # 120s
        except Exception as e:
            if attempt < 1: continue
            results["fail"].append((source, -1, f"retain (after 2 attempts): {e}"))
            return False
```

2 × 120s = 240s 兜底，正常情况下首试在 16-70s 内完成。3 次重试的旧参数总等待 3×30s=90s，但对大条目 30s 不够（LLM 调用 67s），每次都在第一试超时，导致 3×67s≈200s 才能走完。2×120s 既给够单次时间又控制总延迟。

### ⚠️ corrected_text 必须 Python 层校验，不能信任 LLM 输出

Phase 2 LLM 经常产生两类无效 corrected_text：
1. 空字符串（LLM 没给修正文本）
2. 占位文本（"需补充对话上下文验证"、"请先验证原始对话"等）

这两类都必须由 Python 判断为"无实质修正"，走 `retain(原文) + remove`（即 correct 路径），而不是 `retain(修正版) + remove`。**无效 corrected 降级为 correct**（不降级为 keep）——条目本身正确只是因为 session_search 没找到对应对话，不需要留在 MEMORY.md。

判断标准（增强版，含关键词重叠检查）：

```python
orig_kw = set(re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}', original))
corr_kw = set(re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}', corrected))
kw_overlap = len(orig_kw & corr_kw) / max(len(orig_kw), 1) if corrected else 0

has_real_fix = (
    corrected
    and len(corrected) > 10
    and corrected != original[:len(corrected)]
    and "修正" not in corrected[:20]
    and "需补充" not in corrected[:20]
    and kw_overlap > 0.2   # 至少20%中文关键词重叠
)
if not has_real_fix:
    result["verdict"] = "correct"  # 降级为 correct，不降级为 keep
    result["note"] = f"corrected_text 无效（kw_overlap={kw_overlap:.2f}），降级为 correct"
```

关键词重叠检查防止 session_search 搜错对话导致的"修正版"跑偏（如原文是关于"工程科技文档审计"，修正版是"分类干跑V6流水线执行记录"——关键词重叠为0，不视为有效修正）。

**实际效果**：corrected=0 是常态（Python 层校验拦截了所有无效修正）。

### ⚠️ Phase 1 → Phase 2 默认串行！必须用两个独立 Executor

Default 写法（错误）：
```python
with ThreadPoolExecutor(2) as exe:
    mem_p1 = exe.submit(classify_all, mem_entries, "MEMORY")
    user_p1 = exe.submit(classify_all, user_entries, "USER")
    mem_result = mem_p1.result()
    user_result = user_p1.result()
# Phase 2 在 with 块 exit 后才启动 — 等了两个 Phase 1
```

问题：MEMORY Phase 1 完成→等 USER Phase 1（几十秒）→才启动 MEMORY Phase 2。Phase 1 和 Phase 2 是串行的。

正确做法：**两个独立的 Executor**，MEMORY Phase 1 一拿到结果直接投进 Phase 2 的 Executor：

```python
p1_executor = ThreadPoolExecutor(max_workers=2)
mem_p1 = p1_executor.submit(classify_all, mem_entries, "MEMORY")
user_p1 = p1_executor.submit(classify_all, user_entries, "USER")

# MEMORY Phase 1 完成 → 即时启动 Phase 2
mem_result = mem_p1.result()
mem_direct, mem_need_v2 = calc_remove_candidates(mem_entries, mem_result)

p2_executor = ThreadPoolExecutor(max_workers=8)
mem_p2 = p2_executor.submit(phase2_verify, mem_entries, mem_need_v2, "MEMORY.md")

# 再接 USER Phase 1（可能已经跑完）
user_result = user_p1.result()
p1_executor.shutdown()

# USER Phase 2 投进去（和已在跑的 MEMORY Phase 2 并行）
if user_need_v2:
    user_p2 = p2_executor.submit(phase2_verify, user_entries, user_need_v2, "USER.md")

mem_v2 = mem_p2.result()
user_v2 = user_p2.result() if user_p2 else {...}
p2_executor.shutdown()
```

流水线效果：
```
MEMORY P1 ──→ MEMORY P2 (不等 USER)
USER   P1 ───────→ USER   P2 (和 MEMORY P2 并行)
```

验证标志：USER.md 0 条需验证时，MEMORY Phase 2 应该在 USER Phase 1 完成之前就已启动并跑了部分条目。

### ⚠️ 两个文件各开各的 8 线程池

Phase 1 和 Phase 2 中，MEMORY.md 和 USER.md 各创建自己的 `ThreadPoolExecutor(max_workers=8)`。总并发最多 16。不用共享池——API key 无并发限制。

### ⚠️ Batch 失败要输出索引范围

输出格式应为 `批失败 [first_idx-last_idx]: error`，而非只有 `批失败: error`，方便追踪受影响的条目。

### ⚠️ docstring 不撒谎

脚本的 docstring 只写事实（"分类干跑"），不写代码不强制的内容（如"执行阶段需遵守..."）。

### ⚠️ Retain API 支持 `async: true`（推荐非阻塞模式）

`POST /v1/default/banks/{bank_id}/memories` 支持 `async` 参数：

```python
{"items": [{"content": "..."}], "async": true}
```

- `async: false`（默认）：同步等待，daemon 返回 HTTP 200 时 retain 已完成
- `async: true`：立即返回，daemon 后台异步处理，脚本不需要等待

**推荐用 `async: true` 做 fire-and-forget**，避免 retain 阻塞整个 pipeline。但副作用是脚本无法确认 retain 成功与否，remove 策略需改为"投了就删"（信任后台）。

### ⚠️ 注意 retain 耗时：LLM 提事实可能 60s+，同步模式需加大 timeout

Hindsight retain 的时间主要消耗在 daemon 内部调用 DeepSeek 做事实提取（`retain_extract_facts`），单次可达 **67 秒**。如果同步等待（`async: false`，默认）且 timeout=30s，会导致频繁超时重试。

同步模式的正确做法：`timeout=120`（而非 30）：

```python
with urllib.request.urlopen(req, timeout=120): return True
```

但更好的做法是**用 `async: true`**——立即返回，daemon 后台处理，完全绕过 timeout 问题。

### ⚠️ 更改 embedding 模型需改 3 个配置文件

Hindsight daemon 的 embedding 模型配置在 **3 个文件**中，缺一不可：

| 文件 | 用途 |
|------|------|
| `/root/.hindsight/daemon.env` | daemon 主配置 |
| `/root/.hindsight/profiles/hermes.env` | 用户 profile 配置 |
| `/root/.hermes/.env` | Hermes 根环境变量（plugin 启动时读） |

更改前必须**备份数据库**（`pg_dump` 或 `docker exec shared-postgres pg_dump`）。确认没有其他 profile 引用旧模型（`grep -rl "旧模型名" /root/.hindsight/ /root/.hermes/.env`）。

bge-m3（1024维）可替换 bge-large-zh-v1.5（1024维），维度一致无需迁移 schema，语义空间兼容。切换后对已有向量 recall 仍有效。

### ⚠️ Hindsight daemon 内部已自带内容切分，脚本层无需手动 chunk

Hindsight daemon 内置了 `retain_chunk_size`（默认 3000 字符）的切分逻辑：

```python
# orchestrator.py:669
chunk_size = getattr(config, "retain_chunk_size", 3000)
# orchestrator.py:673
content_chunks = fact_extraction.chunk_text(content.content, chunk_size)
```

原始内容被切成 3000 字符的块，每块独立走 LLM 事实提取 → embedding → 建链。**脚本层不需再手动切分内容**——daemon 自己处理。

⚠️ embedding 模型从 `BAAI/bge-large-zh-v1.5` 换为 `BAAI/bge-m3` 后，之前 SiliconFlow API 的 512 token 限制（error 413）已解决——bge-m3 支持 8192 tokens。旧向量与新向量同维（1024），recall 兼容。如需改变更数据库备份命令：

```bash
docker exec shared-postgres pg_dump -U postgres -d hindsight -F c -f /tmp/backup.dump
docker cp shared-postgres:/tmp/backup.dump /root/hindsight_db_$(date +%Y%m%d_%H%M%S).dump
```

### ⚠️ 必须用 background 模式（foreground timeout 上限 600s）

总耗时估算（8 线程 + 120s timeout）：
- Phase 1（并行分类）：~95s（MEMORY 145条 15批 + USER 35条 4批，8线程并行）
- Phase 2（纯 LLM 验证）：~120s（38 条候选 × 2-3s/条 并行）
- 执行 retain（8 线程并行，120s timeout）：~70s（正常条目 16s，大条目 ~67s）
- 执行 remove（串行）：~5s
- 总计：**~5 分钟**

前台 timeout 硬上限 600s（已拒绝 600 以上的值），必须用：
```python
terminal(background=true, notify_on_complete=true, timeout=600)
```

注意：`output_preview` 只显示最近 ~2000 字符，执行阶段可能看不到进度输出。直接查文件系统或 daemon 日志看实际进度：

```bash
# 查文件进度
grep -c '^§$' /root/.hermes/memories/MEMORY.md
# 查 daemon 日志
journalctl -u hindsight-daemon --since "1 min ago" | grep "RETAIN COMPLETE"
```

### ⚠️ 执行前自动备份（shutil.copy2 + 时间戳）

`execute_cleanup()` 入口自动备份目标文件，带时间戳后缀，无需手动操作：

```python
mem_dir = Path("/root/.hermes/memories")
bak_path = mem_dir / f"{target.upper()}.md.bak.{os.popen('date +%Y%m%d_%H%M%S').read().strip()}"
shutil.copy2(str(src_path), str(bak_path))
print(f"  📦 备份: {bak_path.name}")
```

备份在 retain 之前完成，确保始终有可用回滚点。`restore` 不在脚本范围内——用户手动 `cp .bak` 即可。

### ⚠️ 脚本可重复执行（幂等性）

`--apply` 在已部分清理的数据上重跑是安全的：分类结果不同（条目更少），retain 只对新产生的 remove 候选执行。已删除的条目不会再次被匹配（MemoryStore.remove 用 substring 匹配剩余条目）。备份会自动覆盖旧备份。`removed_already` 追踪保证不重复删除。

### ⚠️ retain 并行 + remove 串行（关键性能优化）

脚本的 retain 操作是**独立 HTTP POST**，互不阻塞。应全部并行投递：

```python
# 收集所有需 retain 的条目
retain_tasks = [(idx, content_to_retain, label) for ...]

def _retain_worker(idx_content_label):
    idx, content, label = idx_content_label
    return (idx, _retain(content), label)  # _retain 有 3 次重试

# 全部并发投递
retain_ok, retain_fail = set(), set()
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = [pool.submit(_retain_worker, t) for t in retain_tasks]
    for f in as_completed(futures):
        idx, ok, label = f.result()
        if ok: retain_ok.add(idx)
        else:  retain_fail.add(idx)

# 串行 remove（文件锁串行化）
for idx in sorted(retain_ok):
    _remove(idx)
```

效果（实际数据）：37 条 retain 从串行 ~9 分钟降到并行 ~1.2 分钟（8 线程）。仅 remove 阶段保留串行（MemoryStore 有 fcntl 文件锁，串行更安全）。

注意：`_retain_worker` 是纯函数，不捕获闭包中的 `results` 列表。结果通过 `retain_ok`/`retain_fail` 集合传递回主线程，主线程再统一写 `results`。

## 七、执行阶段（代码级，不靠 Agent 手调）

`MemoryStore` 和 Hindsight HTTP API 都可以在独立 Python 脚本中直接调用，不经过 Hermes agent 的 tool call。

### ⚠️ 默认 dry-run，必须加 `--apply` 才执行

清理脚本**默认只分类报告**，绝不修改数据。执行不可逆操作（retain + memory remove）必须显式传 `--apply`：

```bash
# dry-run（默认，不修改数据）
bash ~/.hermes/scripts/memory-cleanup/run.sh

# 实际执行清理
bash ~/.hermes/scripts/memory-cleanup/run.sh --apply

# JSON 输出（结构化结果，适合 CI/日志）
bash ~/.hermes/scripts/memory-cleanup/run.sh --json

# 多轮投票（降低 LLM 方差，cron 推荐）
bash ~/.hermes/scripts/memory-cleanup/run.sh --vote 2
```

脚本入口通过 `typer` 解析参数，所有 `execute_cleanup` 调用包在 `if apply:` 后面。dry-run 模式输出提示信息后退出。

### MemoryStore 导入方式

```python
import sys
sys.path.insert(0, '/root/.hermes/hermes-agent')
from tools.memory_tool import MemoryStore

store = MemoryStore(memory_char_limit=50000, user_char_limit=15000)
store.load_from_disk()         # 读盘 → 按 § 分割 → 去重
store.add("memory", "内容")    # 原子写入（tempfile + fsync + os.replace）
store.remove("memory", "子串")
store.replace("memory", "旧", "新")
```

所有安全机制都在：`fcntl.flock` 文件锁、`tempfile.mkstemp` 原子重命名、`_scan_memory_content` 安全扫描。不走 LLM。

⚠️ `MemoryStore()` 默认 `memory_char_limit=2200`，必须传 `50000` 否则 add 会被拒绝。

### Hindsight Retain HTTP API

```python
import requests
requests.post(
    "http://127.0.0.1:9177/v1/default/banks/hermes/memories",
    json={"items": [{"content": "要保留的内容"}]}
)
```

### Hindsight Recall HTTP API

```python
r = requests.post(
    "http://127.0.0.1:9177/v1/default/banks/hermes/memories/recall",
    json={"query": "搜索文本", "top_k": 3}
)
```

### 执行规则

- **compress → 不 retain 原文到 Hindsight**：Hindsight auto-sync 已经自动存储了每轮对话内容，原文不需要再手动 retain。压缩流程简化为 `remove(原文) → add(精简版)`，不做额外的 HTTP POST。
- **retain 并行 + remove 串行**：`ThreadPoolExecutor(max_workers=8)` 同时投递所有 retain（每条独立 HTTP POST，互不阻塞），全部返回后再串行调 `MemoryStore.remove()`（文件锁串行化），避免单条 retain 15s 阻塞后续所有操作

```python
# 收集所有需 retain 的条目 → 并行投递
retain_tasks = [(idx, content_to_retain, label) for ...]
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = [pool.submit(_retain_worker, t) for t in retain_tasks]
    for f in as_completed(futures):
        idx, ok, label = f.result()
        if ok: retain_ok.add(idx)    # 标记成功
        else:  retain_fail.add(idx)  # 标记失败

# 串行 remove（只删 retain 成功的）
for idx in sorted(retain_ok):   # 排序保证一致性
    _remove(idx)
```
- **retain 失败 → 不 remove**：每条 `retain` 返回 success/failed，failed 的条目跳过 remove，记日志
- **replace 失败 → fallback**：`memory(replace)` 失败（old_text 找不到），改成 `memory(add)` + `memory(remove)`
- **执行报告**：清理完成后写入 `~/.hermes/memories/cleanup-report-{timestamp}.json`；当前代码不维护 `cleanup_progress.jsonl` 断点续跑日志

### 日志格式

```jsonl
{"ts":"2026-05-23T20:00:00","action":"retain","target":"memory","index":5,"status":"ok"}
{"ts":"2026-05-23T20:00:01","action":"remove","target":"memory","index":5,"status":"ok"}
{"ts":"2026-05-23T20:01:00","action":"retain","target":"memory","index":6,"status":"failed","error":"HTTP 500"}
{"ts":"2026-05-23T20:01:10","action":"merge","target":"memory","indices":"[20,21]","status":"ok"}
{"ts":"2026-05-23T20:01:15","action":"compress","target":"memory","index":100,"status":"ok"}
```

## 八、脚本设计原则（用户偏好）

### 脚本自包含

清理脚本应直接调 Hindsight API + MemoryStore，不依赖 Hermes Agent 工具。通过 `--apply` 标志控制执行，默认 dry-run。用 `argparse` 解析参数。

### 确定性逻辑优先

敏感操作（如 corrected 中空修正文本处理）用 Python 逻辑而非 LLM 判断。在 `_retain` → `_remove` 的流程中，`has_real_fix` 判断完全基于确定性规则（关键词重叠、长度、占位文本检测），不依赖 LLM 二次确认。

### 不可逆操作默认 dry-run

修改数据需显式 opt-in（`--apply`）。脚本在 `main()` 入口检查参数，所有 `execute_cleanup` 调用包在 `if args.apply:` 后面。

### 先审再跑

不要一到任务就写脚本跑。先审查逻辑/脚本/设计，获确认后再执行。

## 九、脚本位置与代码质量

- `~/.hermes/scripts/memory-cleanup/` — 重构后的 Python 包结构（v6.1.0）
  - 入口：`python -m memory_cleanup` 或 `memory-classify-v6`（兼容旧命令）
  - 源码：`src/memory_cleanup/`（typer CLI + core/classifier.py + core/verifier.py）
  - 兼容入口：`memory-classify-v6.py`（仅重定向到 `src/memory_cleanup/cli.py`）
  - 旧版脚本 `memory-classify-v6.py`（flat 结构）已废弃，保留在 `backups/` 目录

### 执行方式

```bash
# 方式1：run.sh（推荐，自动处理 venv）
cd ~/.hermes/scripts/memory-cleanup
bash run.sh              # dry-run
bash run.sh --apply      # 实际执行
bash run.sh --vote 2     # 多轮投票（cron 推荐）

# 方式2：直接调用（需先 setup）
cd ~/.hermes/scripts/memory-cleanup
uv venv venv                          # 首次需创建 venv
uv pip install --python venv/bin/python typer requests PyYAML
PYTHONPATH=src venv/bin/python -m memory_cleanup --help
PYTHONPATH=src venv/bin/python -m memory_cleanup        # dry-run
PYTHONPATH=src venv/bin/python -m memory_cleanup --apply
PYTHONPATH=src venv/bin/python -m memory_cleanup --vote 2  # 多轮投票
```

### 依赖安装（首次使用）

```bash
cd ~/.hermes/scripts/memory-cleanup
uv venv venv
uv pip install --python venv/bin/python typer requests PyYAML
```

若已安装 CLI：`memory-cleanup --help` 可直接使用（通过 `pip install -e .` 或 `uv pip install -e .`）。

### 代码质量模式（审计积累）

清理脚本的代码质量检查清单：

| 检查项 | 推荐做法 | 违反后果 |
|--------|---------|---------|
| docstring 与代码一致 | 描述脚本实际行为：分类+执行，dry-run/--apply | 同级评审会质疑 |
| 模块级 import | 所有 import 放顶部，不用函数内 lazy import | 不影响功能，但增加代码审查噪音 |
| 搜索键长度 | `store.remove()` 的搜索键 ≥80 字符，防碰撞 | 60 字符理论上够用，80 更安全（条目开头唯一） |
| `MAX_WORKERS` 自适应 | `min(32, (os.cpu_count() or 4) + 4)` 而不是硬编码 8 | 高核机器未充分利用 |
| `_retain()` 重试 | 3 次重试，retain 失败不 remove | retain 偶发失败时数据安全 |
| step 6 双删防护 | `removed_already` 动态追踪 | 日志虚高 fail 计数 |
| corrected_text 校验 | Python 层：关键词重叠+占位文本检测 | 垃圾内容入 Hindsight |
| 截断安全 | `_truncate()` 后缀 '…（截断）' 计入 max_len | 截断后超过字符限制 |
| JSON 解析兜底 | `_parse_json()` 四路径：json.loads → strip/clean → 栈匹配 → 正则提取回退 | 深层嵌套 JSON 解析失败 |
| JSON mode | `response_format: {"type": "json_object"}` 强制 LLM 输出 JSON | 解析失败率从 ~15% 降到 ~2% |
| 指数退避重试 | `1.0 × 2^attempt + random(0,1)` 避免惊群 | 并发重试同时命中 LLM 网关 |
| 环境变量安全 | `_safe_int_env()` try/except 包裹 | 非法值导致 int() 崩溃 |
| Config 校验 | `__post_init__` 运行时校验 batch_size/char_limit | 配置错误在运行时才暴露 |
| 批失败单条重试 | 整批失败后逐条调用 LLM，仍失败加入 flagged | 单条 LLM 故障导致整批丢失 |
| 多轮投票 | `--vote N` 跑 N 轮；remove 取并集，compress/merge/hindsight 取交集 | cron 自动化时结果不稳定 |
| Ctrl+C 优雅退出 | 信号处理 + `_running` 标志 | 中断时丢数据 |

## 十、CLI 功能参考（v6.x 新增）

### `--json` 结构化输出

JSON 模式下所有 `print()` 被 `contextlib.redirect_stdout(io.StringIO())` 抑制，仅输出完整 JSON 报告到 stdout：

```json
{
  "version": "6",
  "timestamp": "2026-06-04T16:06:00+0800",
  "mode": "dry-run",
  "total_time_s": 13.3,
  "tokens": {"prompt": 12500, "completion": 3400},
  "sources": {
    "MEMORY.md": {
      "total_entries": 143,
      "phase1_merge": 2,
      "phase1_compress": 3,
      "phase1_remove": 7,
      "after_cleanup": {"keep": 131, "keep_chars": 28000},
      "phase2": {"correct": 5, "corrected": 1, "keep": 1}
    }
  }
}
```

### Token 追踪

`LLMClient` 内部累计 `total_prompt_tokens` 和 `total_completion_tokens`，终端输出：

```
💰 Token 消耗: prompt=12,500 completion=3,400
```

### 报告持久化

清理完成后自动保存 JSON 报告到 `~/.hermes/memories/cleanup-report-{YYYYMMDD_HHMMSS}.json`：
- 含版本号、时间戳、模式（dry-run/apply）
- 两份数据各自的 merge/remove/compress 计数
- Phase 2 correct/corrected/keep 分布
- 执行模式（--apply）下含 ok/fail 结果

### 信号处理

`SIGINT`/`SIGTERM` 触发优雅退出：
- 设置 `_running = False` 全局标志
- 当前批次完成后立即停止，不启动新批次
- 已完成的 Phase 1/2 结果仍可输出

### 耗时统计

双阶段计时：
- `start_time`：整个程序入口
- `phase1_start`：Phase 1 启动时刻
- 输出 Phase1+Phase2 总耗时 和 整体耗时

### Config 校验

`AppConfig.__post_init__` 运行时校验：
- `batch_size > 0`
- `max_workers > 0`
- `char_limit >= 100`
- `output_mode ∈ {human, json}`
- 非法值抛 `ValueError`

### 配置文件（`config/default.yaml`）

```yaml
output_mode: human      # human 或 json
memory_path: "/root/.hermes/memories/MEMORY.md"
user_path: "/root/.hermes/memories/USER.md"
session_db_path: "/root/.hermes/state.db"
llm_url: "http://127.0.0.1:4142/v1/chat/completions"
llm_model: "s-deepseek-v4-flash"
batch_size: 10           # MEMORY.md 每批条目数
user_batch_size: 10      # USER.md 每批条目数
vote_count: 1            # 投票轮数（cron 推荐 2）
memory_char_limit: 50000
user_char_limit: 15000
log_level: "INFO"
```

所有字段均支持 `MEMORY_CLEANUP_*` 环境变量覆盖。

### 参考文件

- `references/dry-run-2026-05-27.md` — 2026-05-27 干跑测试结果（MEMORY.md 11%, USER.md 33%）
