# backfill-k-vector

> 知识树节点 `k_vector` 一次性回填工具。补全 `knowledge_tree` 表中所有 `k_vector IS NULL` 的节点（knowledge_point 与 subject 两类），供向量检索使用。

## 用途

知识树早期写入时部分节点缺少 embedding 向量。本工具扫描缺失节点并补全：

- **knowledge_point**：直接调用 SiliconFlow `BAAI/bge-m3` embedding API，对节点 `name` 取向量写入
- **subject**：优先用其子孙节点 `k_vector` 的均值作为自身向量；若无子孙向量，则对自身 `name` 做 embedding 兜底

## 用法

```bash
# 干跑：仅统计缺失数量，不写入
python3 backfill_k_vector.py

# 实际写入
python3 backfill_k_vector.py --apply
```

## 依赖与环境

- **环境变量**：
  - `KT_DB_URL`（或 `HERMES_DSN`）：PostgreSQL 连接串，需 `source /root/.hermes/.env`
  - `SILICONFLOW_API_KEY`：SiliconFlow embedding API Key（knowledge_point / subject 兜底路径需要）
- **Python 依赖**：`httpx`、`numpy`、`psycopg2`

## 输出

运行结束打印汇总：knowledge_point / subject 的发现数与已补数、失败数、跳过数。

> 注：与知识树其他脚本一致，连接串优先取 `KT_DB_URL`。embedding 走 SiliconFlow（LiteLLM 无 embedding 模型）。
