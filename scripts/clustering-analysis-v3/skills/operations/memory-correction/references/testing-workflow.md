# mark_memory.py 测试工作流

> 2026-06-05 迭代部署测试记录，版本历：09:41 → 10:03 → 10:11 → 10:18

## 完整测试脚本

```bash
# 测试用 UUID（使用前确认未标记）
UUID="a1c0be9c-c0a3-4e7f-809b-2a3fcfe62cc2"
DB="CLUSTERING_DB_URL=postgresql://postgres:***@127.0.0.1:5434/hindsight"
DIR="~/.hermes/scripts/clustering-analysis-v3"

cd $DIR

# 1. 基线
python3 scripts/mark_memory.py check $UUID

# 2. search 子命令
python3 scripts/mark_memory.py search "reranker" --limit 3 --preview-len 80

# 3. hermes-search 子命令
python3 scripts/mark_memory.py hermes-search "reranker" --preview-len 60

# 4. mark + check + 幂等 + unmark 往返
python3 scripts/mark_memory.py mark $UUID 可疑 "测试标记"
python3 scripts/mark_memory.py check $UUID          # 应显示 🔖
python3 scripts/mark_memory.py mark $UUID 可疑 "重复" # 应显示 ⏭️ 跳过
python3 scripts/mark_memory.py unmark $UUID
python3 scripts/mark_memory.py check $UUID          # 应显示 📄

# 5. 默认 dry-run（不加 --apply 只预览）
python3 scripts/mark_memory.py mark $UUID 可疑 "dryrun"
python3 scripts/mark_memory.py unmark $UUID

# 6. --apply-hermes 全链路（默认预览）
python3 scripts/mark_memory.py mark $UUID 可疑 "全链路" --apply-hermes --keyword reranker    # 默认预览，加 --apply 才写入

# 7. 实际标记+取消 + 格式验证
cp /root/.hermes/memories/MEMORY.md /root/.hermes/memories/MEMORY.md.bak
python3 scripts/mark_memory.py mark $UUID 可疑 "格式" --apply-hermes --keyword reranker
python3 scripts/mark_memory.py unmark $UUID --apply-hermes --keyword reranker
diff /root/.hermes/memories/MEMORY.md /root/.hermes/memories/MEMORY.md.bak | wc -l  # 期望值 ≈2-3
rm /root/.hermes/memories/MEMORY.md.bak

# 8. 边界情况
python3 scripts/mark_memory.py check "not-a-uuid"              # 非法 UUID
python3 scripts/mark_memory.py check "00000000-0000-0000-0000-000000000000"  # 不存在
python3 scripts/mark_memory.py search "XYZNOTEXIST123"          # 无结果
```

## 常见失败模式

### 1. UnicodeEncodeError / emoji 打印崩溃
**现象**：`UnicodeEncodeError: surrogates not allowed`
**原因**：print 语句中使用 `\ud83d\udcc4`（📄 的 UTF-16 surrogate pair）
**修复检查**：grep 这 4 行，确认使用了实际字符 `📄` 而非 surrogate pair：
- `mark_memory()` 第 99 行
- `unmark_memory()` 第 124 行
- `mark_hermes_memory()` 第 179 行
- `unmark_hermes_memory()` 第 200 行

### 2. MEMORY.md 格式破坏
**现象**：diff 显示 40+ 行差异，`§` 位置改变
**原因**：`_read_hermes_entries`/`_write_hermes_entries` 的 separator 不正确
**正确设置**：`SEPARATOR = "\n\u00a7\n"`，写回用 `SEPARATOR.join(entries) + "\n"`
**错误设置**：单 `§` 字符或 `"\u00a7"`，导致换行位置错乱

### 3. 预览模式未同步（dry_run 只传了一侧）
**现象**：预览模式标记了 Hindsight 但跳过了 Hermes（或反之）
**原因**：`dry_run` 参数只传给了其中一侧
**正确设计**：`cmd_mark()` 和 `cmd_unmark()` 中将 `dry` 同时传给 `mark_memory(conn, ..., dry_run=dry)` 和 `mark_hermes_memory(..., dry_run=dry)`

### 4. 空 note 产生多余空格
**现象**：标记文本尾部有孤立的空格
**原因**：`f" {note}"` 在 `note=""` 时仍执行
**修复**：`if note and note.strip():`

## 验证要点

每次测试后确认：
- [ ] Hindsight 侧 `check` 状态正确（未标记 / 已标记 / 未找到）
- [ ] MEMORY.md diff 行数合理（标记后增 N 行，取消后 ≈0-2 行）
- [ ] 不加 `--apply` 时两边均未改变（diff=0）
