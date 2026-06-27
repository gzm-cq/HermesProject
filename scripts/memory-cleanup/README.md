# memory-cleanup

> Hindsight 记忆清理管线 — LLM 驱动的记忆分类（retain/remove/merge/compress）。
>
> 对 MEMORY.md / USER.md 进行去重、纠错、合并，控制 token 预算。

## 入口

| 文件 | 职责 |
|------|------|
| `memory-classify-v6.py` | 主分类脚本（v6） |
| `compat/memory_classify_v6.py` | 兼容版本 |
| `run.sh` | 执行入口（wrapper） |
| `config/default.yaml` | 配置 |

## 使用

```bash
cd scripts/memory-cleanup && python3 memory-classify-v6.py --apply
```

### 参数

- `--dry-run` — 仅预览不写入
- `--vote 2` — 需要 2 轮 LLM 投票
- `--apply` — 执行写入

## 分类

- `retain` — 保留
- `remove` — 删除（重复/过时）
- `merge` — 合并（关联内容）
- `compress` — 压缩（保持核心事实）

## Cron

每日 13:00 执行 dry-run（`daily_dryrun.sh` → `cron_common.sh` 包装）。

## 依赖

- Hindsight API（分类需要）
- Hermes MemoryStore（`tools/memory_tool.py`）
