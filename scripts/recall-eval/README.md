# recall-eval

> Recall 质量评估框架 — RAGAS faithfulness 评估 Hindsight/知识树召回质量。
>
> 量化评估召回结果的忠实度、相关性、覆盖率，为后续优化提供数据支撑。

## 入口

| 入口 | 职责 |
|------|------|
| `src/recall_eval/cli.py` | CLI 主入口（typer 实现） |
| `src/recall_eval/__main__.py` | 支持 `python -m recall_eval` 调用 |
| `config/default.yaml` | 默认配置 |
| `pyproject.toml` | 包定义，注册 `recall-eval` CLI 命令 |

## 目录结构

```
src/recall_eval/
├── cli.py              # CLI 入口（run / report / list 三个子命令）
├── config.py           # 配置管理（YAML + ENV 覆盖）
├── __init__.py         # 包元信息（v0.1.0）
├── __main__.py         # python -m 支持
├── adapters/
│   └── llm_client.py   # LLM 客户端（评估调用 + token 计数 + JSON 解析重试）
└── core/
    ├── metrics.py      # 评估指标（faithfulness / relevance / coverage）
    ├── dataset.py      # 数据集管理（加载、分组、样本生成）
    └── runner.py       # 评估运行器（批量评估 + 报告生成）
```

## 评估指标

| 指标 | 说明 | 权重 |
|------|------|------|
| **Faithfulness (忠实度)** | 答案是否完全基于上下文，无幻觉 | 40% |
| **Relevance (相关性)** | 上下文与查询的相关程度 | 30% |
| **Coverage (覆盖率)** | 上下文覆盖查询要点的比例 | 30% |

## 使用

```bash
# 进入项目目录
cd scripts/recall-eval

# 启发式评估（无需 LLM，快速验证）
python -m recall_eval run

# 使用 LLM 评估（更准确，需要 API 可用）
python -m recall_eval run --llm

# 指定数据集和输出路径
python -m recall_eval run --dataset data/eval_queries.json --output reports/

# 只评估指定类别
python -m recall_eval run --category semantic

# JSON 格式输出
python -m recall_eval run --json

# 查看已生成的报告
python -m recall_eval report reports/eval-report-xxx.json

# 列出数据集
python -m recall_eval list
```

### CLI 命令

| 命令 | 说明 |
|------|------|
| `recall-eval run` | 运行评估 |
| `recall-eval report <path>` | 查看已生成的评估报告 |
| `recall-eval list` | 列出数据集中的查询 |

### 参数

| 参数 | 说明 |
|------|------|
| `--config PATH` | 指定配置文件路径 |
| `--dataset PATH` | 指定数据集路径 |
| `--output PATH` | 指定报告输出目录 |
| `--llm` | 使用 LLM 进行评估（默认启发式规则） |
| `--category NAME` | 只评估指定类别的查询 |
| `--json` | JSON 格式输出结果 |
| `--log-level LEVEL` | 日志级别（DEBUG/INFO/WARNING） |

## 配置

| 键 | 默认值 | ENV 覆盖 | 说明 |
|----|--------|----------|------|
| `dataset_path` | `data/eval_queries.json` | `RECALL_EVAL_DATASET_PATH` | 数据集路径 |
| `output_path` | `reports` | `RECALL_EVAL_OUTPUT_PATH` | 报告输出路径 |
| `eval_model` | `s-deepseek-v4-flash` | `RECALL_EVAL_MODEL` | 评估用 LLM 模型 |
| `eval_api_url` | `http://127.0.0.1:4142/v1/chat/completions` | `RECALL_EVAL_API_URL` | LLM API 地址 |
| `hindsight_url` | `http://127.0.0.1:9177/v1/default/banks/hermes/memories/search` | `RECALL_EVAL_HINDSIGHT_URL` | Hindsight API 地址 |
| `batch_size` | `10` | `RECALL_EVAL_BATCH_SIZE` | 批处理大小 |
| `log_level` | `INFO` | `RECALL_EVAL_LOG_LEVEL` | 日志级别 |
| `output_mode` | `human` | `RECALL_EVAL_OUTPUT_MODE` | 输出模式（human/json） |

所有字段均可通过 `RECALL_EVAL_*` 环境变量覆盖。API Key 通过 `LITELLM_MASTER_KEY` 环境变量注入。

## 数据集格式

支持两种 JSON 格式：

**数组格式（简单）：**
```json
[
  {"query_id": "q1", "query": "问题内容", "category": "semantic"},
  {"query_id": "q2", "query": "另一个问题", "category": "debug"}
]
```

**对象格式（带元数据）：**
```json
{
  "name": "数据集名称",
  "queries": [
    {
      "query_id": "q1",
      "query": "问题内容",
      "category": "semantic",
      "expected_context": "预期上下文",
      "expected_answer": "预期回答"
    }
  ]
}
```

## 开发

```bash
# 安装依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -x -q

# 代码检查
ruff check src/ tests/
```

## Feature Flag

- 评估是**离线**运行的，不影响主流程
- 默认使用**启发式规则**评估，无需 LLM 即可运行
- 加 `--llm` 参数才调用 LLM 进行更精确的评估
