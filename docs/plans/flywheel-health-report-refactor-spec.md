# flywheel-health-report 重构 Spec

> **状态**: 待评审
> **日期**: 2026-08-02
> **作者**: Hermes Team

---

## 1. 背景与问题

### 现状

| 文件 | 行数 | 说明 |
|------|------|------|
| `scripts/cron-wrappers/flywheel-health-report.py` | 2188 行 | 单文件包含 11 个分析器 + 报告生成 + 推荐生成 + 主入口 |
| `scripts/cron-wrappers/flywheel-health-report.sh` | 111 行 | bash wrapper（调 Python + 飞书通知 + 末尾调 auto-tuner） |
| `scripts/cron-wrappers/auto-tuner.py` | 1077 行 | 参数自优化调优器（纯 Python，含完整闭环） |
| `scripts/cron-wrappers/auto-tuner.sh` | 28 行 | 薄 wrapper |
| `scripts/cron-wrappers/tests/test_flywheel_health_report.py` | 40 tests | 使用 importlib 按文件路径加载 |

### 问题

1. **单文件过大**：2188 行塞了 11 个分析器 + 报告 + 推荐 + 入口，修改风险高
2. **测试加载方式丑陋**：因脚本不在 Python package 内，测试用 `importlib.util.spec_from_file_location` 按路径加载，所有测试通过 `fhr.xxx` 前缀访问
3. **auto-tuner 与 flywheel 分离**：两者共享路径常量（`HERMES_HOME`、`data/flywheel/`），且 `.sh` 末尾总是链式调用 auto-tuner，分离部署增加维护成本
4. **部署耦合**：flywheel 和 auto-tuner 通过 `cron-wrappers.sh` 整体部署到 `/root/.hermes/scripts/` flat 目录，与其他独立项目（clustering-analysis-v3、memory-cleanup）的结构不一致

---

## 2. 目标

1. 拆 2188 行单文件 → 15+ 个模块，每个分析器独立文件，可单独 import 和测试
2. auto-tuner 合并进同一个包，共用 config，减少部署碎片
3. 测试从 `importlib` 改为正常 `from flywheel_health_report import ...`
4. 独立成 deploy 项目，从 cron-wrappers 移除
5. **cron 调度入口路径不变**：`/root/.hermes/scripts/flywheel-health-report.sh` 仍存在（内容改为调用包内模块）
6. **auto-tuner 调用路径变更**：从 `/root/.hermes/scripts/auto-tuner.sh` 改为 `/root/.hermes/scripts/flywheel-health-report/scripts/auto-tuner.sh`（由 flywheel-health-report.sh 内部调用，不暴露给 cron）
7. **函数签名不变**：只挪位置，不重构逻辑
8. **auto-tuner 数据文件路径不变**：state/log/pause/env 文件仍在 `/root/.hermes/data/flywheel/` 和 `/root/.hermes/.env`

---

## 3. 最终项目结构

