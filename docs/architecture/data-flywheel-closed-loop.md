# Hermes 数据飞轮闭环技术文档

> 本文档详细描述知识导航 4 路召回与飞轮健康巡检如何形成完整的数据飞轮闭环，
> 包括在线召回、离线评估、参数自优化、半自动生效的全链路实现。
>
> 相关文档：[飞轮概览](flywheel-overview.md) · [飞轮蓝图](flywheel-blueprint.md) · [Auto-Tuner Spec](../plans/auto-tuner-spec.md)

---

## 1. 闭环全景

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          在线阶段（每次 LLM 调用）                        │
│                                                                         │
│  用户消息                                                                │
│    │                                                                    │
│    ▼                                                                    │
│  pre_llm_call hook                                                      │
│    ├─ 三层门控（platform / system_prompt / turn_gate）                    │
│    ├─ LLM Router → 4 路 mask {h, kt, s, sag}                            │
│    ├─ 4 路并行召回                                                       │
│    │   ├─ Hindsight（经验记忆：bge-m3 + BM25 + RRF）                     │
│    │   ├─ 知识树 KT（结构化知识 + 实体多跳展开）                           │
│    │   ├─ Skill（三级筛选：关键词 → Embedding → LLM 精排）                │
│    │   └─ SAG（合成记忆搜索，独立熔断器）                                  │
│    ├─ 跨域去重 + 实际 token 消耗观测（预算截断已移除）                      │
│    ├─ XML 组装 → 注入 LLM 上下文                                         │
│    └─ 结构化日志 → trace.log                                            │
│                                                                         │
│  LLM 生成响应                                                            │
│    │                                                                    │
│    ▼                                                                    │
│  post_llm_call hook（knowledge-tree-plugin）                             │
│    └─ 知识提取 → 异步入队 → 知识树增量更新                                 │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               │ trace.log 累积（JSONL，50MB 轮转 × 3）
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        离线阶段（每日 08:00 cronjob）                      │
│                                                                         │
│  flywheel-health-report.sh                                              │
│    │                                                                    │
│    ├─ 阶段 0：Runner 登记（声明内部任务，避免重复执行）                      │
│    │                                                                    │
│    ├─ 阶段 1：报告生成                                                   │
│    │   ├─ 解析 trace.log → 11 个反馈键                                   │
│    │   ├─ KN LLM Judge（采样 200 条，LLM 评分）                          │
│    │   ├─ 聚合 12 个 analyzer 结果                                      │
│    │   ├─ 写入 daily-summary-history.jsonl（保留 30 天）                  │
│    │   └─ 飞书通知 P0/P1 issue                                          │
│    │                                                                    │
│    └─ 阶段 2：Auto-Tuner 参数自优化                                      │
│        ├─ handle_pending_restart()：验证上次调优是否生效                   │
│        ├─ select_param_to_tune()：选参（4 桶优先级）                      │
│        ├─ determine_direction()：方向决策（改善/恶化/未知）                 │
│        ├─ write_env_param()：写入 .env                                   │
│        ├─ normalize_ratio_trio()：三比例归一化（SUM=1.0）                 │
│        └─ 飞书通知 → 人工确认重启                                         │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               │ 人工 systemctl restart hermes-gateway
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          生效阶段                                        │
│                                                                         │
│  systemd EnvironmentFile 注入 .env                                      │
│    → KnowledgeNavigationConfig.from_env() 读取新参数                     │
│    → 4 路召回行为改变                                                    │
│    → trace.log 记录新数据                                                │
│    → 次日 08:00 再次评估 → 闭环                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**闭环周期**：24 小时（每日 08:00 触发）

**半自动环节**：仅 gateway 重启需人工确认（安全机制，防止 cronjob 被杀）

---

## 2. 在线召回层

### 2.1 入口：pre_llm_call

**文件**：`plugins/knowledge-navigation/src/knowledge_navigation/core/hooks/router.py` L1013

```python
def pre_llm_call(session_id: str, user_message: str, **kwargs: Any) -> str | None:
    """每次 LLM 调用前自动执行：三层门控 → LLM Router → 四路 mask 条件执行 → 后处理注入。"""
```

**执行流程**（L1013-1139）：

