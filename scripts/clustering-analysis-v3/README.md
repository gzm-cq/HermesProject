# clustering-analysis-v3

> Hermes 数据飞轮的**记忆维护与聚类分析**组件：从 `memory_units` 中做质量报告 → 超长记忆治理 → MinHash 去重 → 实体挂靠 → HDBSCAN 聚类 → LLM 因果链检测 → 飞书通知 + 基线反馈闭环。
>
> 触发：周一 10:00 cron（`scripts/clustering-analysis-v3/scripts/clustering-analysis-cron.sh`）。

## 记忆维护管线（cron_wrapper.sh）

`scripts/cron_wrapper.sh` 是 6 步 Hindsight 记忆维护管线，`no_agent=true` 可用，每步独立 if/else，失败不阻断后续：

| 步骤 | 内容 | 说明 |
|------|------|------|
| ① | 质量报告 | `memory_quality_report.py --report-only`，统计超长/低质记忆 |
| ② | 超长记忆治理 | `long_memory_governance.py`：归档原文 + 压缩替换 + 重算 embedding，不直接删除唯一记忆 |
| ③ | MinHash LSH 去重 | `dedup_minhash.py`：跨条目去重（级联删除重复） |
| ④ | 聚类分析 | `clustering_analysis.cli.run`：实体挂靠 → HDBSCAN 自适应聚类 |
| ⑤ | 飞书通知 | 从日志解析关键指标，结构化总结（lark-cli / webhook 双通道） |
| ⑥ | 基线反馈闭环 | 对比 `clustering_audit.log` 的 silhouette，质量下降连续 3 周告警升级 |

```bash
# dry-run 管线（默认，不写入）
bash scripts/cron_wrapper.sh

# 写入管线（②③④ 执行 apply，需显式确认）
CONFIRM_APPLY=I_UNDERSTAND_THIS_WRITES_HINDSIGHT bash scripts/cron_wrapper.sh --apply

# 跳过指定步骤（逗号分隔，1-based）
bash scripts/cron_wrapper.sh --skip-steps "1,3"
```

> 环境变量：`CLUSTERING_DB_URL`（必填）、`CONFIRM_APPLY`、`FEISHU_WEBHOOK_URL` / `FEISHU_CHAT_ID`（通知）。

## 目录结构

```
scripts/clustering-analysis-v3/
├── config/default.yaml        # 默认配置（sample_size、epsilon_range、llm/embed 端点）
├── scripts/
│   ├── cron_wrapper.sh            # 记忆维护管线入口
│   ├── clustering-analysis-cron.sh # 部署到 /root/.hermes/scripts 的 cron wrapper
│   ├── dedup_minhash.py           # MinHash 去重独立脚本（--apply 才写入）
│   ├── long_memory_governance.py  # 超长记忆治理（归档+压缩+重算向量）
│   ├── mark_memory.py             # 记忆标记工具
│   └── memory_quality_report.py   # 记忆质量报告
├── src/clustering_analysis/
│   ├── cli.py                  # typer CLI（dedup_memories / quality_score / run）
│   ├── config.py               # AppConfig + load_config
│   ├── adapters/database.py    # PG 访问适配层
│   └── core/
│       ├── clustering.py       # HDBSCAN 聚类 + 因果链检测（含增量）
│       ├── dedup.py            # 去重核心（MinHash / Jaccard）
│       ├── embeddings.py       # 批量 embedding
│       └── quality.py          # 质量评分
└── tests/                      # pytest 回归套件
```

## 手动运行

本包无 `__main__.py` / pyproject 入口，通过 `PYTHONPATH=src` 显式调用：

```bash
cd scripts/clustering-analysis-v3
export CLUSTERING_DB_URL="postgres://..."

# 1) 记忆去重（dry-run 预览，实际合并去掉 --dry-run）
PYTHONPATH=src python -c "from clustering_analysis.cli import run; run()" \
  --help

# 2) 多轮聚类（实体挂靠 → HDBSCAN；--apply 才写入 PG）
PYTHONPATH=src python -c "from clustering_analysis.cli import run; run(apply=True, force=True)"

# 3) 质量评分（0=全量，输出低分记忆）
PYTHONPATH=src python -c "from clustering_analysis.cli import quality_score; quality_score(sample_size=0, min_score=0.6)"
```

> embedding/LLM 配置可从 `~/.hindsight/daemon.env`（`HINDSIGHT_API_EMBEDDINGS_*`）兜底读取；LLM 缺省用 `s-deepseek-v4-flash`。

## 配置要点

| 键 | 说明 |
|----|------|
| `sample_size` | 采样条数，`0`=全量不限 |
| `epsilon_range` | HDBSCAN eps 候选集（自适应搜索） |
| `min_samples` | HDBSCAN 最小样本数 |
| `entity_boost_factor` | 实体权重提升因子 |
| `max_group_size` | 组内最大成员数 |
| `llm_api_url` / `llm_model` | 因果链检测 LLM 端点 |
| `embed_base_url` / `embed_model` | embedding 服务（默认 bge-m3） |

## 本地开发

```bash
cd scripts/clustering-analysis-v3
pip install -e .
pytest
```

## 部署

```bash
sudo ./deploy/deploy.sh deploy clustering-analysis-v3 --yes
```