```
scripts/flywheel-health-report/
├── pyproject.toml                    # 包声明（name="flywheel-health-report"）
├── README.md                         # 项目说明（最后补）
├── src/
│   └── flywheel_health_report/
│       ├── __init__.py               # 空文件，标记 package
│       ├── __main__.py               # 支持 python3 -m flywheel_health_report（调用 cli.main）
│       ├── config.py                 # 路径常量、阈值 TH/REC_TH、CRON_TO_FLYWHEEL、ACTIVE_CRON_JOBS、REQUIRED_OUTPUTS、FLYWHEEL_DEPENDENCIES + auto-tuner 路径常量
│       ├── utils.py                  # _percentile, _resolve_trend_arrow, _is_test_query（跨分析器共享工具）
│       ├── parsers.py                # _load_json, _save_json, _load_jsonl, _rotate_jsonl, parse_cron_states, parse_trace_log, scan_cron_log_errors, append_daily_summary, load_daily_summary
│       ├── integrity.py              # check_output_integrity, check_dependency_chain, detect_zombie_state_files, detect_report_type
│       ├── analyzers/
│       │   ├── __init__.py
│       │   ├── cron_jobs.py          # analyze_cron_jobs
│       │   ├── router.py             # analyze_router（从 utils 导入 _percentile）
│       │   ├── skill.py              # analyze_skill_eval, analyze_skill_usage
│       │   ├── token_budget.py       # analyze_token_budget（从 utils 导入 _percentile）
│       │   ├── sag.py                # analyze_sag_contribution
│       │   ├── global_errors.py      # analyze_global_errors
│       │   ├── kt_baseline.py        # analyze_kt_baseline
│       │   ├── memory_cleanup.py     # analyze_memory_cleanup
│       │   ├── clustering.py         # analyze_clustering
│       │   └── kn_baseline.py        # analyze_kn_baseline, analyze_data_credibility
│       ├── auto_tuner/
│       │   ├── __init__.py
│       │   ├── tuner.py              # 主调优逻辑（handle_pending_restart、determine_direction、update_state、select_param_to_tune、main）
│       │   └── notifier.py           # 飞书通知（_send_lark、notify_gateway_restart、notify_restart_reminder）
│       ├── report.py                 # generate_report, format_7day_trend
│       ├── recommendations.py        # generate_recommendations
│       └── cli.py                    # main()（argparse 入口）
├── scripts/
│   ├── flywheel-health-report.sh     # bash wrapper：source cron_common + PYTHONPATH + python3 -m flywheel_health_report.cli + 飞书通知 + 末尾调 auto-tuner
│   └── auto-tuner.sh                 # bash wrapper：source cron_common + PYTHONPATH + python3 -m flywheel_health_report.auto_tuner.tuner "$@"
├── tests/
│   ├── conftest.py                   # pytest fixtures（tmp_hermes_home、sample_trace、sample_cron_states）
│   ├── test_parsers.py               # parse_trace_log, parse_cron_states, _load_json, append/load_daily_summary
│   ├── test_utils.py                 # _percentile, _is_test_query, _resolve_trend_arrow
│   ├── test_integrity.py             # check_output_integrity, check_dependency_chain, detect_zombie
│   ├── test_analyzers/
│   │   ├── __init__.py
│   │   ├── test_cron_jobs.py
│   │   ├── test_router.py
│   │   ├── test_skill.py
│   │   ├── test_token_budget.py
│   │   ├── test_sag.py
│   │   ├── test_global_errors.py
│   │   ├── test_kt_baseline.py
│   │   ├── test_memory_cleanup.py
│   │   ├── test_clustering.py
│   │   └── test_kn_baseline.py
│   ├── test_report.py                # generate_report, format_7day_trend
│   ├── test_recommendations.py       # generate_recommendations
│   └── test_auto_tuner.py            # auto-tuner 闭环逻辑（14 个单元测试迁出）
└── deploy/                           # 不在项目内，参考 deploy/projects/flywheel-health-report.sh
```

### 设计决策

| 决策 | 理由 |
|------|------|
| 新增 `utils.py` | `_percentile` 被 router 和 token_budget 两个分析器共享，放 router.py 会产生跨分析器依赖 |
| `auto_tuner/notifier.py` 拆出 | 通知逻辑（lark-cli 调用）与调优逻辑职责不同，独立文件方便测试 mock |
| `analyzers/kn_baseline.py` 包含 `analyze_data_credibility` | data_credibility 消费 kn_baseline 输出，内聚在同一文件 |
| 不做 `compat/` 目录 | clustering-v3 的 compat 是历史包袱，flywheel 是全新拆分不需要 |
| 不做 `flywheel-health-report.py` 兼容 shim | 无外部调用方直接 `python3 flywheel-health-report.py`，全部通过 `.sh` 入口；YAGNI |
| 新增 `__main__.py` | 支持 `python3 -m flywheel_health_report`（不带 `.cli`），与 Python 惯例一致 |

---

## 4. 模块职责与函数分配

### 4.1 config.py

从原文件 L37-182 搬入全部全局常量：

