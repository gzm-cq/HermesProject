# HermesProject — 工作知识

## Stack

- **Language** — Python 3.10+ (monorepo, 10+ sub-projects under `scripts/` and `plugins/`)
- **CLI** — `typer` (every sub-project has a CLI entry)
- **DB** — PostgreSQL + pgvector (both L4 Hindsight & L5 knowledge tree)
- **LLM** — OpenAI-compatible API (SiliconFlow for embedding, litellm for chat)
- **Key deps** — numpy, psycopg2-binary, scikit-learn, requests, PyYAML

## Layout (top-level)

| Path | What lives there |
|---|---|
| `scripts/knowledge-tree-builder/` | L5 知识树建树管线 (CLI: `knowledge-tree-builder`) |
| `plugins/knowledge-tree-plugin/` | L5 post_llm_call 增量提取 |
| `plugins/knowledge-navigation/` | L4+L5 双域融合 pre_llm_call 插件 |
| `scripts/clustering-analysis-v3/` | L4 Hindsight 聚类+因果链 (CLI: `clustering-analysis`) |
| `scripts/self-evolving/` | 自进化飞轮基线监控 |
| `scripts/memory-cleanup/` | L2/L3 MEMORY.md/USER.md 清理 |
| `scripts/system-health-check/` | 系统健康巡检 cron |
| `libs/hermes_common/` | 统一共享库（F-1 账本 / LLM 护栏 / 文本工具，脚本层与插件层共用） |
| `deploy/` | 统一部署脚本 (`deploy/deploy.sh`) + manifests |
| `plugins/` 根目录 | Hermes gateway plugins (plugin.yaml + __init__.py) |

## Commands

```bash
deploy/deploy.sh list                    # 列出可部署项目
deploy/deploy.sh plan <project>          # 预览部署清单
deploy/deploy.sh deploy <project> --yes  # 部署到 /root/.hermes/

# 子项目 CLI
knowledge-tree-builder run --merged -j 3           # 建树管线
knowledge-tree-builder consolidate run             # 一键维护 (5 阶段)
knowledge-tree-builder backfill-k-vectors          # 回填 k_vector
knowledge-tree-builder redistribute                # 领域重分类

clustering-analysis run --apply                    # 聚类分析 (周一 09:30 cron)
clustering-analysis dedup-memories --dry-run       # MinHash LSH 去重

# 测试 / lint（各子项目独立）
cd scripts/knowledge-tree-builder && pytest -v tests/
cd scripts/knowledge-tree-builder && ruff check src/
cd scripts/knowledge-tree-builder && mypy src/
```

## Conventions

- **提交格式**: `<type>(<scope>): <中文描述>` — `feat`/`fix`/`refactor`/`docs`/`chore`
- **命名规范**: Python `snake_case` for packages/modules/functions, `PascalCase` for classes, `UPPER_SNAKE` for constants
- **测试规范**: `pytest` with `test_*.py` files, coverage target 80%+, external deps must be mocked
- **工程规范**: `docs/engineering-standards.md` governs all sub-projects (src layout, typer CLI, dataclass config, JSON logging, adapter pattern)
- **导入顺序**: stdlib → third-party → local, three groups separated by blank lines
- **知识树节点类型**: `subject` for domain/subject nodes, `knowledge_point` for leaf nodes

## Watch out for

- **pgvector 适配器**: Python 包 `pgvector` 必须安装，否则 VECTOR 列被读为字符串。写入时 `::vector` 显式转换可绕过（`update_k_vector` 已实现）。读取时必须有适配器或用 `_parse_k_vector()` 兜底。
- **sys.path 注入**: `knowledge-navigation` 和 `knowledge-tree-plugin` 通过 `__init__.py` 模块级注入对方 src 路径到 sys.path。部署顺序至关重要：builder → plugin → navigation。
- **numpy ndarray 布尔歧义**: `if some_array:` 在多元素数组上抛 `ValueError`。所有 k_vector 检查必须用 `is not None`。`admit.py`、`incremental.py`、`consolidation.py` 中多处曾踩过这个坑。
- **`deploy/deploy.sh` 覆盖 WSL 直接修改**: Qoder 有时直接在 WSL `/root/.hermes/` 修代码。下次 deploy 会覆盖这些修改，需先同步回 `D:\HermesProject`。
- **kwargs 关键字 **：Hermes 插件 hook 回调中**不能**调用 Hermes 工具/MCP 工具。
