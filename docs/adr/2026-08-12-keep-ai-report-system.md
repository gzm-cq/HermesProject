# ADR: 保留 ai-report-system（md→docx 导出工具）

## 背景

2026-08-12 清理过程中，提交 `a99fbfe` 曾将 `scripts/ai-report-system/` 标记为
"废弃原型"并从仓库删除，随后同步清理了生产副本与部署物料。用户指出该判断有误，
我们经核实后完整恢复（提交 `1894500`）。

## 为什么不该删

1. **它是真实在用的工具**：配合 `sn-deep-research`，提供 Markdown → DOCX 导出能力
   （`export_docx.py`、`docx_exporter.py`、`chart_renderer.py`、`docx_comments.py` 批注提取 + 全套测试）。
2. **生产 30+ 个 skill 仍引用它**：`md2docx`、`md-to-docx`、`docx-infographic-pipeline`、
   `ai-report-generation-system-implementation` 等依赖其导出能力。删掉即断链。
3. **有持续的功能演进提交**，不是半成品原型。

## 教训（后续"删除/清除"类动作必遵）

- 不能只看 git 提交说明里的"废弃""原型"标签就动手。
- 删除前必须先**扫描生产引用**（skills / cron / 运行代码 / 部署物料），确认无在用依赖。
- 删除属不可逆高风险动作，需先落档引用清单 + 备份，再执行。

## 状态

已完整恢复（源码 / 部署物料 / 生产副本 / 文档），提交 `1894500`。
`a99fbfe` 原文保留为历史记录，不重写远端历史。

## 附：backfill-k-vector 经核实无需恢复（2026-08-12）

同日清理还删除了 `scripts/backfill-k-vector/`（提交 `93a266a`）。经逐项核实，该删除
**正确、无需恢复**，与 ai-report-system 误删不同：

1. **被删的是早期 standalone 脚本**：`backfill_k_vector.py`（201 行，一次性补跑工具）。
2. **功能已被原生命令完整吸收且更完善**：`knowledge-tree-builder` 的 `backfill-k-vectors`
   命令（`knowledge_tree_builder/scripts/backfill_k_vectors.py`）逐行覆盖其逻辑——
   `_parse_k_vector` 解析、子节点向量递归平均、subject 无子节点用 name embed 兜底全部一致；
   且新增分批、`DISTINCT` 去重、幂等、`--dry-run`，对 knowledge_point 用真实内容
   （JOIN `knowledge_point_texts`）而非仅 name，向量更准。
3. **cron 用的是原生命令，与被删脚本无关**：k-vector 维护 wrapper
   `knowledge-tree-kvector-maintenance.sh` 调用 `python3 -m knowledge_tree_builder.cli backfill-k-vectors`，
   调度不受影响。

结论：backfill-k-vector 属"被原生流程取代"的真实删除，不恢复。

## 附：clustering-analysis-v3 残留文件核实（2026-08-12）

同日核实生产侧 `scripts/clustering-analysis-v3/` 的旧原型残留，结论分两类：

**承重文件（已恢复到 git，提交 `62fbe9f`）**：`14d0056` 曾误删生产活跃的聚类管线脚本，
导致 git 与生产出现部署 drift。经 cron 调用链路逐层核实，以下 6 个文件为活跃依赖，
已从 `14d0056^` 恢复：
- `scripts/cron_wrapper.sh`：周聚类 cron 核心编排器（新 wrapper 直接调用）
- `scripts/memory_quality_report.py` / `long_memory_governance.py` / `dedup_minhash.py`：
  管线步骤①/②/③，被 cron_wrapper 直接调用
- `scripts/mark_memory.py`：被 `src/clustering_analysis/cli.py` `importlib` 导入，
  且 `operations/memory-correction` skill 依赖
- `config/default.yaml`：long_memory_governance 默认配置

**死文件（已从生产清理，WSL 备份于 `/root/.hermes/backups/clustering-cleanup-20260812/`）**：
经全仓引用扫描确认无活跃调用，可安全删除：
- `compat/clustering_analysis_v3/`：新管线用 `clustering_analysis` 包，旧兼容层无导入
- `scripts/dedup_long_memories.py`、`scripts/collect_baseline_delta.sh`：仅 review/文档提及
- `skills/mlops/clustering-analysis/`：活跃 skill 为 `operations/memory-correction`
- `README.md`、`pyproject.toml`（`uv.lock` 生产本就不存在）

同时清理部署 manifest 中已失效的 `compat/` 引用。清理后已通过生产验证：
4 个步骤脚本语法编译 OK、`cli.run + mark_memory` 导入 OK。