```python
DEFAULT_HERMES_HOME = "/root/.hermes"
*_SUBPATH          # 9 个子目录相对路径
TH                 # 告警阈值 dict
REC_TH             # 推荐生成阈值 dict
_TEST_QUERY_RE     # 测试查询过滤正则
ACTIVE_CRON_JOBS   # 核心飞轮 cron 任务白名单
EXCLUDED_STATE_FILES  # 非飞轮 state 文件白名单
_CRON_TO_FLYWHEEL  # cron → 飞轮名映射
_FLYWHEEL_ORDER    # 飞轮显示顺序
REQUIRED_OUTPUTS   # 完整性检查必需产出
FLYWHEEL_DEPENDENCIES  # 飞轮依赖链

# auto-tuner 路径常量（从 auto-tuner.py 搬入，统一管理）
HERMES_HOME        # 运行时从环境变量或 DEFAULT_HERMES_HOME 解析
ENV_FILE           # /root/.hermes/.env
HISTORY_FILE       # /root/.hermes/data/flywheel/daily-summary-history.jsonl
LOG_FILE           # /root/.hermes/data/flywheel/auto-tuner-log.jsonl
PAUSE_FILE         # /root/.hermes/data/flywheel/auto-tuner.pause
BACKUP_DIR         # /root/.hermes/backups/auto-tuner
STATE_FILE         # /root/.hermes/data/flywheel/auto-tuner-state.json
```

### 4.2 utils.py

跨分析器共享的纯工具函数：

| 函数 | 原行号 | 被谁调用 |
|------|--------|----------|
| `_percentile(values, p)` | L418 | router.py, token_budget.py, report.py |
| `_resolve_trend_arrow(delta_val)` | L1593 | report.py |
| `_is_test_query(key)` | L185 | kn_baseline.py |

### 4.3 parsers.py

数据加载/解析/持久化：

| 函数 | 原行号 |
|------|--------|
| `_load_json(path)` | L191 |
| `_save_json(path, data)` | L198 |
| `_load_jsonl(path)` | L204 |
| `_rotate_jsonl(path, keep)` | L220 |
| `append_daily_summary(data_flywheel, summary)` | L231 |
| `load_daily_summary(data_flywheel)` | L246 |
| `parse_cron_states(cron_state_dir)` | L252 |
| `parse_trace_log(trace_path, filter_dates)` | L266 |
| `scan_cron_log_errors(cron_log_dir, states, now)` | L309 |

### 4.4 integrity.py

完整性检查：

| 函数 | 原行号 |
|------|--------|
| `check_output_integrity(home)` | L1424 |
| `check_dependency_chain(states)` | L1473 |
| `detect_zombie_state_files(cron_state_dir)` | L1512 |
| `detect_report_type(cron_state_dir, now_utc)` | L1530 |

### 4.5 analyzers/ — 11 个分析器

每个分析器一个文件，函数签名不变。内部嵌套函数（如 `_stats`、`is_restart_cascade_noise`、`_avg`）随主函数一起搬入对应文件。

| 文件 | 函数 | 原行号 | 依赖 |
|------|------|--------|------|
| `cron_jobs.py` | `analyze_cron_jobs` | L355 | parsers.scan_cron_log_errors |
| `router.py` | `analyze_router` | L431 | utils._percentile, parsers._load_json/_save_json |
| `skill.py` | `analyze_skill_eval`, `analyze_skill_usage` | L635, L686 | parsers._load_json |
| `token_budget.py` | `analyze_token_budget` | L757 | utils._percentile |
| `sag.py` | `analyze_sag_contribution` | L828 | — |
| `global_errors.py` | `analyze_global_errors` | L906 | — |
| `kt_baseline.py` | `analyze_kt_baseline` | L1009 | parsers._load_json |
| `memory_cleanup.py` | `analyze_memory_cleanup` | L1056 | parsers._load_json/_save_json |
| `clustering.py` | `analyze_clustering` | L1182 | parsers._load_jsonl |
| `kn_baseline.py` | `analyze_kn_baseline`, `analyze_data_credibility` | L1245, L1380 | utils._is_test_query, parsers._load_json |

### 4.6 auto_tuner/

从 `auto-tuner.py`（1077 行）整体搬入：

- `tuner.py`：主调优逻辑（handle_pending_restart、determine_direction、update_state、select_param_to_tune、main 等）
- `notifier.py`：飞书通知（`_send_lark`、`notify_gateway_restart`、`notify_restart_reminder`）

路径常量改为从 `config.py` 导入（不再硬编码）。

### 4.7 report.py

| 函数 | 原行号 | 依赖 |
|------|--------|------|
| `format_7day_trend(data_flywheel)` | L1549 | parsers.load_daily_summary, utils._resolve_trend_arrow |
| `generate_report(home, dry_run)` | L1601 | 所有 analyzers + integrity + parsers + recommendations |

### 4.8 recommendations.py

