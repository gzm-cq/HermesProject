# knowledge-tree-plugin

> 知识树在线插件 — 在对话中实时增量学习新知识点。
> `post_llm_call` 增量入库；`pre_llm_call` 的知识树 recall 由 `knowledge-navigation` 插件统一负责。

## 类型

Hermes Gateway 插件（Hook 注册）。部署需重启 hermes-gateway。

## 目录结构

| 路径 | 职责 |
|------|------|
| `plugin.yaml` | Hermes 插件注册元信息（hooks: `post_llm_call`） |
| `config/default.yaml` | 默认配置（可通过 `KT_PLUGIN_CONFIG` 覆盖路径） |
| `src/knowledge_tree_plugin/hooks.py` | `post_llm_call` 钩子：提取新知识点 → 去重 → 增量写入知识树 |
| `src/knowledge_tree_plugin/public_api.py` | 供知识导航插件调用的公共 API（recall / multi-hop） |
| `src/knowledge_tree_plugin/recall.py` | 召回核心：科目定位、注意力筛选、时态过滤、use_log 回写 |
| `src/knowledge_tree_plugin/placement.py` | 知识点定位挂靠（子树 / k-vector） |
| `src/knowledge_tree_plugin/extract_new.py` | 新知识点提取逻辑 |
| `src/knowledge_tree_plugin/adapters/database.py` | PostgreSQL + pgvector 适配器（线程池复用） |
| `src/knowledge_tree_plugin/kt_builder_path.py` | 解析 knowledge-tree-builder 源码路径 |
| `src/knowledge_tree_plugin/config.py` | `PluginConfig` 配置加载 |

## 功能

1. **post_llm_call** — 对话结束后，从 LLM 回复中提取新知识点，去重后增量写入知识树
2. **public_api** — 提供 `recall_from_tree()` / `recall_from_tree_raw()` / `multi_hop_recall()` 等接口供知识导航插件调用

### 公共 API

| 接口 | 说明 |
|------|------|
| `recall_from_tree(session_id, user_message)` | 从知识树召回相关知识点，返回格式化注入文本（无可匹配时返回 `None`） |
| `recall_from_tree_raw(session_id, user_message)` | 同上，返回结构化结果 `[{id, name, text, score}]`，供跨域去重/融合使用 |
| `multi_hop_recall(seed_kp_ids, top_k=10)` | 从种子知识点沿三路策略展开关联知识点 |

### 三路多跳召回（multi_hop_recall）

| 路线 | 策略 | 说明 |
|------|------|------|
| Route A | `subject` | seed KPs → 同 subject 兄弟节点 |
| Route B | `entity` | seed KPs → `kt_entity_links` → 共享实体的其他 KPs |
| Route C | `edge` | seed KPs → `knowledge_tree_edges` → 预建边关联 KPs |

各路线结果合并去重，标注 `strategy` 来源；某路线数据为空自动跳过，不阻塞其他路线。多跳结果标记 `source="multi-hop"`（跳过 rerank，关联内容语义维度不同，混排会被向量结果淹没）。

## 测试

```bash
cd /mnt/d/HermesProject/plugins/knowledge-tree-plugin && \
PYTHONPATH=src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src python3 -m pytest tests
```

## 依赖

- PostgreSQL（knowledge_tree 库，pgvector 扩展）
- knowledge-tree-builder 的 `batch_embed`（嵌入服务）
- 由知识导航插件配合调用（`HAS_KNOWLEDGE_TREE=True`）
- 共享工具来自 hermes-common 库（部署至 `/root/.hermes/lib/hermes_common`）

## 部署

```bash
cd /mnt/d/HermesProject && bash deploy/deploy.sh deploy knowledge-tree-plugin --yes
```

部署后重启 hermes-gateway 生效。
