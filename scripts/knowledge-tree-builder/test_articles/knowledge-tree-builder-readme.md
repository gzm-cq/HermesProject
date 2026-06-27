# Knowledge Tree Builder — 知识分域建树管线

从精读笔记/文章中提取知识点，自动构建二叉树知识树。

## 管线步骤

| Step | 名称 | 说明 |
|:----:|------|------|
| 1 | LLM 知识点提取 | 每篇文章提取 3-8 个关键知识点 |
| 1.5 | 规则准入过滤 | 长度/模糊概括规则兜底过滤 |
| 2 | HDBSCAN 递归聚类 | 自底向上聚类 + 建树报告 + 自动干跑 |
| 3 | LLM 结构校验 | 子簇关系判断（平行/上下位） |
| 4 | LLM 节点命名 | 科目名 / 知识点名 |
| 5 | 写入 PG | 树写入 + 去重 + 矛盾检测 |

## 快速开始

```bash
# 完整 dry-run（不写 PG）
cd scripts/knowledge-tree-builder
python -m knowledge_tree_builder.cli run --dry-run

# 实际写入
python -m knowledge_tree_builder.cli run --apply
```

## 项目结构

```
src/knowledge_tree_builder/
├── cli.py                     # typer CLI 入口
├── config.py                  # YAML 配置加载
├── adapters/database.py       # PG 读写层
├── core/
│   ├── extractor.py           # Step 1: LLM 知识点提取
│   ├── admission.py           # Step 1.5: 规则准入
│   ├── clustering.py          # Step 2: HDBSCAN 递归聚类
│   ├── validator.py           # Step 3: LLM 结构校验
│   ├── namer.py               # Step 4: LLM 节点命名
│   ├── writer.py              # Step 5: 写入 PG + 去重
│   ├── incremental.py         # 增量放置 + Q/K 向量管理
│   ├── consolidation.py       # 纠错回路
│   └── embeddings.py          # Embedding API 调用
└── llm/client.py              # LLM API 调用
```
