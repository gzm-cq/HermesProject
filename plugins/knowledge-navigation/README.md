# 知识导航插件（knowledge_navigation）

> 自动 recall Hindsight 经验记忆并融合知识树知识点，通过 LLM Router 智能决策三路注入，采用**三级混合筛选架构**提升召回质量与效率

## ✨ 插件简介

知识导航插件是 Hermes 平台的核心增强组件，专为提升大语言模型（LLM）的上下文感知能力而设计。它在每次 LLM 调用前自动触发，通过 **LLM Router** 决策需要注入哪些知识源，从 Hindsight 记忆库召回经验片段、知识树提取结构化知识、Skill 匹配操作流程，以 XML 语义标签格式注入到请求上下文中。

**核心特性**：
- 🧠 **LLM Router 智能决策**：基于 need analysis 判断 H（经验）/ KT（知识）/ S（技能）三路是否需要注入；支持 confidence 置信度字段，低置信度（<0.5）时自动保守 fallback 全开
- 🔍 **三级混合筛选**：Skill 匹配采用"关键词粗筛（Top-30）→ Embedding 语义精筛（Top-20）→ LLM 精排（Top-3）"三级漏斗，平衡召回率与效率
- ⚡ **高性能**：内置连接池、超时控制与熔断器；按 mask 条件执行（多路并行/单路串行）；Router 缓存（TTL 5 分钟，64 条目上限），同 session 相同消息复用决策
- 📊 **可观测性**：结构化 JSON 日志（含 router_mask 事件），支持监控与基线对比；fallback 原因分类统计（json_parse/api_401/api_timeout/api_error/api_other）；调用耗时记录
- 🛡️ **高可靠**：熔断器防级联故障 + 飞书告警通知；Router 异常自动 fallback 全开；Embedding 调用失败自动降级；401 Unauthorized 自动重试（刷新 API key）
- 🧩 **易集成**：零侵入式 Hook 注册，开箱即用
- ⏰ **时态感知**：支持知识点的有效期过滤（valid_from/valid_until），自动剔除过期知识

---

## 🚀 快速开始

### 1. 前置条件
- 已部署并运行 Hindsight 服务（默认监听 `http://localhost:9177`）
- Hermes 运行环境已安装 `requests>=2.25.0` 和 `httpx`

### 2. 安装插件
将本插件目录（`plugins/knowledge-navigation/`）置于 Hermes 的插件加载路径下即可，Hermes 启动时会自动发现并加载。

### 3. 启用插件
确保 `plugin.yaml` 中的 hook 已注册（默认已启用）：

```yaml
hooks:
  pre_llm_call:
    callback: pre_llm_call
```

---

## ⚙️ 配置说明

所有配置项均位于 `config.py`，支持通过环境变量覆盖。

### 通用配置

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|---------|--------|------|
| `hindsight_api_url` | `KN_HINDSIGHT_URL` | `http://localhost:9177/v1/...` | Hindsight API 地址 |
| `timeout_seconds` | `KN_TIMEOUT_SECONDS` | `25` | 单次请求超时（秒） |
| `max_retries` | `KN_MAX_RETRIES` | `0` | HTTP 重试次数（0=不重试，依赖熔断器） |
| `min_score` | `KN_MIN_SCORE` | `0.6` | 最低接受分数（rerank_score） |
| `max_results` | `KN_MAX_RESULTS` | `3` | 最多注入记忆条数 |
| `max_text_length` | `KN_MAX_TEXT_LENGTH` | `200` | 每条记忆截断长度（字符） |
| `trace_log_path` | `KN_TRACE_LOG_PATH` | `trace.log` | 日志文件路径 |
| `circuit_breaker_threshold` | `KN_CB_THRESHOLD` | `3` | 熔断器阈值（连续失败次数） |
| `circuit_breaker_cooldown` | `KN_CB_COOLDOWN` | `120` | 熔断冷却时间（秒） |
| `feishu_app_id/app_secret/home_channel` | `FEISHU_APP_ID` 等 | `""` | 飞书 OpenAPI 凭据与告警群聊 |
| `enable_temporal` | `KN_ENABLE_TEMPORAL` | `true` | 是否启用时态衰减排序 |
| `eval_queries_path` | `KN_EVAL_QUERIES_PATH` | `""` | 评测查询 JSON 路径 |
| `enable_score_span_compress` | `KN_SCORE_SPAN_COMPRESS` | `true` | 是否启用分数跨度压缩 |

### LLM Router 配置

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|---------|--------|------|
| `router_model` | `KN_ROUTER_MODEL` | `sensenova-6.7-flash-lite` | Router LLM 模型 |
| `router_api_url` | `KN_ROUTER_API_URL` | `http://127.0.0.1:4142/v1` | LLM API 端点（LiteLLM 网关） |
| `router_api_key` | `KN_ROUTER_API_KEY` | `""` | API Key（空时走网关默认凭证） |
| `router_timeout` | `KN_ROUTER_TIMEOUT` | `5` | Router 超时秒数 |

