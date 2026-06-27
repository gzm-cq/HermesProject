# 知识导航插件（knowledge_navigation）

> 自动 recall Hindsight 经验记忆并融合知识树知识点，通过 LLM Router 智能决策三路注入，提升 LLM 回答质量

## ✨ 插件简介

知识导航插件是 Hermes 平台的核心增强组件，专为提升大语言模型（LLM）的上下文感知能力而设计。它在每次 LLM 调用前自动触发，通过 **LLM Router** 决策需要注入哪些知识源，从 Hindsight 记忆库召回经验片段、知识树提取结构化知识、Skill 匹配操作流程，以 XML 语义标签格式注入到请求上下文中。

**核心特性**：
- 🧠 **LLM Router 智能决策**：基于 need analysis 判断 H（经验）/ KT（知识）/ S（技能）三路是否需要注入
- 🔍 **智能召回**：基于语义相似度与 rerank_score 精度过滤
- ⚡ **高性能**：内置连接池、超时控制与熔断器；按 mask 条件执行（多路并行/单路串行）
- 📊 **可观测性**：结构化 JSON 日志（含 router_mask 事件），支持监控与基线对比
- 🛡️ **高可靠**：熔断器防级联故障 + 飞书告警通知；Router 异常自动 fallback 全开
- 🧩 **易集成**：零侵入式 Hook 注册，开箱即用

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
      ├─ kt → _do_kt_recall() → multi_hop_expand()（同科目关联展开）
      └─ s → _do_skill_match()
  ↓
后处理（过滤/去重/融合/标签化注入）
```

---

## 🌳 知识树集成

### 工作流程

1. **LLM Router 决策**：判断 user message 是否需要知识树（mask.kt）
2. **条件 recall**：仅在 `mask.kt=true` 时执行知识树 recall
3. **多跳关联展开**：从知识树召回的知识点出发，沿 subject 展开同科目下的关联知识点（`multi_hop_recall`），标记 `source="multi-hop"`，**跳过 rerank**（关联内容语义维度不同，混排会被向量结果淹没）
4. **结果融合 + 跨域去重**：多跳结果合并到 KT 结果，与 Hindsight 结果做跨域去重（文本 n-gram Jaccard 或 embedding）
5. **统一过滤与注入**：经 Compaction、分数过滤、turn-to-turn 去重后，按来源（hindsight/knowledge_tree）分别以 XML 语义标签注入

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

*版本：1.2.0 | 最后更新：2026-06-28*
