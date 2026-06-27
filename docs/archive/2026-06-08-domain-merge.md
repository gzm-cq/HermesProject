# Domain 合并计划（已整合，计划作废）

> 此计划的逻辑已整合进 `consolidate run --merge-domains`，无需独立执行。
> 保留作为历史参考。

## 原计划

1. 修复 Plugin `get_domain_nodes` node_type='domain' → 'subject' ✅
2. 实现 `merge-domains` 脚本 → 已整合到 `ConsolidationEngine.merge_small_domains()`
3. 注册 CLI 命令 → `consolidate run --merge-domains`（默认开启）

## 当前状态

```bash
# 不需要独立命令，consolidate 已包含
knowledge-tree-builder consolidate run
```