### Skill 匹配 Embedding 配置（三级筛选专用）

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|---------|--------|------|
| `enable_embedding_prescreen` | `KN_SKILL_EMBEDDING_PRESCREEN` | `true` | 是否启用 Embedding 预筛选（Stage 1.5） |
| `embedding_api_url` | `KN_SKILL_EMBEDDING_URL` | `https://api.siliconflow.cn/v1` | Embedding API 地址 |
| `embedding_api_key` | `KN_SKILL_EMBEDDING_API_KEY` | `""` | Embedding API Key（空时 fallback 到 `SILICONFLOW_API_KEY`） |
| `embedding_model` | `KN_SKILL_EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding 模型 |
| `prescreen_top_k` | `KN_SKILL_PRESCREEN_TOP_K` | `30` | 关键词预筛选保留数量 |
| `embedding_top_k` | `KN_SKILL_EMBEDDING_TOP_K` | `20` | Embedding 精筛保留数量 |
| `embedding_batch_size` | `KN_SKILL_EMBEDDING_BATCH_SIZE` | `20` | Embedding API 批量调用大小 |
| `embedding_circuit_threshold` | `KN_SKILL_EMBEDDING_CB_THRESHOLD` | `3` | Embedding 熔断阈值 |
| `embedding_circuit_cooldown` | `KN_SKILL_EMBEDDING_CB_COOLDOWN` | `300` | Embedding 熔断冷却时间（秒） |

---

## 🔄 工作流

```
用户消息
  ↓
三层门控 ← turn_gate（来源 / 系统提示词 / 文本门控）
  ↓ 通过
熔断器检查（仅影响 Hindsight 路）
  ↓
LLM Router → {h: bool, kt: bool, s: bool}
  │ 异常/超时 → fallback 全开
  ↓
全 false? → return None
  ↓
按 mask 条件执行
  ├─ 2+ 路 → ThreadPoolExecutor 并行
  └─ 1 路 → 串行
      ├─ h → _do_hindsight_recall()
      ├─ kt → _do_kt_recall() → multi_hop_expand()（实体多跳关联展开）
      └─ s → _do_skill_match()（三级混合筛选）
  ↓
后处理（过滤/去重/融合/标签化注入）
```

### Skill 三级混合筛选

```
Stage 1: 关键词预筛选（<1ms，345 → Top-30）
         └─ 基于 skill name/description 的关键词匹配

Stage 1.5: Embedding 余弦相似度（<10ms，30 → Top-20）
         └─ 向量化后计算余弦相似度，语义精筛
         └─ 失败时自动降级到关键词结果

Stage 2: LLM 精排（~500ms，20 → Top-3）
         └─ 调用 LLM 对候选 skill 进行相关性打分
```

---

## 🌳 知识树集成

### 工作流程

1. **LLM Router 决策**：判断 user message 是否需要知识树（mask.kt）
2. **条件 recall**：仅在 `mask.kt=true` 时执行知识树 recall
3. **实体多跳关联展开**：从知识树召回的知识点出发，沿 `kt_entity_links` 表展开共享实体的关联知识点（`multi_hop_recall`），标记 `source="multi-hop"`，**跳过 rerank**（关联内容语义维度不同，混排会被向量结果淹没）
4. **结果融合 + 跨域去重**：多跳结果合并到 KT 结果，与 Hindsight 结果做跨域去重（文本 n-gram Jaccard 或 embedding）
5. **统一过滤与注入**：经 Compaction、分数过滤、turn-to-turn 去重后，按来源（hindsight/knowledge_tree）分别以 XML 语义标签注入

### 时态感知

知识树召回支持时态过滤（`valid_from` / `valid_until`），自动剔除过期知识。通过 `KN_ENABLE_TEMPORAL` Feature Flag 控制。

---

## ▶️ 使用示例

### 命令行测试

```bash
# 测试 recall 功能
python -m knowledge_navigation "如何配置知识导航插件？"

# 列出支持的钩子
python -m knowledge_navigation --list-hooks
```

### 日志查看

插件日志默认写入 `trace.log`，JSON 格式：

```json
{
  "timestamp": "2026-06-28T00:47:30.123Z",
  "session_id": "abc123de",
  "event": "router_mask",
  "mask": {"h": true, "kt": false, "s": true}
}
```

---

## 🛠️ 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `recall_empty` / `service_error` 频繁 | Hindsight 服务异常 | 检查 `KN_HINDSIGHT_URL` |
| `recall_error` + `ConnectionError` | 网络不通 | `curl -v http://localhost:9177/health` |
| `kept_results=0` | MIN_SCORE 过高 | 临时调低至 0.4 |
| Router 频繁 fallback 全开 | Router LLM 不可用 | 检查 LiteLLM 网关 |
| 熔断器频繁触发 | Hindsight 不稳定 | 调大 `KN_CB_THRESHOLD` |

---

## 🔔 熔断与告警

插件内置**熔断器**（Circuit Breaker）：

- 连续 3 次 recall 失败 → **熔断打开**，跳过 recall 120 秒
- 冷却期后自动半开，成功即恢复
- Router 不受熔断影响，KT/Skill 路照常执行

配置飞书 OpenAPI 变量后，熔断打开时自动发送卡片告警：

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET
export FEISHU_HOME_CHANNEL="oc_xxx"
```

---

## 📜 许可证

MIT License

---

*版本：1.3.0 | 最后更新：2026-07-03*