| 函数 | 原行号 |
|------|--------|
| `generate_recommendations(...)` | L1986 |

### 4.9 cli.py

| 函数 | 原行号 |
|------|--------|
| `main()` | L2153 |

### 4.10 __main__.py

```python
"""支持 python3 -m flywheel_health_report 调用。"""
from flywheel_health_report.cli import main

if __name__ == "__main__":
    main()
```

---

## 5. 包内 Import 依赖图

```
cli.py
  └── report.py
        ├── analyzers/cron_jobs.py     → parsers.scan_cron_log_errors
        ├── analyzers/router.py        → utils._percentile, parsers._load_json/_save_json
        ├── analyzers/skill.py         → parsers._load_json
        ├── analyzers/token_budget.py  → utils._percentile
        ├── analyzers/sag.py           → (无内部依赖)
        ├── analyzers/global_errors.py → (无内部依赖)
        ├── analyzers/kt_baseline.py   → parsers._load_json
        ├── analyzers/memory_cleanup.py→ parsers._load_json/_save_json
        ├── analyzers/clustering.py    → parsers._load_jsonl
        ├── analyzers/kn_baseline.py   → utils._is_test_query, parsers._load_json
        ├── integrity.py               → config
        ├── parsers.py                 → config
        ├── recommendations.py         → config
        └── utils.py                   → (无内部依赖)

auto_tuner/tuner.py
  ├── config.py                        → 路径常量
  └── auto_tuner/notifier.py           → _send_lark

auto_tuner/notifier.py
  └── config.py                        → FEISHU_CHAT_ID, CRON_LIB
```

**关键规则**：
- analyzers 之间 **不互相 import**（数据通过 `generate_report` 的函数参数传递）
- analyzers 只依赖 utils、parsers、config
- report.py 是唯一编排者，import 所有 analyzers
- auto_tuner 独立于 report.py（通过文件通信，不 import）

---

## 6. 关键设计决策

### 6.1 PYTHONPATH 设置

生产环境通过 shell wrapper 显式设置 PYTHONPATH，与 clustering-analysis-v3 的做法一致：

```bash
# scripts/flywheel-health-report.sh
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$PROJECT_DIR/src:${PYTHONPATH:-}"
```

```bash
# scripts/auto-tuner.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="$PROJECT_DIR/src:${PYTHONPATH:-}"
```

### 6.2 共享工具函数

`_percentile` 等跨分析器工具函数放在 `utils.py`，不放在任何单个 analyzer 文件内：

```python
# analyzers/router.py
from ..utils import _percentile

# analyzers/token_budget.py
from ..utils import _percentile
```

### 6.3 flywheel-health-report.sh 完整变更

原 111 行逻辑保持不变，仅修改 3 处：

```diff
  # L24-35 路径常量区
+ PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
+ export PYTHONPATH="$PROJECT_DIR/src:${PYTHONPATH:-}"
- SCRIPT_PATH="${HERMES_HOME}/scripts/flywheel-health-report.py"

  # L39-51 Python 调用
- python3 "$SCRIPT_PATH" --home "$HERMES_HOME"
+ python3 -m flywheel_health_report.cli --home "$HERMES_HOME"

  # L96-109 末尾调用 auto-tuner
- _AUTO_TUNER="${HERMES_HOME}/scripts/auto-tuner.sh"
+ _AUTO_TUNER="${HERMES_HOME}/scripts/flywheel-health-report/scripts/auto-tuner.sh"
```

不变的部分：
- L13-21：cron_init / cron_common 加载
- L39-51：exit code 判断逻辑（0=ok / 1=P0 / 其他=失败）
- L53-94：awk 提取 P0/P1 + cron_notify + lark-cli 发 .md
- L96-109：if -f 检查 + bash 调用

### 6.4 auto-tuner.sh 部署路径

auto-tuner.sh 部署在包内 `scripts/auto-tuner.sh`，部署后路径为 `/root/.hermes/scripts/flywheel-health-report/scripts/auto-tuner.sh`。

**不保留**旧的 `/root/.hermes/scripts/auto-tuner.sh` flat 路径（避免两份文件混淆）。旧文件由 deploy LEGACY_FILES 清理。

auto-tuner.sh 不暴露给 cron 直接调度，只由 `flywheel-health-report.sh` 末尾链式调用。

### 6.5 auto-tuner 数据文件向后兼容

