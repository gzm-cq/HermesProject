# Hindsight 升级后知识导航调用方式核查报告

- 生成时间：2026-08-20 13:58 (GMT+8)
- 升级时间：hindsight-api daemon 于 2026-08-20 **13:54** 重启（与 SAG 13:43 同批升级）
- 核查对象：知识导航插件 `knowledge-navigation` 对 Hindsight 服务（`hindsight-api --port 9177`）的 recall 调用
- 方法：代码静态比对（adapter/router/filtering）+ 生产实测（直接 POST 升级后端点）

## 一、调用方（知识导航）

| 层 | 文件 | 作用 |
|----|------|------|
| 适配器 | `adapters/hindsight.py` → `HindsightClient.recall()` | 直接 `POST {hindsight_api_url}`，body=`{query, budget, trace, max_results}`，重试/超时/4xx不熔断/5xx+超时熔断 |
| 路由 | `core/hooks/router.py:_do_hindsight_recall` (251) | 调 `client.recall(query, max_results=CONFIG.max_results*3)`，返回整 dict |
| 结果提取 | `router.py:592` `_hs_results = result.get("results", [])` | 取 `results` 列表 |
| 分数回填 | `router.py:1030-1038` | `rerank_map = extract_rerank_scores(trace)`；按 `node_id` 把 score 回填到 item 的 `score`/`rerank_score` |
| 过滤 | `filtering.py:filter_by_score` (100) | `score = rerank_map.get(node_id, 0.0)`；`if score >= CONFIG.min_score(0.35)` 保留 |

`hindsight_api_url` 生产实际值：`http://localhost:9177/v1/default/banks/hermes/memories/recall`（config.py 默认值；`.env` 无 `KN_HINDSIGHT_URL` 覆盖，kit-config `plugin_config` 为空走 `from_env` 默认值）。已通过生产 trace.log 实证该 URL 被实际调用。

## 二、服务端契约（hindsight-api，升级后实测）

- 端点：`POST /v1/default/banks/hermes/memories/recall` ✓（adapter 直接 POST 整个 base_url）
- 鉴权：**无**（实测无 token 返回 200；adapter 不发 `Authorization`，一致）
- 请求体：`query` / `budget`("low"|"medium"|"high") / `trace`(bool) / `max_results` ✓ 全部匹配
- 响应结构：
  - 顶层：`results`(list) / `trace`(dict) / `entities`(dict)
  - `results[]` 每项：`id` / `text` / `type` / `entities` / `mentioned_at` / `metadata` / `tags` / `scores`(嵌套 `{final, reranker, semantic}`)
  - `trace["reranked"]` 每项：`node_id` / `text` / `rerank_score` / `rerank_rank` / `rrf_rank` / `rank_change` / `score_components`

## 三、逐字段对齐验证（生产实测）

| 环节 | 期望值 | 实测 | 结论 |
|------|--------|------|------|
| 端点可达 | 200 | 200 | ✅ |
| 请求体字段 | query/budget/trace/max_results | 服务端正常消费 | ✅ |
| `results[].id` / `text` | 被消费 | 响应含两字段 | ✅ |
| `results[].score` 顶层 | **无**（嵌套在 `scores`） | 实测确无顶层 score | ⚠️ 但非问题（见下） |
| `trace["reranked"][].node_id` | 与 `results[].id` 对应 | **10/10 匹配 (ratio=1.00)** | ✅ |
| `trace["reranked"][].rerank_score` | 被 `extract_rerank_scores` 读取回填 | 结构完全匹配 | ✅ |
| 鉴权 | 无 | 无 token 200 | ✅ |

**关键说明**：hindsight 升级后，每条记忆的 score **不在 `results[]` 顶层**，而是嵌套在 `results[].scores.{final,reranker,semantic}`；但知识导航并不依赖顶层 score——它通过 `trace["reranked"][].{node_id, rerank_score}` 回填（见 `router.py:1030-1038`）。实测 `reranked.node_id` 与 `results.id` **100% 匹配**，回填链路完全打通，`min_score` 过滤与排序按预期工作。

## 四、结论

**Hindsight 调用方式正确，升级后契约完全对齐，无需任何代码改动。**

错误分类（adapter 设计）也与系统一致：4xx（如 query 超长被拒）不触发熔断；5xx/超时计入熔断。健康报告中 `plugins.memory.hindsight(8)` 错误来自 memory-cleanup 链路的 `llm_guard`，与 recall 调用无关。

## 五、监控建议（非错误）

1. **rerank_score 量级 vs `min_score=0.35`**：本次弱相关查询 rerank_score≈0.014（远低于阈值），但强相关记忆（见 08-11 trace，score 0.39–0.77）可正常越阈注入。建议持续观察真实对话的 `score_stats`，若普遍 < 0.35 则需下调 `CONFIG.min_score`（属阈值调优，非调用问题）。
2. **无鉴权**：hindsight-api 仅本地 `127.0.0.1:9177` 监听，依赖网络隔离。若未来暴露需加 Bearer（与 SAG 对齐）。
3. **外部守护进程**：hindsight-api 来自 `/opt/hindsight/venv`（独立 venv），其 embedding/reranker 模型由 `.env` 的 `HINDSIGHT_API_*` 配置；升级若改这些键需同步。
4. **冗余字段**：`results[].scores` 嵌套未被知识导航消费（用 `trace.reranked` 代替），无害。

## 六、与 SAG 核查对比

| 维度 | SAG | Hindsight |
|------|-----|-----------|
| 协议 | REST `/api/v1/search` + Bearer | REST `/v1/.../recall` 无鉴权 |
| 结果取字段 | `sections[]` | `results[]` + `trace.reranked[]` |
| 分数来源 | `sections[].score` 顶层 | `trace.reranked[].rerank_score`（回填） |
| 升级后状态 | ✅ 正确 | ✅ 正确 |
