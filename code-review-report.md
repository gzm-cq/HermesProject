# 飞轮代码审查报告

**审查范围**：Hermes 项目全部飞轮相关代码
**审查日期**：2026-07-03
**审查人**：火眼眼（Code Review Expert）

---

## 一、总体评价

飞轮系统的架构设计成熟度高，三大闭环（数据飞轮、能力飞轮、Router 飞轮）分工清晰，Feature Flag 体系完善，熔断器/降级/fallback 机制覆盖面广。代码整体质量在 P0-P3 八轮迭代后有显著提升，但仍有若干线程安全、内存管理和可维护性问题需要关注。

**亮点**：
- 三路并行 recall + ThreadPoolExecutor 设计有效降低了 P99 延迟
- 熔断器 + 飞书通知的自动降级链路完整
- Token 预算守门按域分配 + 剩余再分配的策略设计精巧
- Cron 编排层的 flock 防重入 + 状态文件依赖检查 + 指数退避重试三重保障
- SkillOpt Runner 的增量采集 + denylist 过滤 + 负反馈累积机制

---

## 二、问题清单

### 🔴 Blocker（必须修复）

---

#### B-1: PG 连接缓存跨线程共享，存在线程安全隐患

**文件**：`plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py:53-93`

**问题**：`_pg_conn_cache` 是全局 dict，`_get_cached_conn()` 返回的 psycopg2 connection 对象被多个线程共享。psycopg2 connection **不是线程安全的**（[官方文档明确说明](https://www.psycopg2/docs/install.html#with-thread-support)），在 `pre_llm_call` 的并行 recall 场景下，多个线程可能同时通过同一个 connection 执行 `_causal_boost()` 查询，导致游标错乱或查询失败。

**影响**：高并发场景下因果链 boost 查询可能返回错误结果或抛异常（当前被 `except` 静默吞掉，表现为因果链提权间歇性失效）。

**建议**：
- 方案 A（推荐）：改用 `psycopg2.pool.ThreadedConnectionPool`，每次 `_get_cached_conn()` 从池中借出独占连接，用完归还
- 方案 B：改为 thread-local 存储，每个线程维护独立连接
- 方案 C（最小改动）：`_causal_boost` 内部新建连接，不复用缓存（牺牲一些性能换取安全）

---

#### B-2: skill_matcher 缓存无上限，长驻进程内存泄漏

**文件**：`plugins/knowledge-navigation/src/knowledge_navigation/core/skill_matcher.py`

**问题**：`_embedding_cache` 和 `_query_embedding_cache` 两个全局 dict 没有容量上限，在网关常驻进程中会持续增长。每个 embedding 是 1024 维 float32 向量（约 4KB），假设每天 1000 次查询、每次匹配 20 个 skill，一天积累约 80MB，一个月可达 2.4GB。

**影响**：网关进程内存缓慢增长，最终触发 OOM 或被系统 kill。

**建议**：
```python
from functools import lru_cache

# 方案：使用 lru_cache 替代手动 dict 缓存
@lru_cache(maxsize=512)
def _get_skill_embedding(skill_name: str) -> list[float] | None:
    ...
```
或手动实现 LRU 淘汰（参考 hooks.py 中 `_injected_ids` 的 `_OrderedDict` 模式）。

---

#### B-3: cron-catchup-repair.sh 在非函数上下文使用 `local` 关键字

**文件**：`scripts/cron-wrappers/cron-catchup-repair.sh:128-129`

**问题**：
```bash
local script_path="/root/.hermes/scripts/${JOB_SCRIPT}"
local cd_cmd=""
```
`local` 关键字只能在函数内部使用。这两行位于 `for` 循环体中，不在任何函数内，在 bash 中会报错 `local: can only be used in a function`。虽然 `set -euo pipefail` 下脚本报错会退出，但由于 `local` 赋值失败不会改变 `$?`（取决于 bash 版本），可能被静默忽略，导致 `script_path` 和 `cd_cmd` 变量为空。

**影响**：catchup 模式下直接执行脚本的路径（方式 2）可能无法正确构造路径，导致追赶失败。

**建议**：移除 `local` 关键字，改为普通变量赋值：
```bash
script_path="/root/.hermes/scripts/${JOB_SCRIPT}"
cd_cmd=""
```

---

### 🟡 Suggestion（建议修复）

---

#### S-1: pre_llm_call 函数过长（~500 行），严重违反单一职责

**文件**：`plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py:714-1260`

**问题**：`pre_llm_call` 函数从第 714 行延伸到约第 1260 行，包含了门控、Router 调用、并行 recall、多跳展开、因果链 boost、Compaction、跨域去重、Turn-to-turn 去重、Token 预算、上下文组装等十余个阶段。每个阶段内部逻辑复杂，但都平铺在一个函数中。

**影响**：极难维护和测试。任何修改都需要理解整个 500 行流程，单元测试几乎不可能。

**建议**：按阶段拆分为独立函数：
```python
def pre_llm_call(session_id, user_message, **kwargs):
    if not _pass_gates(session_id, user_message, **kwargs):
        return None
    mask = _get_router_mask(session_id, user_message)
    raw_results = _execute_recall(session_id, user_message, mask)
    candidates = _post_process(raw_results, session_id, user_message)
    return _assemble_context(candidates, session_id, user_message)
```

---

#### S-2: public_api.py 每次调用创建新 DB 适配器，无连接池

**文件**：`plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/public_api.py:55-64`

**问题**：`_recall_core` 在 `adapter is None` 时创建新的 `PluginDatabaseAdapter(cfg.db_url)`，每次 `recall_from_tree_raw` 调用都会新建 PG 连接。在高频 `pre_llm_call` 场景下（每次用户消息触发一次），这意味着每秒可能新建多个 PG 连接。

**影响**：PG 连接数压力，连接建立开销（TCP 三次握手 + auth）增加 recall 延迟。

**建议**：在 public_api 层维护一个 thread-local 的 adapter 池，或使用 `psycopg2.pool.SimpleConnectionPool`，按 thread 复用连接。

---

#### S-3: 关键词提取函数 `_extract_keywords` 在三处重复定义

**文件**：
- `hooks.py:416-435` — 英文 + CJK 2-gram，用于 eval query 匹配
- `recall.py:31-54` — 英文（≥3 字符）+ CJK 2-gram，用于科目定位
- `skill_matcher.py` — 英文 + 中文整段 + 2-gram，用于 skill 预筛选

**问题**：三处实现略有不同（英文最短字符数不同、CJK 处理策略不同），但核心逻辑高度相似。代码重复导致维护负担：修改停用词表需要改三处。

**建议**：提取到 `knowledge_navigation.core.text_utils` 模块，统一基础实现，各调用方通过参数差异化配置：
```python
def extract_keywords(text: str, *, min_en_length: int = 2, stop_chars: set[str] | None = None) -> set[str]:
    ...
```

---

#### S-4: ThreadPoolExecutor 每次 pre_llm_call 新建，未复用

**文件**：`hooks.py:803-846`

**问题**：每次 `pre_llm_call` 在多路并行时创建新的 `ThreadPoolExecutor(max_workers=_active_count)`，调用结束后 `executor.shutdown(wait=False, cancel_futures=True)`。频繁创建/销毁线程池有线程创建开销，且 `wait=False` 可能导致线程在后台持续运行未被完全清理。

**影响**：高频调用下线程创建开销累积；`wait=False` 的 shutdown 可能留下僵尸线程。

**建议**：使用模块级共享线程池（如 `concurrent.futures.ThreadPoolExecutor` 单例），通过 `submit` + `as_completed` 管理任务。注意需要限制最大并发数。

---

#### S-5: recall.py 中 locate_best_subject 的 embedding 兜底定位可能 N+1 查询

**文件**：`plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/recall.py:91-131`

**问题**：当关键词匹配失败时，对每个 domain 调用 `adapter.get_child_nodes(domain["id"])` 获取子节点计算 centroid。如果 domain 数量较多（如 10+），会触发 10+ 次 DB 查询。

**影响**：冷启动或关键词未命中时 recall 延迟显著增加。

**建议**：
- 缓存 domain centroid（TTL 5 分钟，类似 placement.py 的 `_leaf_cache`）
- 或在 DB 层一次性查询所有 domain 的子节点 k_vector 均值（SQL 聚合）

---

#### S-6: filtering.py MMR 算法 O(n²) 复杂度

**文件**：`plugins/knowledge-navigation/src/knowledge_navigation/core/filtering.py` — `_mmr_diversity()`

**问题**：MMR 需要计算每对候选之间的相似度，复杂度为 O(n²)。当候选数量较大（如 30+）时，计算量显著增加。

**影响**：在 max_results * 3 = 45 条候选时，MMR 需要计算 ~1000 次余弦相似度。

**建议**：
- 候选数 > 20 时先做一次快速聚类（如 k-means with k=max_results），从每个簇选 top-1 再做 MMR
- 或设上限：MMR 只在 top-15 候选中运行，其余直接按 score 截断

---

#### S-7: placement.py `_leaf_cache` 在写入后可能返回过期数据

**文件**：`plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/placement.py`

**问题**：`_leaf_cache` 缓存科目子节点列表，TTL 60 秒。但在 batch 写入新知识点后，缓存未失效。如果 60 秒内有同科目的新知识点被插入，下一次 recall 可能看不到新知识点（因为用的是缓存的子节点列表）。

**影响**：高频对话场景下，用户刚讨论的知识点可能不在 60 秒内的后续 recall 结果中。

**建议**：在 `batch_write` 成功后主动清除对应 `parent_id` 的缓存条目，或将 TTL 降到 10-15 秒。

---

#### S-8: cron-periodic-detect.sh 去重状态文件在 /tmp，重启后丢失

**文件**：`scripts/cron-wrappers/cron-periodic-detect.sh:28`

**问题**：`DEDUP_FILE="/tmp/cron-periodic-dedup.json"` 放在 /tmp 下。系统重启后 /tmp 被清空，去重状态丢失。如果重启后某个 job 仍处于 error 状态，会重复发送飞书告警。

**影响**：系统重启后可能产生重复告警（非致命，但影响告警信噪比）。

**建议**：将去重状态文件放到 `${HERMES_HOME}/lib/cron-state/` 下，与其他状态文件一致。

---

#### S-9: extract_new.py 并行提取无速率限制

**文件**：`plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/extract_new.py:280-309`

**问题**：XL 输入（>12000 字符）会触发最多 6 路并行 LLM 提取，每路调用 `_extract_one_chunk` 发送一次 LLM 请求。没有任何速率限制或并发控制。

**影响**：高频对话场景下，多个 post_llm_call 同时触发 XL 提取，可能瞬间发出大量 LLM 请求，触发 API 限流或耗尽 token 预算。

**建议**：
- 使用信号量限制全局并行提取数量（如 `threading.Semaphore(3)`）
- 或在 hooks.py 的 post_llm_call 队列层做速率限制

---

#### S-10: self-evolving 算子的 LLM Prompt 硬编码在源码中

**文件**：
- `revision.py:24-100` — 4 个 prompt 模板
- `recombination.py:19-35` — 1 个 prompt 模板
- `refinement.py:23-71` — 3 个 prompt 模板

**问题**：所有 LLM prompt 以字符串字面量写在 Python 源码中，修改 prompt 需要改代码并重新部署。

**建议**：将 prompt 外部化到 YAML/JSON 配置文件，支持热更新：
```yaml
# config/prompts.yaml
revision:
  auto_detect: |
    分析以下失败内容...
```

---

#### S-11: skillopt_runner.py 中 `_detect_feedback` 的关键词匹配可能误报

**文件**：`scripts/skillopt-runner/skillopt_runner.py:397-438`

**问题**：负反馈检测使用简单子串匹配（`if ph in lower`），如 `"wrong"` 会匹配 `"wrongly"`、`"nope"` 会匹配 `"nope-fully"`。中文部分虽有排除逻辑（如排除"不可以"中的"可以"），但覆盖不完整：`"不行"` 会匹配 `"行不通"` 中的子串（虽然是正向匹配），`"错误"` 会匹配 `"没有错误"`（正向语义）。

**影响**：负反馈计数可能偏高，导致某些 skill 被过度标记为有问题。

**建议**：使用词边界匹配（英文）和更精确的中文分词 + 情感分析。至少对中文关键词增加否定前缀排除。

---

### 💭 Nit（可改进）

---

#### N-1: hooks.py 中多处函数内 import

**文件**：`hooks.py` — `_causal_boost` 内 `import psycopg2`、`_batch_embed` 内 `import requests`、`pre_llm_call` 内 `import os as _os`

**问题**：函数内 import 是 Python 的合法用法（用于可选依赖延迟加载），但在这些场景中 import 的模块（psycopg2, requests, os）都是必然需要的，延迟加载无意义，反而增加了每次调用的 import 查找开销。

**建议**：移到模块顶层 import。如果是为了避免循环导入，添加注释说明。

---

#### N-2: recall.py 中 `format_context_lines` 内部 import html

**文件**：`plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/recall.py:432`

**问题**：`import html as _html` 在函数内部，每次调用 `format_context_lines` 都会执行一次 import 查找。

**建议**：移到模块顶层。

---

#### N-3: 多处日志使用 `logger.debug` 但缺乏结构化字段

**文件**：多处，如 `recall.py:267`、`placement.py` 等

**问题**：部分 debug 日志只输出字符串消息，没有 `extra={}` 结构化字段，不利于后续日志分析。

**建议**：统一使用 `extra={"event": "...", "session_id": ...}` 格式。

---

#### N-4: cron 脚本中 Python heredoc 与 bash 变量混用容易出错

**文件**：`cron-boot-detect.sh:31-125`、`cron-periodic-detect.sh:36-133`

**问题**：Python heredoc 中混用 `$(date '+%Y-%m-%d %H:%M')` 和 `$DEDUP_FILE` 等 bash 变量展开。如果 Python 代码中有 `$` 字符（如正则表达式），会被 bash 误展开。

**建议**：对不需要 bash 变量展开的 Python heredoc 使用 `<<'PY'`（单引号），需要展开的用 `<<PY`。当前 `cron-periodic-detect.sh` 已部分采用了这个策略，但 `cron-boot-detect.sh` 没有（第 31 行 `<<PY` 应改为 `<<'PY'` 并通过环境变量传参）。

---

#### N-5: skillopt_runner.py 类型注解风格不统一

**文件**：`scripts/skillopt-runner/skillopt_runner.py`

**问题**：混用旧式 `Dict[str, Dict]`、`List[str]`、`Optional[str]` 和新式 `dict`、`list`。文件顶部有 `from __future__ import annotations`，可以使用新式注解。

**建议**：统一使用 Python 3.9+ 内建泛型（`dict[str, Any]`、`list[str]`），删除 `typing` 中的 `Dict`、`List`、`Optional` 导入。

---

## 三、各模块审查详情

### 3.1 数据飞轮 — 知识导航插件

| 组件 | 文件 | 核心功能 | 质量评分 |
|------|------|----------|----------|
| Router | `core/router.py` | LLM 三路 mask 决策 | ★★★★☆ |
| Hooks | `core/hooks.py` | 三层门控→Router→三路 recall→后处理注入 | ★★★☆☆ |
| Skill Matcher | `core/skill_matcher.py` | 关键词预筛→Embedding→LLM 精排 | ★★★★☆ |
| Filtering | `core/filtering.py` | 标记排除/时态融合/MMR/跨域去重/Token 守门 | ★★★★☆ |
| Use Log | `core/use_log.py` | 批量写入 + 定时刷盘记忆使用日志 | ★★★★★ |
| Circuit Breaker | `core/circuit_breaker.py` | 熔断器 + 飞书双通道通知 | ★★★★★ |

**核心问题**：
- `hooks.py` 的 pre_llm_call 过长（S-1），PG 连接缓存线程不安全（B-1）
- skill_matcher 缓存无上限（B-2），ThreadPoolExecutor 未复用（S-4）

**亮点**：
- `_CompactionTracker` 长对话注入限制设计巧妙
- `_HitCounter` 高频记忆 boost + LRU 淘汰
- Turn-to-turn 去重支持 demote/remove 双模式
- 评测查询匹配的三级策略（explicit → exact → fuzzy）设计严谨

### 3.2 数据飞轮 — 知识树插件

| 组件 | 文件 | 核心功能 | 质量评分 |
|------|------|----------|----------|
| Public API | `public_api.py` | 知识树 recall 公共接口 + 多跳关联 | ★★★★☆ |
| Recall | `recall.py` | 科目定位→注意力筛选→时态过滤→格式化 | ★★★★☆ |
| Extract | `extract_new.py` | 对话知识提取（动态切分 + 并行） | ★★★★☆ |
| Placement | `placement.py` | Embedding→去重→矛盾检测→batch 写入 | ★★★★☆ |

**核心问题**：
- public_api 无连接池（S-2），_leaf_cache 写后不失效（S-7）
- extract_new 并行提取无速率限制（S-9）

**亮点**：
- `attention_filter` 冷启动/非冷启动双策略（余弦 vs softmax attention）
- `temporal_filter` 时态感知降权，避免误杀
- `multi_hop_recall` 三路策略（subject/entity/edge）+ 向量桥接兜底
- `extract_new` 动态切分策略（small/medium/large/xl）+ Jaccard 去重

### 3.3 能力飞轮

| 组件 | 文件 | 核心功能 | 质量评分 |
|------|------|----------|----------|
| SkillOpt Runner | `skillopt_runner.py` | 会话采集→技能排序→SkillOpt-Sleep 优化 | ★★★★☆ |
| Revision | `operators/revision.py` | 失败轨迹分析→多层反思→替代方案生成 | ★★★★☆ |
| Recombination | `operators/recombination.py` | 跨轨迹组件提取→冲突检测→最优合成 | ★★★★☆ |
| Refinement | `operators/refinement.py` | 风险扫描→冗余检测→迭代优化 | ★★★★☆ |

**核心问题**：
- `_detect_feedback` 关键词匹配可能误报（S-11）
- LLM prompt 硬编码（S-10）

**亮点**：
- SkillOpt Runner 的双格式兼容（jsonl + session_*.json）+ state.db 采集
- 负反馈累积 + 3 次阈值触发优化的设计合理
- Revision 的三层反思（direct → root → deep）结构清晰
- Recombination 的 Jaccard 双阈值（low/high）边界判断 + LLM 兜底精排

### 3.4 Cron 编排层

| 组件 | 文件 | 核心功能 | 质量评分 |
|------|------|----------|----------|
| Common | `cron_common.sh` | flock/日志/飞书通知/状态跟踪公共库 | ★★★★★ |
| Boot Detect | `cron-boot-detect.sh` | 开机检测停机期间遗漏 tick | ★★★★☆ |
| Catchup Repair | `cron-catchup-repair.sh` | 修复后追赶重跑 | ★★★☆☆ |
| Periodic Detect | `cron-periodic-detect.sh` | 周期检测失败 job + 恢复通知 | ★★★★☆ |
| Clustering | `clustering-analysis-v3/scripts/` | 记忆维护 5 步管线 + 基线反馈 | ★★★★☆ |
| Router Health | `kn-router-health-check.sh` | Router JSON 解析/召回率/模型稳定性巡检 | ★★★★☆ |

**核心问题**：
- `cron-catchup-repair.sh` 的 `local` 关键字误用（B-3）
- 去重状态文件在 /tmp（S-8）
- heredoc bash 变量展开风险（N-4）

**亮点**：
- `cron_common.sh` 的 `cron_run_step_retry` 指数退避重试设计完善
- `cron-boot-detect.sh` 的三态分类（caught_up / fast_forwarded / failed_exhausted）
- 聚类分析管线的 `CONFIRM_APPLY` 安全门控
- Router 健康巡检的"无异常静默退出"策略减少告警噪声

---

## 四、建议优先级

| 优先级 | 问题编号 | 描述 | 预计工作量 | 状态 |
|--------|----------|------|-----------|------|
| P0 | B-1 | PG 连接缓存线程安全 | 2-4h | ✅ 已修复 |
| P0 | B-2 | skill_matcher 缓存无上限 | 1h | ✅ 已修复 |
| P0 | B-3 | cron-catchup-repair.sh `local` 误用 | 10min | ✅ 已修复 |
| P1 | S-1 | pre_llm_call 函数拆分 | 4-6h | 🟡 部分（已抽 _pass_gates / _get_router_mask） |
| P1 | S-2 | public_api 连接池 | 2-3h | ✅ 已修复（thread-local adapter 池 + TTL） |
| P1 | S-4 | ThreadPoolExecutor 复用 | 1-2h | ✅ 已修复（模块级 _recall_executor） |
| P1 | S-7 | _leaf_cache 写后失效 | 30min | ✅ 已修复（batch_write 后调用 _reset_leaf_cache） |
| P1 | S-9 | extract_new 速率限制 | 1-2h | ✅ 已修复（threading.Semaphore(6)） |
| P2 | S-3 | _extract_keywords 统一 | 1-2h | ✅ 已修复（新增 core/text_utils.py） |
| P2 | S-5 | locate_best_subject N+1 | 2-3h | ✅ 已修复（domain centroid 缓存 TTL 300s） |
| P2 | S-6 | MMR 复杂度优化 | 2-3h | ✅ 已修复（_MMR_MAX_CANDIDATES=20） |
| P2 | S-8 | 去重状态文件路径 | 10min | ✅ 已修复（迁移到 ${HERMES_HOME}/lib/cron-state/） |
| P2 | S-10 | LLM prompt 外部化 | 2-3h | ✅ 已修复（prompts.yaml + prompt_loader） |
| P2 | S-11 | _detect_feedback 误报 | 1-2h | ✅ 已修复（词边界 + 否定前缀检测） |
| P3 | N-1 | hooks.py 函数内 import | 10-30min | ✅ 已修复（psycopg2/requests 提升为顶层） |
| P3 | N-2 | recall.py 函数内 import html | 10-30min | ✅ 已修复（提升为顶层） |
| P3 | N-3 | debug 日志缺结构化字段 | 10-30min | ✅ 已修复（recall.py 三处补齐 extra） |
| P3 | N-4 | cron heredoc 变量展开风险 | 10-30min | ✅ 已修复（cron-boot-detect.sh 改 &lt;&lt;'PY' + 环境变量传参） |
| P3 | N-5 | 类型注解风格不统一 | 10-30min | ✅ 已修复（skillopt_runner 全量 dict/list/tuple/&#124;None） |

---

## 五、测试覆盖度评估

| 模块 | 单元测试 | 集成测试 | 评价 |
|------|----------|----------|------|
| Router | ✅ test_router.py | ❌ | 缺少 401 重试的集成测试 |
| Hooks | ❌ | ❌ | pre_llm_call 过长，难以单元测试（S-1 拆分后可改善） |
| Skill Matcher | ✅ test_skill_matcher.py | ❌ | 关键词/Embedding/LLM 三级各有测试 |
| Filtering | ✅ test_filtering.py | ❌ | MMR/跨域去重/Token 预算覆盖较好 |
| Use Log | ✅ | ❌ | 批量写入/定时刷盘/线程安全有测试 |
| Circuit Breaker | ✅ | ❌ | 熔断/恢复/飞书通知有测试 |
| Knowledge Tree | ✅ test_recall.py | ❌ | attention_filter/temporal_filter 有测试 |
| SkillOpt Runner | ❌ | ❌ | **缺少测试**，rank_skills/optimize 逻辑复杂但无单元测试 |
| Self-Evolving | ✅ test_operators.py | ❌ | 三大算子有基础测试 |
| Cron Scripts | ❌ | ❌ | Shell 脚本无测试，依赖手动验证 |

**关键缺口**：
1. `pre_llm_call` 端到端集成测试缺失
2. SkillOpt Runner 的 `rank_skills` / `harvest_hermes_sessions` 无测试
3. Cron 脚本无自动化测试

---

## 六、总结

飞轮系统的三大闭环架构设计成熟，Feature Flag 体系完善，降级链路完整。核心问题集中在：

1. **线程安全**（B-1: PG 连接共享）— 高并发下可能导致数据错误
2. **内存管理**（B-2: 缓存无上限）— 长驻进程内存泄漏
3. **可维护性**（S-1: 函数过长）— 500 行函数极难维护和测试

建议按 P0 → P1 → P2 顺序逐步修复。P0 的三个问题预计 4-6 小时即可完成，能显著提升系统稳定性和安全性。
