---
name: memory-correction
description: 记忆修正 — 标记/取消/搜索 Hindsight 记忆单元 + Hermes MEMORY.md 协同修正，确保双存储一致
version: 1.2.0
related_skills: [clustering-analysis, knowledge-navigation]
---

# 记忆修正工具

> 入口脚本：`~/.hermes/scripts/clustering-analysis-v3/scripts/mark_memory.py`
> 调用方式：`cd ~/.hermes/scripts/clustering-analysis-v3 && python3 scripts/mark_memory.py <子命令> [参数]`

> ⚠️ **默认只预览不写入**：`mark` / `unmark` 默认 dry-run（仅显示将会如何修改），加 `--apply` 才实际写入 Hindsight + Hermes。
> 这与 `cli.py` 的设计一致。

## 为什么需要记忆修正

Hermes 有两处记忆存储：

| 存储 | 位置 | 格式 |
|:-----|:-----|:-----|
| **Hindsight** | memory_units 表 (PG) | 每条一个 UUID，结构化字段 |
| **Hermes Memory** | `~/.hermes/memories/MEMORY.md` | `§` 分隔的纯文本条目 |

两者不一致时，修正了 Hindsight 没修 MEMORY.md，用户仍会说错话。本工具支持 **双存储协同修正**。

## 子命令

### `mark` — 标记记忆

```bash
# 预览标记（默认 dry-run，不写入）
python3 scripts/mark_memory.py mark <unit_id> 错误 "记忆内容过时"

# 实际写入
python3 scripts/mark_memory.py mark <unit_id> 错误 "记忆内容过时" --apply

# 预览两边标记
python3 scripts/mark_memory.py mark <unit_id> 错误 --apply-hermes --keyword "reranker"

# 实际写入两边
python3 scripts/mark_memory.py mark <unit_id> 错误 --apply --apply-hermes --keyword "reranker"
```

标记类型：`错误` / `作废` / `可疑` / `已解决` / `待验证`

### `unmark` — 取消标记

```bash
# 预览取消标记（默认 dry-run）
python3 scripts/mark_memory.py unmark <unit_id>

# 实际移除
python3 scripts/mark_memory.py unmark <unit_id> --apply

# 两边实际移除
python3 scripts/mark_memory.py unmark <unit_id> --apply --apply-hermes --keyword "reranker"
```

### `check` — 检查标记状态

```bash
python3 scripts/mark_memory.py check <unit_id>
# 三种输出：🔖 已标记 / 📄 未标记 / ❌ 未找到
```

### `search` — 搜索 Hindsight 记忆

```bash
python3 scripts/mark_memory.py search 关键词 --limit 10 --preview-len 200
```

### `hermes-search` — 搜索 Hermes MEMORY.md

```bash
python3 scripts/mark_memory.py hermes-search 关键词 --preview-len 200
# [Hermes Memory] 匹配 3 条:
#   [条目42] Reranker 经历了 bge-reranker-v2-m3 → ...
#   [条目156] Hindsight 配置：Embedding 模型为...
```

`🔖` 标记表示该条目已被标记过。

## 典型场景

| 场景 | 命令 |
|:-----|:-----|
| 搜到一条错误记忆，两边都标记 | `python3 scripts/mark_memory.py mark <unit_id> 错误 --apply --apply-hermes --keyword "关键词"` |
| 只想修 MEMORY.md | `python3 scripts/mark_memory.py hermes-search 关键词` → 找到后手动编辑 |
| 纠正误标 | `python3 scripts/mark_memory.py unmark <unit_id> --apply --apply-hermes --keyword "关键词"` |
| 查看有哪些标记 | `python3 scripts/mark_memory.py search error --limit 50` |
| 预览操作影响 | 不加 `--apply`，所有 mark/unmark 默认预览 |

## 技术要点

### UUID 校验
所有 `mark/check/unmark` 命令对 `unit_id` 做 UUID 格式预校验（`8-4-4-4-12` 格式），非法 ID 直接输出友好提示，不会崩溃。

### 幂等保护
- **Hindsight**：`has_mark` 检查后写，已标记的跳过
- **MEMORY.md**：`"[标记:" in entry` 防重复标

### 三态返回
所有查询函数统一返回字符串三态，区分"未找到" / "无标记" / "已标记"，不会混淆：

| 命令 | 返回值 |
|:-----|:-------|
| `check` | `not_found` / `no_mark` / `marked` |
| `mark` | `not_found` / `already_marked` / `success` |
| `unmark` | `not_found` / `no_mark` / `success` |

### MEMORY.md 操作原则
- 关键词匹配：纯英文关键词（如 `reranker`）用 `\b` 单词边界，避免子串误标；中文/其他保持子串匹配
- § 分隔符 split/join 可逆，不破坏文件格式
- 标记追加在条目末尾，不修改原内容
- 取消标记用正则移除，可逆恢复
