## 任务：同步 + 修复 consolidation check_merge() 的 last_placement_day

### 改动内容
修改 `scripts/knowledge-tree-builder/src/knowledge_tree_builder/core/consolidation.py` 的 `_estimate_cooccurrence()` 方法（约 516 行附近）。

把这段：
```python
        # fallback：基于 placement 时间接近度估算
        diff = abs(sa.get("last_placement_day", 0) - sb.get("last_placement_day", 0))
        return max(0.0, 1.0 - diff * 0.1)
```

改成：
```python
        # fallback：无使用日志时返回 0（不触发合并）
        # last_placement_day 字段不存在（subjects 字典中没有该字段），
        # 因此不再基于虚构的时间差做无意义估算
        return 0.0
```

### 步骤
1. 在 `D:\HermesProject\scripts\knowledge-tree-builder\src\knowledge_tree_builder\core\consolidation.py` 里做上述修改
2. 运行 deploy 部署到 WSL
3. 告诉我部署完成
