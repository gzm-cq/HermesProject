# Commands 目录 - CLI 子命令拆分计划

## 概述

本目录用于存放拆分后的 CLI 子命令模块。当前 `cli.py` 文件有 2183 行，包含 21 个子命令，远超 800 行上限。拆分计划将在冻结期实施。

## 拆分计划

### 第一阶段：高复杂度子命令（优先拆分）

| 子命令 | 行数估算 | 目标文件 | 说明 |
|--------|----------|----------|------|
| `run` | 428 行 | `commands/run.py` | 管线运行命令，最复杂 |
| `consolidate` | ~200 行 | `commands/consolidate.py` | 纠错回路命令 |
| `validate` | ~150 行 | `commands/validate.py` | LLM 结构校验（已废弃） |

### 第二阶段：中等复杂度子命令

| 子命令 | 目标文件 | 说明 |
|--------|----------|------|
| `add` | `commands/add.py` | 单条知识点添加 |
| `tree` | `commands/tree.py` | 知识树可视化 |
| `find` | `commands/find.py` | 知识点搜索 |
| `move` | `commands/move.py` | 节点移动 |
| `rename` | `commands/rename.py` | 节点重命名 |

### 第三阶段：低复杂度子命令

| 子命令 | 目标文件 | 说明 |
|--------|----------|------|
| `init-db` | `commands/db.py` | 数据库初始化 |
| `drop-db` | `commands/db.py` | 数据库清空 |
| `backfill-k-vectors` | `commands/backfill.py` | K向量回填 |
| `redistribute` | `commands/redistribute.py` | 领域重分类 |
| `review` | `commands/review.py` | 审查队列管理 |

## 拆分后结构

```
src/knowledge_tree_builder/
├── cli.py              # 入口文件（导入并注册子命令）
├── commands/           # 子命令目录
│   ├── __init__.py
│   ├── run.py          # run 命令
│   ├── consolidate.py  # consolidate 命令
│   ├── add.py          # add 命令
│   ├── tree.py         # tree 命令
│   ├── find.py         # find 命令
│   ├── db.py           # init-db / drop-db
│   ├── backfill.py     # backfill-k-vectors
│   ├── redistribute.py # redistribute
│   └── review.py       # review
│   └── deprecated/     # 已废弃命令
│       ├── validate.py # validate（旧管线）
│       ├── cluster.py  # cluster（旧管线）
│       └── name.py     # name（旧管线）
```

## 拆分原则

1. **保持接口不变**：所有子命令的参数和功能保持不变
2. **共享函数提取**：将重复的辅助函数（如 `_scan_articles`）移到 `cli_utils.py`
3. **导入优化**：使用 `from commands import run, tree, find` 避免重复导入
4. **测试覆盖**：每个拆分后的命令文件需有对应的单元测试

## 实施时机

- **当前状态**：准备工作已完成（目录结构、拆分计划）
- **建议时机**：下一个冻结期（无活跃开发时）
- **风险控制**：拆分后立即运行全量测试，确保功能无损

## 参考

- 审查报告：P1-1 cli.py 2183 行 > 800 行上限
- 建议策略：渐进式拆分，优先处理最复杂的子命令