# mark_memory.py 已知 Bug 与边界情况

> 记录于 2026-06-05 三轮迭代测试，skill v1.2.0

## 未修复 Bug

### Bug 6 [P3] — PG 搜索 ILIKE 未转义 LIKE 通配符

**位置**：`mark_memory.py:301,312` — `cmd_search()` 的 SQL 查询

```python
cur.execute("SELECT COUNT(*) FROM memory_units WHERE text ILIKE %s",
             (f"%{args.keyword}%",))
```

**问题**：`args.keyword` 直接嵌入 LIKE 模式。若关键词含 `%` 或 `_`，PG 会解释为通配符：
- `%` 匹配任意字符序列
- `_` 匹配单个字符

例如 `search "100%"` 会误匹配 `"100x"`、`"100abc"` 等。

**修复方向**：构造 LIKE 模式前转义：

```python
escaped = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
pattern = f"%{escaped}%"
```

## 已修复问题清单

| Bug | 修复内容 | 版本 | 验证状态 |
|-----|---------|------|---------|
| Bug 1 | 调用方式 `python3 -m` → `python3 scripts/` | v1.2.0 | ✅ |
| Bug 2 | 添加 `--apply` 标志，默认 dry-run | v1.2.0 | ✅ |
| Bug 3 | 路径统一到 `/mnt/d/HermesProject/` | v1.2.0 | ✅ |
| Bug 4 | `search` 添加总数显示 `（DB 中共 X 条）` | v1.2.0 | ✅ |
| Bug 5 | 英文关键词 `\b` 单词边界匹配 (`_keyword_matches`) | v1.2.0 | ✅ |
