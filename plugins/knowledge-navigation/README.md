# 知识导航插件（knowledge_navigation）

> 自动 recall Hindsight 经验记忆并融合知识树知识点，智能注入上下文，提升 LLM 回答质量

## ✨ 插件简介

知识导航插件是 Hermes 平台的核心增强组件，专为提升大语言模型（LLM）的上下文感知能力而设计。它在每次 LLM 调用前自动触发，从 Hindsight 记忆库召回经验片段，并融合知识树结构化知识点，以 XML 结构注入到请求上下文中，从而显著提升回答的准确性、连贯性和专业性。

**核心特性**：
- 🔍 **智能召回**：基于语义相似度与 rerank_score 精度过滤
- ⚡ **高性能**：内置连接池、超时控制与熔断器
- 📊 **可观测性**：结构化 JSON 日志，支持监控与调试
- 🛡️ **高可靠**：熔断器防级联故障 + 飞书告警通知
- 🧩 **易集成**：零侵入式 Hook 注册，开箱即用

---

## 🚀 快速开始

### 1. 前置条件
- 已部署并运行 Hindsight 服务（默认监听 `http://localhost:9177`）
- Hermes 运行环境已安装 `requests>=2.25.0`

### 2. 安装插件
将本插件目录（`plugins/knowledge_navigation/`）置于 Hermes 的插件加载路径下即可，Hermes 启动时会自动发现并加载。

### 3. 启用插件
确保 `plugin.yaml` 中的 hook 已注册（默认已启用）：
```yaml
hooks:
  pre_llm_call:
    callback: pre_llm_call
```

---

## ⚙️ 配置说明

所有配置项均位于 `config.py`，支持通过环境变量覆盖：

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
| `feishu_app_id` / `feishu_app_secret` / `feishu_home_channel` | `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_HOME_CHANNEL` | `""` | 飞书 OpenAPI 应用凭据与告警群聊 |
| `enable_temporal` | `KN_ENABLE_TEMPORAL` | `true` | 是否启用时态衰减排序 |
| `eval_queries_path` | `KN_EVAL_QUERIES_PATH` | `""` | 评测查询 JSON 路径 |
| `enable_knowledge_tree` | `KN_ENABLE_KT` | `true` | 是否启用知识树 recall |
| `knowledge_tree_api_url` | `KN_KT_URL` | `http://localhost:9100` | 知识树 API 地址 |
| `kt_max_results` | `KN_KT_MAX_RESULTS` | `5` | 知识树最多注入条数 |
| `enable_score_span_compress` | `KN_SCORE_SPAN_COMPRESS` | `true` | 是否启用分数跨度压缩 |
| `score_span_top3_threshold` | `KN_SPAN_TOP3` | `0.15` | Top-3 与平均分差距阈值 |
| `score_span_half_threshold` | `KN_SPAN_HALF` | `0.10` | 后半段与平均分差距阈值 |
| `enable_cross_domain_dedup` | `KN_CROSS_DOMAIN_DEDUP` | `true` | 是否启用跨域去重 |

---

## 🌳 知识树集成

插件支持从**知识树（Knowledge Tree）**并行 recall，与主 recall（Hindsight）结果融合后统一过滤与注入。

### 工作流程

1. **并行 recall**：`pre_llm_call` 中同时发起 Hindsight recall 和知识树 recall（线程池异步）
2. **结果融合**：知识树结果追加到 Hindsight 结果之后
3. **统一过滤**：融合后的结果经过去重、分数过滤、时间衰减、跨域去重
4. **格式化注入**：最终 `kept` 结果格式化为 `<<< hint ... >>>` 文本注入 LLM

### 配置参数

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|---------|--------|------|
| `enable_knowledge_tree` | `KN_ENABLE_KT` | `true` | 是否启用知识树 recall |
| `knowledge_tree_api_url` | `KN_KT_URL` | `http://localhost:9100` | 知识树服务地址 |
| `kt_max_results` | `KN_KT_MAX_RESULTS` | `5` | 知识树最多返回条数 |

### 知识树结果格式

知识树 recall 返回的结果格式与 Hindsight 类似，但包含额外的 `source` 字段标识来源：

```json
{
  "id": "kt_xxx",
  "text": "知识树节点内容...",
  "score": 0.85,
  "timestamp": "2026-06-01T12:00:00Z",
  "metadata": {"source": "knowledge_tree", "node_id": "..."}
}
```

---

## ▶️ 使用示例

### 命令行测试（开发/调试用）
```bash
# 测试 recall 功能（需在 plugins/knowledge_navigation 目录下执行）
python -m knowledge_navigation "如何配置知识导航插件？"

# 列出支持的钩子
python -m knowledge_navigation --list-hooks
```

### 日志查看
插件日志默认写入 `plugins/knowledge_navigation/trace.log`，格式为 JSON：
```json
{
  "timestamp": "2026-05-24T15:30:45.123Z",
  "session_id": "abc123de",
  "query": "如何配置知识导航插件？",
  "event": "recall_success",
  "total_results": 12,
  "kept_results": 3,
  "score_stats": {"min": 0.62, "max": 0.78, "avg": 0.71, "count": 3},
  "latency_ms": 427
}
```

---

## 🛠️ 故障排查

| `recall_empty` / `service_error` 日志频繁出现 | Hindsight 服务未运行 / 返回空响应 | 检查 `KN_HINDSIGHT_URL` 地址；检查服务状态 `curl http://localhost:9177/health` |
| `recall_error` 且报 `ConnectionError` | 网络不通或端口被占用 | `curl -v http://localhost:9177/health` 检查服务状态 |
| `recall_success` 但 `kept_results=0` | `MIN_SCORE` 设置过高 | 临时调低至 `0.4` 观察效果 |
| 飞书未收到熔断告警 | 未配置 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_HOME_CHANNEL` | 在 WSL 中设置环境变量后重启服务 |
| 熔断器频繁触发 | Hindsight 服务不稳定 | 检查服务日志；考虑上调 `KN_CB_THRESHOLD` |

## 🔔 熔断与告警

插件内置**熔断器**（Circuit Breaker），防止 Hindsight 服务异常时反复重试拖慢 LLM 响应：

- 连续 3 次 recall 失败 → **熔断打开**，跳过 recall 120 秒
- 冷却期后自动半开，下一次成功调用即完全恢复
- 所有失败按类型分类追踪（未预期异常 / 服务返回空 / 无匹配结果）

配置飞书 OpenAPI 三个环境变量后，熔断打开时会自动发送飞书卡片告警（限频 5 分钟一次）：

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET  # 已在安全环境中设置，勿在文档/仓库中写入真实值
export FEISHU_HOME_CHANNEL="oc_xxx"
```

---

## 📜 许可证
MIT License — 详见 `plugin.yaml` 元数据

---
*版本：1.1.0 | 最后更新：2026-06-14*