以下文件路径 **不变**，搬迁后 auto-tuner 继续读写同一位置：

| 文件 | 路径 | 说明 |
|------|------|------|
| state | `/root/.hermes/data/flywheel/auto-tuner-state.json` | 调优状态 |
| log | `/root/.hermes/data/flywheel/auto-tuner-log.jsonl` | 调优日志 |
| pause | `/root/.hermes/data/flywheel/auto-tuner.pause` | 暂停标记 |
| .env | `/root/.hermes/.env` | 参数文件（读写） |
| backup | `/root/.hermes/backups/auto-tuner/` | .env 备份 |
| history | `/root/.hermes/data/flywheel/daily-summary-history.jsonl` | 飞轮报告历史（只读） |

搬迁时只需把路径常量从 `auto-tuner.py` 硬编码改为从 `config.py` 导入，值不变。

### 6.6 pyproject.toml

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "flywheel-health-report"
version = "0.1.0"
description = "飞轮健康报告与参数自优化调优器"
requires-python = ">=3.10"
dependencies = []  # 仅依赖标准库

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

### 6.7 部署注册

新建 `deploy/projects/flywheel-health-report.sh`：

```bash
#!/bin/bash
# deploy/projects/flywheel-health-report.sh — 飞轮健康报告部署脚本
set -euo pipefail

PROJECT_NAME="flywheel-health-report"
PROJECT_SRC_REL="scripts/flywheel-health-report"
PROJECT_TGT="/root/.hermes/scripts/flywheel-health-report"
PROJECT_SVC=""

# 清理旧 flat 文件（从 cron-wrappers 时代遗留在 /root/.hermes/scripts/ 的）
LEGACY_FILES=(
  "/root/.hermes/scripts/flywheel-health-report.py"
  "/root/.hermes/scripts/flywheel-health-report.py.bak"
  "/root/.hermes/scripts/auto-tuner.py"
  "/root/.hermes/scripts/auto-tuner.sh"
  "/root/.hermes/scripts/auto-tuner.bak"
  "/root/.hermes/scripts/auto-tuner.json"
)

FIRST_DEPLOY_CLEANUP="false"  # 目标目录是共享父目录的子目录，不清理

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh" "$@"
```

### 6.8 cron-wrappers 移除清单

从 `scripts/cron-wrappers/` 目录中**删除**以下文件（已迁移到 flywheel-health-report 项目）：

| 删除文件 | 迁移到 |
|----------|--------|
| `flywheel-health-report.py` | `src/flywheel_health_report/` 拆分为 15 个模块 |
| `flywheel-health-report.sh` | `scripts/flywheel-health-report.sh` |
| `auto-tuner.py` | `src/flywheel_health_report/auto_tuner/tuner.py` + `notifier.py` |
| `auto-tuner.sh` | `scripts/auto-tuner.sh` |
| `auto-tuner.bak` | 删除（历史备份） |
| `auto-tuner.json` | 删除（历史调试文件） |
| `tests/test_flywheel_health_report.py` | 拆分到 `tests/test_*.py`（6 个测试文件） |

**保留**在 `scripts/cron-wrappers/` 的文件（与 flywheel 无关）：

```
cron-wrappers/
├── clustering-analysis-v3/      # 独立项目子目录
├── daily-learn/                 # 独立项目子目录
├── knowledge-tree-builder/      # 独立项目子目录
├── memory-cleanup/              # 独立项目子目录
├── skillopt-runner/             # 独立项目子目录
├── README.md
├── backfill-scope.py
├── cron-boot-detect.service
├── cron-boot-detect.sh
├── cron-catchup-repair.sh
├── cron-jobs-config.md
├── cron-periodic-detect.sh
├── health-check-cron.sh
├── kn-router-health-check.sh
├── knowledge-navigation-baseline.sh
├── run-skill-eval.sh
├── test_context.sh
├── test_env.sh
├── test_minimal.sh
└── test_syntax.sh
```

### 6.9 数据契约保持

auto-tuner 通过 `daily-summary-history.jsonl` 文件与 flywheel 通信，**不改为 Python import**。契约字段（`FEEDBACK_KEYS`）保持一致：