| 步骤 | 函数 | 行号 | 说明 |
|------|------|------|------|
| 1 | `_pass_gates` | L411-438 | 三层门控：platform 检查 → system_prompt 检查 → turn_gate 操作型消息过滤 |
| 2 | 熔断器检查 | L1024-1027 | 熔断器开启时跳过召回 |
| 3 | `_get_router_mask` | L451-468 | 调 LLM Router 获取 4 路 mask `{h, kt, s, sag}` |
| 4 | `_execute_recall` | L497-518 | 按 mask 并行/串行执行 4 路召回 |
| 5 | `_expand_multi_hop` | L730-738 | 知识树实体多跳展开 |
| 6 | `_post_process_recall` | L883-916 | 降级 + 过滤 + boost + 因果链 + 压缩 + 跨域去重 |
| 7 | SAG 合并 | L1085-1122 | SAG 候选合并到主结果集 |
| 8 | `_dedup_and_measure` | L725-790 | Turn-to-turn 去重 + 文本去重 + 实际 token 消耗观测 |
| 9 | `_assemble_xml_output` | L753-881 | 组装 XML 标签化上下文 + 记录日志 |

### 2.2 四路召回详解

#### Route 1：Hindsight（经验记忆）

**函数**：`_do_hindsight_recall` — [router.py L250-261](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/hooks/router.py#L250-L261)

```python
def _do_hindsight_recall(query: str) -> dict | None:
    client = HindsightClient(CONFIG.hindsight_api_url, CONFIG.timeout_seconds)
    try:
        return client.recall(query, max_results=CONFIG.max_results * 3)
    finally:
        client.close()
```

- **数据源**：Hindsight 服务（`http://localhost:9177`）
- **检索方式**：bge-m3 语义检索 + BM25 关键词 + RRF 融合
- **候选量**：`max_results × 3`（3 倍候选用于后续过滤）
- **关键参数**：`KN_MAX_RESULTS`、`KN_MIN_SCORE`、`KN_TEMPORAL_HALFLIFE`

#### Route 2：知识树 KT（结构化知识）

**函数**：`_do_kt_recall` — [router.py L264-277](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/hooks/router.py#L264-L277)

```python
def _do_kt_recall(session_id: str, query: str) -> list[dict]:
    if not HAS_KNOWLEDGE_TREE:
        _ensure_kt_imported()
    if not HAS_KNOWLEDGE_TREE:
        return []
    try:
        return _recall_knowledge_tree_raw(session_id, query)
    except Exception as e:
        logger.warning("知识树 recall 异常（跳过）", ...)
        return []
```

- **数据源**：knowledge-tree-plugin（`recall_from_tree_raw`）
- **检索方式**：向量检索 + 实体多跳展开
- **容错**：懒加载模块，异常时降级为空列表
- **关键参数**：`KN_MIN_SCORE`、`KN_MAX_RESULTS`

#### Route 3：Skill（三级混合筛选）

**函数**：`_do_skill_match` — [router.py L280-318](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/hooks/router.py#L280-L318)

```python
def _do_skill_match(query: str) -> str:
    from knowledge_navigation.core.skill_matcher import match_skills, strip_frontmatter
    matched = match_skills(query)
    if not matched:
        return ""
    # 逐个读取 skill 文件，strip frontmatter，超 4000 字符截断
    ...
```

- **数据源**：`~/.hermes/skills/` 目录下的 skill 文件
- **三级筛选流程**：
  1. **关键词预筛**（`KN_SKILL_KEYWORD_PRESCREEN`）：top-30 候选
  2. **Embedding 预筛**（`KN_SKILL_EMBEDDING_PRESCREEN`）：bge-m3 向量 top-20
  3. **LLM 精排**：DeepSeek 对候选集打分，返回 top-K
- **关键参数**：`KN_SKILL_EMBEDDING_TOP_K`、`KN_SKILL_KEYWORD_PRESCREEN`

#### Route 4：SAG（合成记忆搜索）

**函数**：`_do_sag_recall` — [router.py L321-360](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/hooks/router.py#L321-L360)

```python
def _do_sag_recall(query: str) -> list[dict]:
    if sag_circuit_is_open():
        return []
    payload = {"query": query, "topK": CONFIG.sag_search_top_k,
               "searchMode": "fast", "sourceIds": source_ids}
    resp = _req.post(f"{CONFIG.sag_api_url}/search", json=payload, timeout=...)
    ...
```

- **数据源**：SAG 服务（`http://127.0.0.1:4173/search`）
- **独立熔断器**：连续失败时自动熔断，跳过 SAG 召回
- **关键参数**：`KN_SAG_SEARCH_TOP_K`、`KN_SAG_MAX_INJECT`、`KN_SAG_MIN_SCORE`

### 2.3 跨域去重与实际消耗观测

**函数**：`_dedup_and_measure` — [router.py L725-790](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/hooks/router.py#L725-L790)

两步处理：

1. **Turn-to-turn 去重**（L634-663）：从 `_injected_ids[session_id]` 读取已注入历史，`demote` 模式降权 ×0.1 后重排，`remove` 模式直接删除

2. **文本去重**（L665-666）：`dedup_by_text(kept)` 字符 n-gram Jaccard 相似度去重

> ⚠️ **Token 预算截断已于 2026-08-10 移除**（决策详见 [data-flywheel-system-map.md §4.8](data-flywheel-system-map.md)）。原 `_dedup_and_budget` 的 `apply_token_budget` 三比例截断逻辑整体删除，`filtering.py` 相关死代码已清理。当前仅做**实际 token 消耗观测**：`_dedup_and_measure` 在合并后无条件输出 `token_usage` 事件（含 `total_tokens` 字段），用于离线趋势分析，不再做任何注入前截断。

### 2.4 XML 输出组装

**函数**：`_assemble_xml_output` — [router.py L753-881](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/hooks/router.py#L753-L881)

按 source 分桶拼装 XML 标签：

```xml
<user_query>用户消息</user_query>
<recalled_memory source="hindsight" count="3" score_avg="0.72">
  ...记忆条目...
</recalled_memory>
<knowledge source="knowledge_tree" count="2">
  ...知识点...
</knowledge>
<knowledge source="sag" count="1">
  ...SAG 段落...
</knowledge>
<auto_loaded_skills>
  ...skill 内容...
</auto_loaded_skills>
<system_state>
  pwd: /root
  time: 2026-08-09T08:00:00+08:00
</system_state>
```

### 2.5 知识沉淀：post_llm_call

**文件**：`plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/hooks.py` L291-354

```python
def post_llm_call(session_id, user_message, assistant_response, **kwargs):
    # 三层门控（与 pre_llm_call 同构）
    if _skip_non_user(...): return
    if _skip_system_prompt(...): return
    if _skip_post_llm_call_fn(assistant_response): return
    # cheap gate 廉价预筛
    if should_skip_extraction(user_message, assistant_response): return
    # 异步入队
    task = ExtractTask(session_id, user_message, assistant_response)
    _task_queue.put_nowait(task)
```

- **只写入知识树**，不写入 Hindsight（Hindsight 写入由外部服务自身管理）
- **异步执行**：非阻塞入队后立即返回，后台 worker 消费
- **门控规则**：响应 <80 字符 / 纯工具输出 / 确认型首行 → 跳过

---

## 3. 数据采集层：trace.log

### 3.1 日志系统架构

**配置文件**：`plugins/knowledge-navigation/src/knowledge_navigation/config.py`

| 组件 | 行号 | 说明 |
|------|------|------|
| `JSONFormatter` | L11-52 | LogRecord → JSON（包含 extra 字段） |
| `TraceRecordFilter` | L80-84 | 过滤 pytest/mock 测试记录 |
| `setup_logging()` | L422-460 | RotatingFileHandler，50MB 轮转 × 3 备份 |

**日志路径**：`~/.hermes/plugins/knowledge-navigation/trace.log`（JSONL 格式）

### 3.2 结构化事件清单

| event 名 | 写入位置 | 用途 |
|----------|---------|------|
| `router_mask` | router.py L464-468 | Router 四路 mask 决策结果 |
| `recall_success` | router.py L847-874 | 召回成功，含 score_stats / kept_results / 各路数量 |
| `recall_empty` | router.py L902 | Hindsight + KT 都无结果 |
| `recall_empty_results` | router.py L916 | 全域无结果 |
| `hindsight_fail_kt_fallback` | router.py L904 | Hindsight 无结果，KT 降级 |
| `recall_error` | router.py L518, L588 | 召回异常 |
| `recall_timeout` | router.py L514 | Hindsight 超时 |
| `multi_hop_expand` | router.py L738 | 多跳展开条数 |
| `token_usage` | router.py L786 | 实际 token 消耗（total_tokens 等，仅观测） |
| `sag_recall` | router.py L351-354 | SAG recall 成功条数 |
| `sag_merge` | router.py L1122 | SAG 候选合并条数 |
| `skill_match` | router.py L310-314 | Skill 匹配命中列表 |
| `eval_query_bypass` | router.py L411-416 | eval query 绕过门控 |
| `skip_router_all_off` | router.py L1032 | Router 全关闭 |

### 3.3 关键日志字段（recall_success）

```json
{
  "timestamp": "2026-08-09T08:00:00+08:00",
  "session_id": "abc123",
  "event": "recall_success",
  "query_trunc": "用户查询前 N 字符",
  "total_results": 12,
  "kept_results": 5,
  "dropped_results": 7,
  "score_stats": {"min": 0.35, "max": 0.92, "mean": 0.65, "std": 0.15},
  "hs_kept": 2,
  "kt_kept": 2,
  "sag_kept": 1,
  "latency_ms": 1200,
  "injected_count": 8,
  "total_chars": 3500
}
```

---

## 4. 离线评估层：飞轮健康巡检

### 4.1 执行入口

**文件**：`scripts/flywheel-health-report/scripts/flywheel-health-report.sh`

| 行号 | 阶段 | 命令 |
|------|------|------|
| L13-21 | 初始化 | `source cron_common.sh` + `cron_init` |
| L25-32 | 环境变量 | `HERMES_HOME` / `PYTHONPATH=PROJECT_DIR/src` |
| L37-45 | 阶段 0 | `python3 -m flywheel_health_report.runner` |
| L53-67 | 阶段 1 | `python3 -m flywheel_health_report.cli` |
| L69-109 | 飞书通知 | awk 提取 P0/P1 → `cron_notify` + `lark-cli` |
| L111-124 | 阶段 2 | `bash auto-tuner.sh` |

**触发时间**：每日 08:00（cron）

### 4.2 Runner 前置阶段

**文件**：`scripts/flywheel-health-report/src/flywheel_health_report/runner.py`

`run_all(home, dry_run=False)` L91-101：

- **零耗时声明**：不执行外部任务，只写 `runner-summary.json`
- 声明 KN Judge 由阶段 1 的 `report.py:run_judge_within_window()` 内部执行
- 替代原 `knowledge-navigation-baseline` cronjob，避免重复跑 LLM

### 4.3 报告生成

**文件**：`scripts/flywheel-health-report/src/flywheel_health_report/report.py`

**`generate_report(home, dry_run=False)` L86-630**：

**数据窗口**（L92-94）：报告在 CN 08:00（UTC 00:00）生成，数据窗口 = UTC 昨天 + 前天（2 天滚动）

**12 个 Analyzer 聚合**（L105-120）：

| Analyzer | 分析对象 | 关键反馈键 |
|----------|---------|-----------|
| `analyze_cron_jobs` | cron 任务状态 | cron_error_count |
| `analyze_router` | trace.log router_mask + recall | router_empty_pct, sag_total_kept |
| `analyze_skill_eval` | skill 评估基线 | skill_eval_score |
| `analyze_skill_usage` | `.usage.json` | skill_used_count |
| `analyze_token_usage` | trace.log token_usage | token_usage_total |
| `analyze_sag_contribution` | trace.log sag_merge | sag_contribution_rate |
| `analyze_global_errors` | error.log | error_count |
| `analyze_kt_baseline` | KT 基线 | kt_node_count |
| `analyze_clustering` | 聚类报告 | cluster_count |
| `analyze_kn_baseline` | KN 基线 | kn_recall_rate |
| `analyze_memory_cleanup` | 清理报告 | memory_hindsight_count |

### 4.4 KN LLM Judge

**文件**：`scripts/flywheel-health-report/src/flywheel_health_report/analyzers/kn_judge.py`

**`run_judge_within_window(home, since_iso, until_iso)` L113-221**：

1. **采样**（L149-173）：从 trace.log 提取窗口内 `recall_success` 记录，取最近 200 条
2. **LLM 并发评分**（L177-197）：`ThreadPoolExecutor` 并发调用 `_judge_one`，硬超时 3600s
3. **三键计算**（L210-221）：

```python
avg_rel = sum(scores) / len(scores)
rel_rate = sum(1 for s in scores if s >= 0.5) / len(scores)
ci = _bootstrap_ci(scores)
return {
    "kn_judge_sample_count": judged,
    "kn_judge_relevant_rate": round(rel_rate, 4),
    "kn_judge_avg_relevance": round(avg_rel, 4),
    "kn_judge_ci_lo": round(ci[1], 4),
    "kn_judge_ci_hi": round(ci[2], 4),
}
```

- **相关率阈值**：LLM 评分 ≥0.5 视为相关
- **Bootstrap CI**：对评分做 bootstrap 置信区间估计
- **兜底机制**（L229-266）：judged < 25 时用 `avg_score` 粗估，标记 `kn_judge_fallback=True`

### 4.5 daily-summary-history.jsonl

**写入函数**：`parsers.py` L55-66 `append_daily_summary`

```python
def append_daily_summary(data_flywheel: Path, summary: dict) -> None:
    path = data_flywheel / "daily-summary-history.jsonl"
    records = _load_jsonl(path)
    records = [r for r in records if r.get("date") != summary.get("date")]  # 按 date 去重
    records.append(summary)
    records = records[-30:]  # 保留最近 30 天
    # 原子写入
```

**路径**：`~/.hermes/data/flywheel/daily-summary-history.jsonl`

**关键字段**：date, report_type, p0_count, p1_count, router_empty_pct, sag_total_kept, memory_hindsight_count, skill_used_count, kn_judge_sample_count, kn_judge_relevant_rate, kn_judge_avg_relevance, kn_judge_fallback

---

## 5. 参数自优化层：Auto-Tuner

### 5.1 核心文件

**文件**：`scripts/flywheel-health-report/src/flywheel_health_report/auto_tuner/tuner.py`

### 5.2 15 个可调参数

| 参数 | 反馈键 | 方向 | 类型 |
|------|--------|------|------|
| `KN_MIN_SCORE` | router_empty_pct, kn_judge_relevant_rate | down_better | float |
| `KN_MAX_RESULTS` | router_empty_pct, kn_judge_relevant_rate | up_better | int |
| `KN_TEMPORAL_HALFLIFE` | kn_judge_avg_relevance | up_better | int |
| `KN_TEMPORAL_WEIGHT` | kn_judge_avg_relevance | up_better | float |
| `KN_LAMBDA_MRR` | kn_judge_avg_relevance | up_better | float |
| `KN_SCORE_SPAN_TOP3_THRESHOLD` | router_empty_pct | down_better | float |
| `KN_SCORE_SPAN_HALF_THRESHOLD` | router_empty_pct | down_better | float |
| `KN_SAG_MAX_INJECT` | sag_total_kept | up_better | int |
| `KN_SAG_SEARCH_TOP_K` | sag_total_kept | up_better | int |
| `KN_SAG_MIN_SCORE` | sag_total_kept | down_better | float |
| `KN_TURN_TO_TURN_MODE` | kn_judge_avg_relevance | stable_ok | str |
| `KN_CROSS_DOMAIN_DEDUP_MODE` | kn_judge_avg_relevance | stable_ok | str |
| `KN_CAUSAL_BOOST_ALPHA` | kn_judge_avg_relevance | up_better | float |
| `KN_CAUSAL_BOOST_CAP` | kn_judge_avg_relevance | up_better | float |
| `KN_CIRCUIT_BREAKER_COOLDOWN` | recall_timeout | up_better | int |

### 5.3 调优主流程（main 函数）

```
main()
 ├─ Step 1: handle_pending_restart()     # 验证上次调优是否生效
 │   ├─ verify_restart(ts)               #   对比 gateway 启动时间 vs 调优时间
 │   ├─ True → 提取今日指标 → 判断改善 → update_state()
 │   └─ False → notify_restart_reminder() # 飞书提醒重启，本次跳过
 │
 ├─ Step 2: 冷却期检查                    # 今日已 applied 则进入 24h 冷却
 │
 ├─ Step 3: select_param_to_tune()       # 选参（4 桶优先级）
 │   ├─ virgin-confident                 # 从未调过 + 反馈可信
 │   ├─ virgin-unconfident               # 从未调过 + 反馈不可信
 │   ├─ remaining-confident              # 调过未收敛 + 可信
 │   └─ remaining-unconfident            # 调过未收敛 + 不可信
 │
 ├─ Step 4: determine_direction()        # 方向决策
 │   ├─ improved=True → 同向
 │   ├─ improved=False → 反向
 │   └─ improved=None → 按位置（离哪个边界近就反向）
 │
 ├─ Step 5: 计算新值（step × direction）
 │
 ├─ Step 6: write_env_param()            # 写入 .env
 │
 ├─ Step 7: normalize_ratio_trio()       # 三比例归一化（若选中比例参数）
 │
 ├─ Step 8: update_state()               # 更新状态机
 │   ├─ 震荡检测（up→down→up → 惩罚）
 │   ├─ 收敛锁定（no_change_count >= 3 → locked）
 │   └─ 连续恶化暂停（consecutive_degradation >= 3 → suspended）
 │
 └─ Step 9: notify_gateway_restart()     # 飞书通知重启
```

### 5.4 verify_restart — L523-546

通过 `systemctl show hermes-gateway --property=ActiveEnterTimestamp` 取 gateway 启动时间，与调优 timestamp 对比：

```python
gw_epoch = datetime.strptime(val, "%a %Y-%m-%d %H:%M:%S %Z").timestamp()
tune_epoch = datetime.fromisoformat(tune_timestamp).timestamp()
return gw_epoch > tune_epoch
```

### 5.5 select_param_to_tune — L997-1060

**4 桶优先级**（R3 P1-D）：

| 优先级 | 桶 | 含义 |
|--------|---|------|
| 1 | virgin-confident | 从未调过 + 今日反馈可信 |
| 2 | virgin-unconfident | 从未调过 + 反馈不可信 |
| 3 | remaining-confident | 调过未收敛 + 可信 |
| 4 | remaining-unconfident | 调过未收敛 + 不可信 |

**反馈可信判定**：
- 参数反馈**不依赖** kn_judge 主观键 → 任何时候可信
- 参数反馈依赖主观键，但今日 `kn_judge_sample_count >= 50` → 可信
- 其他（样本不足 + 依赖主观键）→ 不可信

**永久跳过**（R1 P0-A）：`KN_ENABLE_CAUSAL_CHAIN=false` 时跳过 `KN_CAUSAL_BOOST_ALPHA` 和 `KN_CAUSAL_BOOST_CAP`

### 5.6 determine_direction — L889-980

1. 取上次调优的 `metrics_before` / `metrics_after`
2. 对每个反馈键调 `_is_metric_improved(name, direction, old, new)`：
   - `up_better`: new >= old
   - `down_better`: new <= old
   - `stable_ok`: 变化 <10% 视为改善
3. KN Judge 主观键先过滤（R2 P0-B）：样本 <50 时跳过
4. `improved = improved_count >= max(total_count/2, 1)`（多数改善才算改善）
5. 改善 → 同向；恶化 → 反向；未知 → 按位置

### 5.7 update_state 状态机 — L776-863

| 机制 | 触发条件 | 动作 |
|------|---------|------|
| 震荡检测 | direction_history 出现 up→down→up 或 down→up→down | `osc_punish=2`，清空历史 |
| 收敛锁定 | `no_change_count >= 3` | `locked=True` |
| 连续恶化暂停 | `consecutive_degradation >= 3` | `suspended=True, locked=True`，回滚 initial_value |
| 冷却期 | applied 后 24h 内 | 跳过调优 |

### 5.8 normalize_ratio_trio（已废弃 — 2026-08-10）

> ⚠️ **本节已废弃**：`normalize_ratio_trio` 与 `KN_TOKEN_BUDGET_*_RATIO` 三个比例参数一同于 2026-08-10 随 token 预算移除而下线（详见 [data-flywheel-system-map.md §4.8](data-flywheel-system-map.md)）。当前 Auto-Tuner 的 15 个可调参数中已无比例型参数，无需三比例归一化。原归一化算法保留于此仅供历史参考：

**触发条件（历史）**：选中的参数是 `KN_TOKEN_BUDGET_KT_RATIO` / `SKILL_RATIO` / `HINDSIGHT_RATIO` 之一

**归一化算法（历史）**：

1. clip `tuned_new_value` 到自身 `[pmin, pmax]`
2. 剩余预算 `remain = 1.0 - tuned_value`，按另外两参数当前权重分摊
3. 第一轮 clip：超界的固定到边界，diff 全给未 clip 的那个
4. 兜底：两个都 clip 还凑不齐 1.0，微调 `tuned_param`；极端情况整体缩放
5. 浮点残差修正：残差加到数值最大的参数上，保证 `SUM=1.0`

```python
s = sum(result.values())
residual = round(1.0 - s, 4)
if abs(residual) >= 1e-4:
    max_key = max(result, key=lambda k: result[k])
    result[max_key] = round(result[max_key] + residual, 4)
```

**联动写入（历史）**：三个比例同时写入 `.env`，日志新增 `ratio_trio_normalized` 和 `ratio_trio_old` 字段

### 5.9 飞书通知

**文件**：`scripts/flywheel-health-report/src/flywheel_health_report/auto_tuner/notifier.py`

| 函数 | 行号 | 触发时机 |
|------|------|---------|
| `notify_gateway_restart` | L91-103 | 调参后立即发送 |
| `notify_restart_reminder` | L75-88 | pending_restart 但 gateway 未重启 |

**消息格式**：

```
🔧 Auto-Tuner 需要手动重启网关

参数: KN_MIN_SCORE
旧值: 0.35
新值: 0.32
原因: 上次调优改善指标，同向(down)

操作:
systemctl restart hermes-gateway

验证:
systemctl status hermes-gateway
```

---

## 6. 参数生效层

### 6.1 配置加载链路

```
systemctl restart hermes-gateway
  → systemd EnvironmentFile=-/root/.hermes/.env
    → hermes-gateway 进程启动
      → KnowledgeNavigationConfig.from_env()
        → 读取 ENV 变量（token 预算 5 字段已随 2026-08-10 决策移除）
        → 4 路召回使用新参数
```

### 6.2 from_env 方法

**文件**：`plugins/knowledge-navigation/src/knowledge_navigation/config.py` L290-419

**加载优先级**：`kit-config > .env > 代码默认值`

**ENV 参数**（token 预算 5 字段已移除，详见 config.py，部分关键参数）：

| ENV | 字段 | 默认值 | 说明 |
|-----|------|--------|------|
| `KN_MAX_RESULTS` | max_results | 3 | 每路最大召回条数 |
| `KN_MIN_SCORE` | min_score | 0.35 | 最低分数阈值 |
| `KN_SAG_MAX_INJECT` | sag_max_inject | 3 | SAG 最大注入条数 |
| `KN_ENABLE_CAUSAL_CHAIN` | enable_causal_chain | True | 因果链开关 |
| `KN_SKILL_EMBEDDING_PRESCREEN` | kn_skill_embedding_prescreen | True | Skill Embedding 预筛 |

> 完整 57 参数清单见 [config.py L290-419](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/config.py#L290-L419)

---

## 7. 闭环质量保障

### 7.1 四轮修复成果

| 轮次 | 机制 | 问题 | 解决方案 |
|------|------|------|---------|
| R1 (P0-A) | 死参数跳过 | 因果链关闭时仍调 `KN_CAUSAL_BOOST_*` | `KN_ENABLE_CAUSAL_CHAIN=false` 时永久跳过，不占轮次 |
| R2 (P0-B) | 样本不足哨兵 | Judge 样本 <50 时小样本噪声驱动方向 | 主观键标记 untrusted，全部被过滤时 `improved=None` 保持 pending |
| R3 (P1-D) | 反馈可信度排序 | 不可信反馈的参数与可信反馈的参数同等优先 | 4 桶优先级：virgin-confident → virgin-unconfident → remaining-* |
| R4 (P1-C) | 三比例归一化 | 调一个比例后三者和≠1.0 | 联动调整另外两个，按当前权重分摊剩余预算，浮点残差修正 |

### 7.2 安全机制

| 机制 | 说明 |
|------|------|
| 不自动重启 | Auto-tuner 不自动 `systemctl restart`，飞书通知人工确认 |
| 收敛锁定 | 参数连续 3 次无变化 → `locked=True`，不再调优 |
| 连续恶化暂停 | 连续 3 次恶化 → `suspended=True`，回滚初始值 |
| 震荡惩罚 | up→down→up 模式 → `osc_punish=2`，清空方向历史 |
| 冷却期 | applied 后 24h 内不再调优（等待效果验证） |
| 回滚机制 | `deploy.sh rollback` 可回滚到任意备份版本 |

---

## 8. 部署架构

### 8.1 Deploy 框架

**入口**：`deploy/deploy.sh`（命令：list / plan / deploy / rollback / history / cleanup）

**飞轮模块配置**：

| 文件 | 说明 |
|------|------|
| `deploy/projects/flywheel-health-report.sh` | 项目脚本：源 `scripts/flywheel-health-report` → 标 `/root/.hermes/scripts/flywheel-health-report` |
| `deploy/manifests/flywheel-health-report.manifest` | 文件清单：`src/**/*.py` + `scripts/**/*.sh` |

**部署命令**：

```bash
# 预览
./deploy/deploy.sh plan flywheel-health-report

# 部署
sudo ./deploy/deploy.sh deploy flywheel-health-report --yes

# 回滚
./deploy/deploy.sh rollback flywheel-health-report <timestamp>

# 历史
./deploy/deploy.sh history flywheel-health-report
```

### 8.2 systemd 服务

```ini
[Service]
EnvironmentFile=-/root/.hermes/.env
ExecStart=/root/.hermes/...
```

- `.env` 文件由 Auto-Tuner 写入参数
- `EnvironmentFile` 在进程启动时注入 ENV
- 重启后 `from_env()` 读取新值

---

## 9. 文件索引

### 知识导航插件

| 文件 | 核心内容 |
|------|---------|
| [router.py](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/hooks/router.py) | 4 路召回 + pre_llm_call + 去重 + XML 组装 |
| [config.py](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/config.py) | from_env() + setup_logging() + JSONFormatter |
| [filtering.py](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/filtering.py) | 去重/过滤工具（`apply_token_budget` 已随预算移除而删除） |
| [skill_matcher.py](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/skill_matcher.py) | Skill 三级筛选 |
| [turn_gate.py](file:///d:/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/turn_gate.py) | 门控规则 |

### 知识树插件

| 文件 | 核心内容 |
|------|---------|
| [hooks.py](file:///d:/HermesProject/plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/hooks.py) | post_llm_call 知识提取 |

### 飞轮健康巡检

| 文件 | 核心内容 |
|------|---------|
| [flywheel-health-report.sh](file:///d:/HermesProject/scripts/flywheel-health-report/scripts/flywheel-health-report.sh) | 主脚本 |
| [runner.py](file:///d:/HermesProject/scripts/flywheel-health-report/src/flywheel_health_report/runner.py) | 阶段 0 Runner |
| [report.py](file:///d:/HermesProject/scripts/flywheel-health-report/src/flywheel_health_report/report.py) | 报告生成 |
| [parsers.py](file:///d:/HermesProject/scripts/flywheel-health-report/src/flywheel_health_report/parsers.py) | trace.log 解析 |
| [config.py](file:///d:/HermesProject/scripts/flywheel-health-report/src/flywheel_health_report/config.py) | 路径常量 |
| [kn_judge.py](file:///d:/HermesProject/scripts/flywheel-health-report/src/flywheel_health_report/analyzers/kn_judge.py) | KN LLM Judge |
| [router.py](file:///d:/HermesProject/scripts/flywheel-health-report/src/flywheel_health_report/analyzers/router.py) | Router 分析器 |
| [skill.py](file:///d:/HermesProject/scripts/flywheel-health-report/src/flywheel_health_report/analyzers/skill.py) | Skill 分析器 |
| [memory_cleanup.py](file:///d:/HermesProject/scripts/flywheel-health-report/src/flywheel_health_report/analyzers/memory_cleanup.py) | 记忆清理分析器 |

### Auto-Tuner

| 文件 | 核心内容 |
|------|---------|
| [tuner.py](file:///d:/HermesProject/scripts/flywheel-health-report/src/flywheel_health_report/auto_tuner/tuner.py) | 调优核心逻辑 |
| [notifier.py](file:///d:/HermesProject/scripts/flywheel-health-report/src/flywheel_health_report/auto_tuner/notifier.py) | 飞书通知 |

### Deploy 框架

| 文件 | 核心内容 |
|------|---------|
| [deploy.sh](file:///d:/HermesProject/deploy/deploy.sh) | 部署入口 |
| [flywheel-health-report.sh](file:///d:/HermesProject/deploy/projects/flywheel-health-report.sh) | 项目配置 |
| [flywheel-health-report.manifest](file:///d:/HermesProject/deploy/manifests/flywheel-health-report.manifest) | 文件清单 |

---

## 10. 闭环验收 Checklist

### 10.1 每日自动验收（08:00 cronjob 触发后）

| 检查项 | 位置 | 预期 |
|--------|------|------|
| Runner 登记 | cron 日志 | `Runner 登记 OK` |
| runner-summary.json | `~/.hermes/data/flywheel/runner-summary.json` | mtime = 08:00:xx |
| 报告生成 | 飞书群 | P0/P1 通知消息 |
| daily-summary | `~/.hermes/data/flywheel/daily-summary-history.jsonl` | 新增 1 条，date=昨天 |
| Auto-Tuner 日志 | `~/.hermes/data/flywheel/auto-tuner-log.jsonl` | 新增 0-1 条 |

### 10.2 Auto-Tuner 调优验证

| 检查项 | 位置 | 预期 |
|--------|------|------|
| R1 死参数跳过 | auto-tuner-log.jsonl | `KN_ENABLE_CAUSAL_CHAIN=false` 时含 `永久跳过` |
| R2 样本不足 | auto-tuner-log.jsonl | 样本 <50 时含 `忽略反馈 kn_judge_*` |
| R3 可信度排序 | auto-tuner-log.jsonl | 含 `选参[virgin-confident]` |
| R4 三比例归一化 | auto-tuner-log.jsonl | 含 `ratio_trio_normalized`，SUM=1.0 |
| .env 更新 | `~/.hermes/.env` | 调优参数值已更新 |
| 飞书通知 | 飞书群 | `Auto-Tuner 需要手动重启网关` |

### 10.3 生效验证

| 检查项 | 命令 | 预期 |
|--------|------|------|
| Gateway 启动时间 | `systemctl show hermes-gateway --property=ActiveEnterTimestamp` | > 调优 timestamp |
| 进程状态 | `systemctl status hermes-gateway` | active (running), NRestarts=0 |
| 参数生效 | trace.log 新记录 | 召回行为符合新参数 |
| 次日闭环 | auto-tuner-log.jsonl | `status=applied`，含 metrics_after |
