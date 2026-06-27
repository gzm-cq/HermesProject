---
name: knowledge-tree-plugin
description: 知识树在线插件 — post_llm_call 增量学习，在对话中提取新知识点并自动入库
version: 0.1.1
author: Hermes Team
license: MIT
metadata:
  hermes:
    tags: [knowledge-tree, incremental-learning, knowledge-extraction, hermes-plugin]
    related_skills: [knowledge-navigation, knowledge-tree-builder]
    categories: [knowledge-management, plugins]
---

# Knowledge Tree Plugin

Hermes 插件。在每次 LLM 调用后，从用户消息与 assistant 回复中提取新知识点，异步增量写入知识树。知识树 recall（pre_llm_call）由 `knowledge-navigation` 插件统一负责，本插件只注册 `post_llm_call`。

## 职责

- **post_llm_call**：轻量门控后入后台队列，不阻塞主对话返回。
- **extract_from_dialog**：调用 `knowledge_tree_builder.phase.merged.analyze_and_split`，提取当前知识点类型（原理/公式/要点/结论；方法类如由 builder 准入则一并保留），并按输入规模动态选择单次或分块并行提取。
- **place_new_knowledge_points**：对新知识点做去重、矛盾检测、PG 写入，并更新父科目 K 向量。

## 架构

```text
LLM 响应
  ↓
post_llm_call hook
  ↓
cheap gate + Queue(maxsize=100)
  ↓
background worker
  ↓
extract_from_dialog(..., llm_retries, llm_timeout_seconds)
  ↓
place_new_knowledge_points()
  ↓
去重 → 矛盾检测 → PG 写入 → K 向量更新
```

## 配置

配置文件：`config/default.yaml`，环境变量优先覆盖。

```yaml
db_url: ""                         # KT_DB_URL
max_recall_results: 5
recall_min_score: 0.3
cold_start_threshold: 20

extract_enabled: true
extract_min_dialog_length: 50
extract_max_input_length: 4000
min_knowledge_point_length: 10
extract_llm_timeout_seconds: 30      # KT_EXTRACT_LLM_TIMEOUT_SECONDS
extract_llm_retries: 1               # KT_EXTRACT_LLM_RETRIES；会传入 extract_from_dialog

llm_api_url: "http://127.0.0.1:4142/v1/chat/completions"
llm_model: "s-deepseek-v4-flash"
embed_model: "BAAI/bge-m3"
dedup_cosine_threshold: 0.95
conflict_cosine_threshold: 0.80
k_vector_alpha_max: 0.1
```

主要环境变量：

| 环境变量 | 作用 |
|---|---|
| `KT_DB_URL` | 知识树 PostgreSQL/pgvector 连接串 |
| `LITELLM_MASTER_KEY` | LLM 网关 key |
| `KT_LLM_MODEL` | 覆盖提取模型 |
| `KT_EXTRACT_LLM_TIMEOUT_SECONDS` | 单次 LLM 请求 read timeout |
| `KT_EXTRACT_LLM_RETRIES` | 提取请求重试次数 |
| `HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY` | embedding API key |

## 运行约束

- hook 只入队，后台 worker 执行 LLM 提取与放置，避免拖慢用户响应。
- `extract_llm_timeout_seconds` 是单次请求超时；worker hard timeout 按 `timeout × retries + 5s` 计算。
- 若队列满，本轮知识提取会跳过并记录日志，不影响主对话。
- 命令输出、部署状态、代码/日志密集响应会被 cheap gate 跳过，经验轨迹交给 Hindsight 存储。

## 验证

```bash
cd /mnt/d/HermesProject/plugins/knowledge-tree-plugin
PYTHONPATH=src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src pytest tests
```

部署必须从 `/mnt/d/HermesProject` 走 `deploy/deploy.sh`，不要直接改 `/root/.hermes/plugins/knowledge-tree-plugin`。
