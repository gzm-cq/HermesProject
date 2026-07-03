# CRON 环境变量兜底机制

> **日期**: 2026-07-03
> **状态**: ✅ 已实施 + 已部署
> **关联**: [flywheel-cron-restructure-spec](flywheel-cron-restructure-spec.md) (CRON-03)、[skill-matcher-eval-spec](skill-matcher-eval-spec.md)

## 问题背景

Hermes cron 任务以 `no_agent: true` 方式运行，没有 shell profile（~/.bashrc 不加载），导致 `os.environ.get()` 拿不到 `~/.hermes/.env` 中定义的 API key 和其他环境变量。

**表现**: Skill Eval 退化 28%，F1 从 0.4367 降至 0.3133，15/30 条 query 返回空列表，延迟 ~32s。

## 根因

### 直接根因：LLM Timeout 不足

- `_LLM_TIMEOUT = 15` 秒对 `s-deepseek-v4-flash` 处理 353 个 skill 的大 prompt（~13K tokens）不够
- 代码逻辑 `for attempt in range(2)` 即 2 次重试，15s × 2 = 30s + 网络开销 ≈ 32s
- 超时后 `except Exception` 捕获 → 返回空列表 → P=0, R=0, F1=0

### 潜在根因：cron 环境 env var 缺失

- cron 进程没有 `~/.hermes/.env` 中的 `LITELLM_MASTER_KEY` 等变量
- Gateway 进程通过 systemd `EnvironmentFile` 注入是正常的，但 cron 脚本没有
- 当前环境下 key 通过其他方式传入，但未来 cron 环境可能完全缺失，需要兜底

## 解决方案：三层防御

### 第 1 层：Shell 层 — cron_common.sh source .env

`scripts/cron_common.sh` 顶部（`set -euo pipefail` 之后）:

```bash
_HERMES_ENV_FILE="${HERMES_ENV_FILE:-$HOME/.hermes/.env}"
if [[ -f "$_HERMES_ENV_FILE" ]]; then
    set -a
    source "$_HERMES_ENV_FILE"
    set +a
fi
```

- `set -a`：自动 export 所有后续赋值的变量
- 使用 `${HERMES_ENV_FILE:-...}` 允许外部覆盖路径
- 所有 source cron_common.sh 的 cron 脚本自动获得 env var

### 第 2 层：Python 层 — env_loader.py 兜底

新建 `src/knowledge_navigation/core/env_loader.py`，提供 `get_env()` 替代 `os.environ.get()`:

```python
@lru_cache(maxsize=1)
def _read_env_file() -> dict[str, str]:
    """从 ~/.hermes/.env 读取 KEY=VALUE，跳过注释和空行。"""

def get_env(key: str, default: str = "") -> str:
    """优先 os.environ，兜底 ~/.hermes/.env。"""
```

- `@lru_cache(maxsize=1)` 避免重复读取文件
- 文件不可读时静默返回空，不影响上层
- 如果未来 .env 加密，此层会静默失败，由 Shell 层兜底

### 第 3 层：Systemd 层 — 已有

- `hermes-gateway.service` 已通过 `EnvironmentFile=/root/.hermes/.env` 注入变量
- Gateway 进程正常运行时不需要修改

## 涉及文件

| 文件 | 变更内容 |
|:----|:--------|
| `scripts/cron_common.sh` | 添加 `source ~/.hermes/.env` 块 |
| `src/knowledge_navigation/core/env_loader.py` | 新建，env 兜底加载器 |
| `src/knowledge_navigation/core/skill_matcher.py` | `_LLM_TIMEOUT` 15→30（可配 `KN_SKILL_MATCH_TIMEOUT`）；4处 `os.getenv` → `get_env` |
| `src/knowledge_navigation/core/router.py` | 3处 `os.getenv` → `get_env`（`_fetch_api_key`、`KN_ROUTER_TIMEOUT`） |
| `src/knowledge_navigation/core/hooks.py` | 2处 `os.environ.get` → `get_env`（`SILICONFLOW_API_KEY`、`KT_DB_URL`） |
| `src/knowledge_navigation/__init__.py` | 1处 `os.environ.get` → `get_env`（`KT_PLUGIN_SRC`） |
| `scripts/run_skill_eval.py` | format bug 修复（`:+d` → `:+.0f`） |

## 效果验证

| 指标 | 修复前 | 修复后 | 变化 |
|:-----|:------:|:------:|:----:|
| F1@3 | 0.3133 | 0.5145 | +64% |
| 空返回 | 15/30 | 0/30 | 消除 |
| 平均延迟 | 19,957ms | 6,367ms | -68% |

## .env 加密场景处理

当前 `.env` 文件是明文存储，Hermes 工具层（terminal/grep）会自动遮蔽 key 值显示。如果未来 Hermes 引入 .env 加密:

1. Shell 层 `source` 会失败 — 需要 Hermes 提供解密后的 env 加载机制
2. Python 层 `env_loader.py` 也会静默失败 — 返回空值不影响上层逻辑
3. Systemd 层 `EnvironmentFile` 同样会失败 — 需要改为 `ExecPre` 调用解密命令

**结论**: 三层中任何一层能拿到 env var 即可工作。加密场景需要至少一层适配。

## 后续建议

- [ ] `config.py` 中 40+ 个 `os.getenv` 可批量替换为 `get_env`（当前优先级低，Gateway 进程 env 可用）
- [ ] `scripts/collect_baseline.py` 和 `generate_eval_queries.py` 中的 env 引用可替换
- [ ] `KN_SKILL_MATCH_TIMEOUT` 可加入 `~/.hermes/.env` 中显式配置
