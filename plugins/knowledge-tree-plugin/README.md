# knowledge-tree-plugin

> 知识树在线插件 — 在对话中实时增量学习新知识点。

## 类型

Hermes Gateway 插件（Hook 注册）。部署需重启 hermes-gateway。

## 入口

| 文件 | 职责 |
|------|------|
| `src/__init__.py` | 模块入口 |
| `plugin.yaml` | Hermes 插件注册元信息 |
| `config/default.yaml` | 默认配置 |

## 功能

1. **post_llm_call** — 对话结束后，从 LLM 回复中提取新知识点，去重后增量写入知识树
2. **public_api** — 提供 `recall_from_tree()` / `multi_hop_recall()` 等接口供知识导航插件调用
   - `recall_from_tree(query, top_k)` — 向量匹配种子知识点
   - `multi_hop_recall(seed_ids, top_k)` — 从种子沿 `kt_entity_links` 表展开共享实体的关联知识点（实体多跳），标记 `source="multi-hop"`（跳过 rerank）

> 知识树的 **pre_llm_call recall** 由 `knowledge-navigation` 插件统一负责（`_do_kt_recall()`），本插件只注册 `post_llm_call`。

## 测试

```bash
cd /mnt/d/HermesProject/plugins/knowledge-tree-plugin && \
PYTHONPATH=src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src python3 -m pytest tests
```

## 依赖

- PostgreSQL（knowledge_tree 库）
- 由知识导航插件配合调用（`HAS_KNOWLEDGE_TREE=True`）

## 部署

```bash
cd /mnt/d/HermesProject && bash deploy/deploy.sh deploy knowledge-tree-plugin --yes
```
