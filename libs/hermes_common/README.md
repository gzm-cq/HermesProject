# hermes_common

> Hermes 统一共享库 — 跨「脚本层（scripts/*）」与「插件层（plugins/*）」复用的纯依赖工具集中地。

## 组成

| 模块 | 职责 |
|------|------|
| `ledger.py` | F-1 统一反馈账本（`append_ledger_event`，零依赖） |
| `llm_guard.py` | 所有 LLM 调用的统一护栏（解析 / 重试 / 退避 / 限速，零第三方依赖） |
| `text_utils.py` | 关键词提取（`extract_keywords`）、CJK 处理（`CJK_STOP_CHARS`） |

## 用法

消费方将本包父目录（开发态 `libs/hermes_common` 或生产态 `/root/.hermes/lib`）注入 `sys.path` 后，以 `from hermes_common.xxx import ...` 使用。

统一入口（唯一 bootstrap）：

- `ensure_on_path()` — 幂等地把本包父目录注入 `sys.path`（开发/生产双路径自定位）
- `bootstrap()` — `ensure_on_path()` + 失败即 raise（缺包哨兵），供消费方在 import 前调用

消费方推荐统一样板：

```python
try:
    from hermes_common import bootstrap
except ImportError:
    # 兜底：直接注入父目录再导入
    ...
bootstrap()
from hermes_common.ledger import append_ledger_event
```

## 部署

```bash
cd /mnt/d/HermesProject && bash deploy/deploy.sh deploy hermes-common --yes
```

生产部署位置为 `/root/.hermes/lib/hermes_common`（flat 布局：`__init__.py`、`ledger.py`、`llm_guard.py`、`text_utils.py`）。采用纯路径注入方式（无 pip 安装，`pyproject.toml` 仅用于测试环境），生产缺失该库时 `bootstrap()` 立即抛错，防止账本静默失效。
