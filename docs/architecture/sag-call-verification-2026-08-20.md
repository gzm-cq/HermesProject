# 知识导航 SAG 调用方式核查报告（2026-08-20）

> 背景：SAG 服务今日 13:43 升级重启（`/root/SAG/apps/api`，postgres `sag_lite_v2`）。
> 任务：核查知识导航插件（knowledge-navigation）调用 SAG 的方式是否与升级后契约一致。

## 结论：✅ 调用方式正确，无需改动

知识导航的 SAG 召回（`core/hooks/router.py:_do_sag_recall`，第 4 路 recall）与升级后的 SAG v1.x REST 契约**完全对齐**。

## 逐项契约核对

| 维度 | SAG 服务端契约 | 知识导航调用实现 | 判定 |
|------|----------------|------------------|------|
| 路径 | `POST /api/v1/search`（`api_router` prefix `/api/v1` + `global_router` prefix `/search`） | `{sag_api_url}{sag_api_search_path}` = `http://127.0.0.1:4173/api/v1/search` | ✅ |
| 方法 | POST | POST | ✅ |
| 鉴权 | `get_current_user` 强制 Bearer，无 token → 401 `缺少认证令牌` | `CONFIG.sag_auth_token` → `Authorization: Bearer ...`（来自 `KN_SAG_AUTH_TOKEN` env 或 `~/.hermes/.sag_token` 兜底） | ✅ |
| 请求体 `query` | 必填 str(1–4000) | 传入（经 `_truncate_recall_query` 截断） | ✅ |
| 请求体 `top_k` | int(1–50) | `CONFIG.sag_search_top_k` 默认 3 | ✅ |
| 请求体 `strategy` | 枚举 `vector`/`multi`/`multi_es_fast` | `"vector"`（合法） | ✅ |
| 请求体 `source_ids` | list[str] \| None | `["89a9a04d..."]`（实测该 source 存在且有命中） | ✅ |
| 响应 `sections` | list[SectionOut]：`chunk_id,heading,content,score,rank,source_id,source_name` | `data.get("sections", [])`，下游映射 `content/heading/score/chunk_id/source_id` | ✅ |
| 错误处理 | 4xx 不熔断 / 5xx 熔断 | 4xx→仅告警；5xx→`sag_circuit_record_failure` | ✅ |
| 超时 | p95 ≈ 21–24s | `CONFIG.sag_search_timeout` 默认 30s | ✅ |

## 实测证据（生产现场）

- **无 token**：`HTTP 401 {"error":{"code":"unauthorized","message":"缺少认证令牌"}}` → 服务端鉴权强制生效。
- **带 token（`.sag_token` 兜底文件）**：`HTTP 200`，返回 `sections` 含 3 条，真实命中文档 `AI平台预算细化表_垂域增强版`（score 0.719），字段完整。

## 配置加载路径澄清（避免误判）

`CONFIG` 主加载顺序（`config.py:481-485`）：优先 `from_kit_config()`，返回 None 则回退 `from_env()`。

- `/root/.hermes-kit/config.yaml` 存在，但其 `plugin_config` 段为空 → `from_kit_config()` 返回 None → **实际走 `from_env()`**。
- `from_env()`（`config.py:419`）读取 `KN_SAG_AUTH_TOKEN`，并兜底读 `~/.hermes/.sag_token` 文件。该文件存在且 token 有效 → Bearer 正确注入。
- ⚠️ 注意：若未来改成真正依赖 `from_kit_config` 路径，其 field_map（`line 249`）读的是无前缀键 `sag_auth_token`，与 `.env` 的 `KN_SAG_AUTH_TOKEN` 不一致 → 会丢 token。当前无影响。

## 设计行为确认（非 bug）

- SAG 召回采用**指针策略**：当 section `content` 长度 > `sag_pointer_threshold` 时，只注入 heading+预览，引导 LLM 用 `sag_search` 工具按需取全文（全量 content 经 SAG 自身 MCP server 拉取，不在 recall 内联）。属既定架构。
- 完整 content 的按需拉取由 `mcp_servers.sag`（SAG 自带 MCP `/mcp/`）承接，升级前后工具名一致。

## 可留意项（非错误，建议监控）

1. **`document_id` 恒为空**：SAG `SectionOut` 无 `document_id` 字段，候选 `document_id` 始终取默认 `""`。当前靠 `heading` 重新查询，无功能影响。
2. **`source_ids` 单一硬编码**：默认仅 `89a9a04d...` 一个信源。今日实测有效；若 SAG 升级后信源集合变动导致该 ID 失效，召回会**静默返回空**（非报错）。建议升级 SAG 信源后复查此 ID。
3. **JWT 有效期**：`.sag_token` 为 7 天有效期 JWT，当前有效（实测 200）。建议在过期前刷新，避免某日 SAG 召回集体 401。

## 变更建议

无代码变更需求。SAG 升级未破坏知识导航的调用契约。
