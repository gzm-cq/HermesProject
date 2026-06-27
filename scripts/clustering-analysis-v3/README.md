# clustering-analysis-v3 — Hindsight 记忆聚类与因果链增量维护

对 Hindsight `memory_units` 做语义聚类、实体挂靠、因果链检测、长记忆治理与审计日志写入。项目采用 `src/` package layout，源码部署到 `/root/.hermes/scripts/clustering-analysis-v3/` 后由 cron/no_agent 调度运行。

## 当前运行入口

### 推荐：cron wrapper 完整管线

`src/clustering_analysis/cli.py` 是 Typer 单命令应用；不同 Typer 版本下 `python -m clustering_analysis.cli run --apply` 可能把 `run` 当成无效参数。生产/cron 推荐使用 wrapper，直接调用 Python `run()` 函数绕过解析差异。

```bash
cd ~/.hermes/scripts/clustering-analysis-v3
source ~/.hermes/.env 2>/dev/null
CLUSTERING_DB_URL="$CLUSTERING_DB_URL" bash scripts/cron_wrapper.sh
```

只重跑聚类步骤（跳过质量报告、超长治理、MinHash、通知）：

```bash
cd ~/.hermes/scripts/clustering-analysis-v3
source ~/.hermes/.env 2>/dev/null
CLUSTERING_DB_URL="$CLUSTERING_DB_URL" bash scripts/cron_wrapper.sh --skip-steps "1,2,3,5"
```

### 直接调用 `run()`（调试）

```bash
cd ~/.hermes/scripts/clustering-analysis-v3
source ~/.hermes/.env 2>/dev/null
PYTHONPATH="src:${PYTHONPATH:-}" CLUSTERING_DB_URL="$CLUSTERING_DB_URL" python3 - <<'PY'
from clustering_analysis.cli import run
run(apply=True, dry_run=False, cleanup=False, force=True, skip_entity=False, config_path="config/default.yaml")
PY
```

### 开发安装后 CLI

```bash
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3
python3 -m venv venv
venv/bin/pip install -e .
PYTHONPATH=src venv/bin/python -m clustering_analysis.cli --apply
```

不加 `--apply` 为 dry-run；写库必须显式 `--apply`。

## 管线阶段

1. 质量报告：`scripts/memory_quality_report.py`
2. 超长记忆治理：`scripts/dedup_long_memories.py`
3. MinHash/LSH 去重：`scripts/dedup_minhash.py`
4. 聚类与写库：`clustering_analysis.cli.run()`
   - 拉取 `memory_units` + embeddings + 既有实体
   - 既有实体相似挂靠
   - HDBSCAN 语义聚类
   - 实体合并 / 因果链检测 / 文本富化
   - 批量 UPSERT `entities`、`unit_entities`、`memory_links` 并更新 embedding
5. 飞书通知：wrapper 汇总执行结果后发送

## 关键配置

配置文件：`config/default.yaml`，敏感值从环境变量注入。

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `sample_size` | `0` | 0=全量；>0 时抽样 |
| `min_samples` | `3` | HDBSCAN 的 `min_cluster_size` / `min_samples` 基准值 |
| `max_group_size` | `20` | 超过该规模的簇跳过高成本处理 |
| `max_workers` | `32` | 并发线程数 |
| `epsilon_range` | `[0.15...0.50]` | 兼容旧 DBSCAN 配置，当前 HDBSCAN 主路径不使用 |
| `llm_api_url` | `http://127.0.0.1:4142/v1/chat/completions` | LLM 网关地址 |
| `llm_model` | `s-deepseek-v4-flash` | LLM 模型 |
| `embed_model` | `BAAI/bge-m3` | embedding 模型 |

必需环境变量：

```bash
CLUSTERING_DB_URL=postgresql://...
LITELLM_MASTER_KEY=...
HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY=...
```

不要把连接串或 key 写入仓库。

## 目录结构

```text
scripts/clustering-analysis-v3/
├── config/default.yaml
├── scripts/
│   ├── cron_wrapper.sh
│   ├── memory_quality_report.py
│   ├── dedup_long_memories.py
│   ├── dedup_minhash.py
│   └── mark_memory.py
├── src/clustering_analysis/
│   ├── cli.py
│   ├── config.py
│   ├── clustering.py
│   ├── database.py
│   └── ...
├── tests/
├── skills/
└── pyproject.toml
```

## 部署与验证

从源码目录部署，不直接修改 `/root/.hermes`：

```bash
cd /mnt/d/HermesProject
./deploy/deploy.sh plan clustering-analysis-v3
./deploy/deploy.sh deploy clustering-analysis-v3
```

验证建议：

```bash
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3
PYTHONPATH=src python3 -m pytest tests
PYTHONPATH=src python3 -m clustering_analysis.cli --help
```

## 安全约束

- 默认 dry-run；写库必须 `--apply`。
- 聚类写库是增量 UPSERT，`cleanup` 当前不删除实体或因果链。
- `memory_links` 依赖 `(from_unit_id, to_unit_id, link_type)` 唯一索引以支持 `ON CONFLICT`。
- `mark_memory.py` 维护 `[标记: 错误/作废/可疑/待验证/已解决]`，其中前四类会被知识导航 recall 排除。
