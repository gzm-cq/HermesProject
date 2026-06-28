# memory-cleanup

> Hindsight 记忆清理管线 — LLM 驱动的记忆分类（retain/remove/merge/compress/hindsight/flagged）。
>
> 对 MEMORY.md / USER.md 进行去重、纠错、合并，控制 token 预算。

## 入口

| 入口 | 职责 |
|------|------|
| `src/memory_cleanup/cli.py` | CLI 主入口（typer 实现），两阶段流水线编排 |
| `src/memory_cleanup/__main__.py` | 支持 `python -m memory_cleanup` 调用 |
| `config/default.yaml` | 默认配置 |
| `pyproject.toml` | 包定义，注册 `memory-cleanup` `memory-classify-v6` 两个 CLI 命令 |

## 目录结构

```
src/memory_cleanup/
├── cli.py              # CLI 入口，两阶段流水线（Phase 1 LLM 分类 → Phase 2 验证 → 执行）
├── config.py           # 配置管理（YAML + ENV 覆盖）
├── __init__.py         # 包元信息（v6.0.0）
├── __main__.py         # python -m 支持
├── adapters/
│   ├── llm_client.py   # LLM 客户端（批量调用 + token 计数）
│   ├── memory_store.py # MemoryStore 读写 + Hindsight API 执行
│   └── session_db.py   # SessionDB 适配器（Phase 2 去重参考）
└── core/
    ├── classifier.py   # Phase 1 分类引擎 + remove 候选筛选
    ├── prompts.py      # LLM 提示词模板
    ├── reporter.py     # 报告输出（human/json）
    └── verifier.py     # Phase 2 验证（LLM 二次确认 remove 决策）
```

## 使用

```bash
# 直接在项目目录运行
cd scripts/memory-cleanup && python -m memory_cleanup run

# 安装后直接调用
memory-cleanup run

# 执行清理（默认 dry-run）
memory-cleanup run --apply

# 多轮投票（cron 推荐 2 轮）
memory-cleanup run --vote 2

# JSON 输出
memory-cleanup run --json
```

### 参数

| 参数 | 说明 |
|------|------|
| `--apply` | 实际执行清理（默认 dry-run，只分类不修改） |
| `--dry-run` | 默认行为，只预览不写入 |
| `--vote N` | 投票轮数（>1 时 remove 并集、其他决策取交集） |
| `--json` | JSON 格式输出结果 |
| `--config PATH` | 指定配置文件路径 |
| `--log-level LEVEL` | 日志级别（DEBUG/INFO/WARNING） |

## 分类

| 类别 | 说明 |
|------|------|
| `retain` | 保留（无需处理） |
| `remove` | 删除（重复/过时/无关内容） |
| `merge` | 合并（多个相关条目合并为一条） |
| `compress` | 压缩（浓缩核心事实，保留关键信息） |
| `hindsight` | 降级到 Hindsight RAG（USER.md 中移除但写入 hindsight 记忆） |
| `flagged` | 需人工审查（LLM 不确定的条目） |

## Cron

每日 13:00 执行 dry-run（`daily_dryrun.sh` → `cron_common.sh` 包装）。

## 配置

| 键 | 默认值 | ENV 覆盖 | 说明 |
|----|--------|----------|------|
| `memory_path` | `/root/.hermes/memories/MEMORY.md` | `MEMORY_CLEANUP_MEMORY_PATH` | MEMORY.md 路径 |
| `user_path` | `/root/.hermes/memories/USER.md` | `MEMORY_CLEANUP_USER_PATH` | USER.md 路径 |
| `session_db_path` | `/root/.hermes/state.db` | `MEMORY_CLEANUP_SESSION_DB_PATH` | SessionDB 路径 |
| `hermes_agent_path` | `/root/.hermes/hermes-agent` | `MEMORY_CLEANUP_HERMES_AGENT_PATH` | Hermes Agent 路径 |
| `llm_url` | `http://127.0.0.1:4142/v1/chat/completions` | `MEMORY_CLEANUP_LLM_URL` | LLM API 端点 |
| `llm_model` | `s-deepseek-v4-flash` | `MEMORY_CLEANUP_LLM_MODEL` | LLM 模型名 |
| `hindsight_url` | `http://127.0.0.1:9177/v1/default/banks/hermes/memories` | `MEMORY_CLEANUP_HINDSIGHT_URL` | Hindsight API 端点 |
| `batch_size` | `10` | `MEMORY_CLEANUP_BATCH_SIZE` | MEMORY 批处理大小 |
| `user_batch_size` | `10` | `MEMORY_CLEANUP_USER_BATCH_SIZE` | USER 批处理大小 |
| `vote_count` | `1` | `MEMORY_CLEANUP_VOTE_COUNT` | LLM 投票轮数 |
| `memory_char_limit` | `50000` | `MEMORY_CLEANUP_MEMORY_CHAR_LIMIT` | MEMORY 字符上限 |
| `user_char_limit` | `15000` | `MEMORY_CLEANUP_USER_CHAR_LIMIT` | USER 字符上限 |
| `log_level` | `INFO` | `MEMORY_CLEANUP_LOG_LEVEL` | 日志级别 |
| `output_mode` | `human` | `MEMORY_CLEANUP_OUTPUT_MODE` | 输出模式（human/json） |

所有字段均可通过 `MEMORY_CLEANUP_*` 环境变量覆盖。API Key 通过 `LITELLM_MASTER_KEY` 环境变量注入。

## 部署

```bash
./deploy/deploy.sh deploy memory-cleanup --yes
```

## 工作流

1. **Phase 1** — LLM 对 MEMORY.md / USER.md 逐条分类为 6 类（retain/remove/merge/compress/hindsight/flagged）
2. **Phase 2** — LLM 对 remove 候选做二次验证，标记为 correct（确认删除）、corrected（修正后删除）、keep（保留）
3. **执行** — 按验证结果执行删除/合并/压缩/降级操作，更新 MemoryStore 和 Hindsight

MEMORY 和 USER 并行处理：MEMORY Phase 1 完成后立即启动 Phase 2，不等 USER。

## 依赖

- LLM API（分类和验证需要，通过 LiteLLM 网关）
- Hermes MemoryStore（`tools/memory_tool.py`）
- Hindsight API（USER.md 降级条目写入记忆库）
- SessionDB（Phase 2 去重参考）