```python
# auto_tuner/tuner.py
from ..config import HISTORY_FILE  # 路径从 config 导入

FEEDBACK_KEYS = [
    "kn_avg_score", "router_empty_pct", "sag_total_kept",
    "sag_merge_zero_pct", "memory_hindsight_count",
]
```

---

## 7. Before/After 部署路径映射

| 文件 | 旧路径（cron-wrappers） | 新路径（flywheel-health-report） |
|------|------------------------|-------------------------------|
| flywheel-health-report.sh | `/root/.hermes/scripts/flywheel-health-report.sh` | `/root/.hermes/scripts/flywheel-health-report/scripts/flywheel-health-report.sh` |
| flywheel-health-report.py | `/root/.hermes/scripts/flywheel-health-report.py` | 拆分为包内 15 个模块，无单文件 |
| auto-tuner.sh | `/root/.hermes/scripts/auto-tuner.sh` | `/root/.hermes/scripts/flywheel-health-report/scripts/auto-tuner.sh` |
| auto-tuner.py | `/root/.hermes/scripts/auto-tuner.py` | `src/flywheel_health_report/auto_tuner/tuner.py` + `notifier.py` |
| test_flywheel_health_report.py | `/root/.hermes/scripts/tests/test_flywheel_health_report.py` | `tests/test_*.py`（6 个文件） |

**cron 调度入口**：`flywheel-health-report.sh` 的 cron 调度路径从 `/root/.hermes/scripts/flywheel-health-report.sh` 改为 `/root/.hermes/scripts/flywheel-health-report/scripts/flywheel-health-report.sh`。

**cron 配置需同步修改**：将 cron job 中 `flywheel-health-report.sh` 的路径更新为新路径。

---

## 8. conftest.py Fixture 设计

```python
# tests/conftest.py
import pytest
import tempfile
import json
from pathlib import Path


@pytest.fixture
def tmp_hermes_home(tmp_path, monkeypatch):
    """创建临时 HERMES_HOME 目录结构，隔离测试。"""
    home = tmp_path / "hermes"
    for sub in ["data/flywheel", "cron-state", "cron-log", "logs/reports",
                "memories", "baselines/kn", "baselines/kt", "backups/auto-tuner"]:
        (home / sub).mkdir(parents=True, exist_ok=True)
    # 创建空 .env
    (home / ".env").write_text("# test env\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.fixture
def sample_trace_lines():
    """样例 trace.log 行，覆盖 router/sag/token_budget 分析器所需字段。"""
    return [
        '{"ts":"2026-08-01T10:00:00+08:00","event":"router_search","recall_hindsight":2,"recall_knowledge_tree":3,"recall_sag":1,"recall_skill":0,"token_budget":{"hindsight":500,"total":2000},"sag_merge":{"kept":2,"zero":1}}\n',
        # ... 更多样例行
    ]


@pytest.fixture
def sample_cron_states(tmp_path):
    """样例 cron state 文件。"""
    state_dir = tmp_path / "cron-state"
    state_dir.mkdir()
    for job in ["flywheel-health-report", "run-skill-eval", "clustering-analysis"]:
        (state_dir / f"{job}.state").write_text(
            json.dumps({"status": "success", "last_run": "2026-08-01T10:00:00+08:00"}))
    return state_dir
```

---

## 9. 迁移步骤

| 阶段 | 内容 | 验证 |
|------|------|------|
| P0-1 | 创建项目骨架：`pyproject.toml` + `src/flywheel_health_report/__init__.py` + `__main__.py` + 空 `scripts/` + 空 `tests/` | 目录结构存在 |
| P0-2 | 搬 `config.py`（全部常量 + auto-tuner 路径常量）+ `utils.py`（`_percentile` 等）+ `parsers.py`（`_load_json` 等）+ `cli.py`（`main`） | `python3 -m flywheel_health_report.cli --help` 可运行 |
| P1-1 | 搬 11 个分析器到 `analyzers/`，每个文件 `from ..config import ...` + `from ..utils import ...` + `from ..parsers import ...` | `python3 -c "from flywheel_health_report.analyzers.router import analyze_router"` 可 import |
| P1-2 | 搬 `auto_tuner/tuner.py` + `auto_tuner/notifier.py`，路径常量改从 config 导入 | `python3 -c "from flywheel_health_report.auto_tuner.tuner import main"` 可 import |
| P1-3 | 搬 `report.py`（`generate_report` + `format_7day_trend`）+ `recommendations.py` | `python3 -c "from flywheel_health_report.report import generate_report"` 可 import |
| P1-4 | 写 `scripts/flywheel-health-report.sh`（保留通知逻辑 + PYTHONPATH + 新 auto-tuner 路径）+ `scripts/auto-tuner.sh` | `bash -n` 语法检查通过 |
| P2-1 | 写 `conftest.py` + 迁移测试：`importlib` → 正常 import，`fhr.xxx` → `from flywheel_health_report.xxx import xxx`，拆成按模块的测试文件 | `pytest tests/ -v` 全通过 |
| P2-2 | 新建 `deploy/projects/flywheel-health-report.sh` + 从 cron-wrappers 删除 flywheel/auto-tuner 文件 | `deploy.sh plan flywheel-health-report` 无报错 |
| P2-3 | dry-run 验证：用相同输入跑旧脚本和新包，对比输出 Markdown（忽略 timestamp 行）的 md5 | md5 一致 |

