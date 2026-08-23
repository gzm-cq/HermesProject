# p2-eval

> P2 理念验证 — MemSkill 自适应召回 + EvoAgentX 组合优化（数据飞轮增强方案 §4，仅借鉴理念，不拷贝源码）。

> ⚠️ **不接入生产召回链路**：本脚本是理念层面的对比分析。生产仍用 `skill_matcher` 的三级筛选；如需落地自适应召回，应在 `skill_matcher._execute_recall` 中按 query 分类动态调整 `_prescreen_top_k` / `_embedding_top_k`。

## 功能

1. **MemSkill 理念**：按 query 类型（`kw_heavy` 关键词明确 / `sem_heavy` 语义模糊）自适应调整 keyword/embedding 召回权重，用 50 条合成 eval 对比「固定权重」vs「自适应权重」的命中率
2. **EvoAgentX 理念**：给出 worker profile 的 skill 组合优化建议（启发式报告）

## 用法

```bash
python concept_eval.py              # 运行理念验证对比 + 组合优化建议
python concept_eval.py --json       # JSON 输出
```

## 测试

```bash
cd scripts/p2-eval
python -m pytest tests/
```

> 本目录为单文件理念验证工具，无独立部署清单。
