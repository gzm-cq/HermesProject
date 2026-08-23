# graphiti-bridge

> Graphiti 时间维度桥接（数据飞轮增强方案 §3.2，P1-2，轻量自实现，对齐 Graphiti 语义）。
>
> 为**配置类知识**提供时间维度：同一配置变更后旧值标注过期，召回时只返回当前有效版本。

## 设计权衡

- 完整 Graphiti（graphiti-core + Neo4j）是重型依赖，且 Neo4j 端口与 Cognee 冲突
- 本 bridge 用本地 JSON 持久化实现相同的"时间版本 + as_of 查询"语义，零外部依赖，默认不启用
- 接口已对齐 Graphiti：`add_config_knowledge ↔ add_episode`，`search_as_of ↔ search(as_of=...)`，后续可无缝替换为 graphiti-core

## 持久化格式（state.json）

```json
{
  "entities": [
    {"key": "<配置标识>", "text": "<知识文本>", "valid_from": <ts>, "valid_to": <ts|null>, "source": "<来源>"}
  ]
}
```

状态文件默认 `~/.hermes/knowledge-navigation/graphiti_state.json`，可用 `KN_GRAPHITI_STATE` 覆盖。

## 用法

```bash
# 记录配置版本（同 key 的旧版本自动置为 expired）
python bridge.py add --key nginx.conf --text "旧配置内容"

# 查询当前有效版本（as_of=now）
python bridge.py search --key nginx.conf

# 列出所有版本（valid / expired）
python bridge.py list
```

## 核心接口

| 函数 | 说明 |
|------|------|
| `add_config_knowledge(key, text, valid_from=None, source="")` | 记录新版本，同 key 旧版本标记过期 |
| `search_as_of(key, as_of=None)` | 查询指定时间点的有效版本 |
| `is_expired(key, as_of=None)` | 判断是否过期 |
| `list_entities()` | 列出全部实体版本 |

> 本目录为单文件工具，无独立部署清单，直接以脚本方式调用。
