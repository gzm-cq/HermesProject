# memory-cleanup 安全修复 SPEC

## 项目
`scripts/memory-cleanup/src/memory_cleanup/`

## 背景
OpenCode 审查发现 1 个 P0（任意代码执行）、4 个 P1（数据完整性/安全漏洞）、若干 P2（死代码/逻辑缺陷）。

---

## P0 — 立即修复：sys.path 注入任意代码执行

**文件**: `adapters/memory_store.py:107-110`
```python
agent_path = self._config.hermes_agent_path
if agent_path not in sys.path:
    sys.path.insert(0, agent_path)
from tools.memory_tool import MemoryStore
```
**问题**: `hermes_agent_path` 可被环境变量 `MEMORY_CLEANUP_HERMES_AGENT_PATH` 覆盖，攻击者可构造恶意路径注入任意 Python 模块。
**修复**: 替换为 `importlib.util.spec_from_file_location` 直接加载，不做路径插入。

---

## P1-1 — 路径白名单校验

**文件**: `config.py` + `adapters/memory_store.py:113-118`
**问题**: `memory_path` 可被环境变量覆盖为任意路径，`execute_cleanup` 的备份写入跟随该路径。
**修复**: 在 `load_config` 中增加 `validate_path()` 函数，路径必须位于预期目录树内（默认为 `~/.hermes/memories/` 或环境变量指定的基目录）。

---

## P1-2 — _retain 检查 HTTP 状态码

**文件**: `adapters/memory_store.py:62-69`
```python
with urllib.request.urlopen(req, timeout=120):
    return True
```
**问题**: 只靠异常判断成败，不检查 HTTP 响应码。
**修复**: 先 `urlopen`，再检查 `r.status`，非 2xx 记录 warning 返回 False。

---

## P1-3 — --quiet 检测改用精确匹配

**文件**: `cli.py:494`
```python
quiet = "--quiet" in sys.argv
```
**问题**: 子串匹配，`-x --quietly` 会误触发。
**修复**: 改为 `sys.argv == ["--quiet"]` 或用 typer 的 `is_flag=True` 标记。

---

## P1-4 — LLM 响应 error 字段检查

**文件**: `adapters/llm_client.py:71-75`
**问题**: `r.json()` 后只检查 `choices` 键，不检查顶层 `error` 字段（部分 API 在 error 时仍返回 200 + choices）。
**修复**: 在返回前检查 `data.get("error")`，有 error 则记录 warning 并 raise。

---

## P2 — 清理死代码 + 统一逻辑

### P2-1: 删除 utils.py 中的死代码
**文件**: `core/utils.py:44-107`
删除 `collect_remove_indices` 和 `validate_corrected_text` 两个未使用的函数。

### P2-2: 统一 _retain timeout
**文件**: `adapters/memory_store.py:233`
`f.result(timeout=30)` 过短，`_retain` 本身有 120s 超时，30s 会误报 timeout。改为 150s。

---

## 执行顺序

1. P0: 修改 `memory_store.py` 的 `execute_cleanup` 加载方式
2. P1-1: 在 `config.py` 加 `validate_path()`，在 `memory_store.py` 调用
3. P1-2: 修改 `_retain` 检查 status code
4. P1-3: 修改 `cli.py` 的 quiet 检测
5. P1-4: 修改 `llm_client.py` 的 error 检查
6. P2-1: 删除 `utils.py` 死代码
7. P2-2: 修改 `_retain_worker` 的 timeout

---

## 验收标准

- `python -m pytest scripts/memory-cleanup/tests/ -v --tb=short` 全量通过
- 无新增 lint 警告（ruff check scripts/memory-cleanup/src/）
- P0 修复后 `sys.path` 不再有动态 insert
