# knowledge-tree-plugin

> 知识树在线插件 — 在对话中实时召回知识树节点、增量学习新知识点。

## 类型

Hermes Gateway 插件（Hook 注册）。部署需重启 hermes-gateway。

## 入口

| 文件 | 职责 |
|------|------|
| `src/__init__.py` | 模块入口 |
| `plugin.yaml` | Hermes 插件注册元信息 |
| `config/default.yaml` | 默认配置 |

## 测试

```bash
cd plugins/knowledge-tree-plugin && python3 -m pytest tests/
```

## 功能

1. **post_llm_call** — 对话结束后，从 LLM 回复中提取新知识点，聚类写入知识树
2. **pre_llm_call recall** — 在知识导航插件之前，从知识树召回相关知识节点

## 依赖

- PostgreSQL（knowledge_tree 库）
- 由知识导航插件配合调用（`HAS_KNOWLEDGE_TREE=True`）

## 部署

```bash
cd deploy && ./deploy.sh deploy knowledge-navigation --yes  # 随知识导航一起部署
```