---

## 10. 风险与缓解

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| 搬迁过程中函数签名意外变化 | 高 | 只挪位置不重构；搬迁后 `python3 -c "import ..."` 逐模块验证 |
| PYTHONPATH 未设置导致 ModuleNotFoundError | 高 | shell wrapper 显式设置，与 clustering-v3 一致 |
| auto-tuner 路径变化导致 flywheel-health-report.sh 调用失败 | 中 | 同步修改 .sh 中的调用路径；LEGACY_FILES 清理旧文件 |
| cron 调度路径变化导致任务不执行 | 高 | P2-2 同步更新 cron 配置中的 .sh 路径；部署后手动触发一次验证 |
| cron-wrappers 移除后旧文件残留 | 中 | deploy LEGACY_FILES 清理旧 flat 文件 |
| 测试数据依赖真实环境路径 | 低 | conftest.py 用 tmp_path + monkeypatch 隔离 |
| auto-tuner 的 `daily-summary-history.jsonl` 契约字段不一致 | 低 | 搬迁时不改 append_daily_summary 的写入字段 |
| auto-tuner 数据文件（state/log/pause）路径变化 | 中 | config.py 中路径值不变，只改导入方式 |

---

## 11. 回滚策略

如果 dry-run 验证失败或生产环境异常：

1. **代码回滚**：`git revert` 重构 commit，恢复 `scripts/cron-wrappers/` 下的原始文件
2. **部署回滚**：`deploy.sh rollback flywheel-health-report` 回滚到上一版本
3. **cron 配置回滚**：恢复 cron job 中 `.sh` 的旧路径 `/root/.hermes/scripts/flywheel-health-report.sh`
4. **数据无损**：auto-tuner 的 state/log/pause 文件路径不变，回滚后数据连续

---

## 12. dry-run 不一致时的调查流程

如果 P2-3 的 md5 对比不一致：

1. **排除 timestamp**：确认差异行是否仅为报告头部的日期/时间行
2. **逐分析器对比**：在新代码中临时加 `print(json.dumps(metrics, indent=2))`，逐个分析器对比输出
3. **定位根因**：常见原因 — import 路径变化导致遗漏了某个常量、嵌套函数未随主函数搬入
4. **修复后重跑**：修复后重新 dry-run 对比，直到 md5 一致
5. **不一致不部署**：md5 不一致禁止进入 P2-2 部署阶段

---

## 13. 验证清单

- [ ] `python3 -m flywheel_health_report.cli --help` 正常输出
- [ ] `python3 -m flywheel_health_report.auto_tuner.tuner --help` 正常输出
- [ ] `python3 -m flywheel_health_report` 正常输出（`__main__.py` 生效）
- [ ] `pytest tests/ -v` 全部通过（≥54 tests：40 原有 + 14 auto-tuner）
- [ ] `bash -n scripts/flywheel-health-report.sh` 语法检查通过
- [ ] `bash -n scripts/auto-tuner.sh` 语法检查通过
- [ ] `deploy.sh plan flywheel-health-report` 无报错
- [ ] dry-run 输出与旧脚本一致（忽略 timestamp，md5 对比）
- [ ] auto-tuner 的 14 个单元测试在新包内通过
- [ ] auto-tuner 数据文件路径不变（state/log/pause/env/backup）
- [ ] cron 配置中 .sh 路径已更新
