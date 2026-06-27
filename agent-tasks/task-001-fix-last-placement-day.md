## 任务：修复 consolidation check_merge() 的 last_placement_day 字段缺失

### 问题
`core/consolidation.py` 中 `_estimate_cooccurrence()` 的 fallback 用了 `last_placement_day`：
```python
diff = abs(sa.get("last_placement_day", 0) - sb.get("last_placement_day", 0))
return max(0.0, 1.0 - diff * 0.1)
```
但 subjects 里没有这个字段，差值恒为 0，共现率恒为 1.0。虽然 `a_count + b_count < 20` 门限阻止了误合并，但逻辑是错误的。

### 改动要求
修改 `_estimate_cooccurrence()` 的 fallback 分支，当 `last_placement_day` 字段不存在时直接返回 0（不触发合并建议），而非用默认值 0 算出共现率 1.0。

### 文件
`knowledge-tree-builder/src/knowledge_tree_builder/core/consolidation.py`

### 验证
改完后运行 `consolidate run --dry-run` 不应出现大量 merge_suggestions。
