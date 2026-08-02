# Auto-Tuner Refactor SPEC v5 — Bash → Python Rewrite

## 目标

将 auto-tuner.sh 的核心逻辑用 Python 重写，bash 仅作为 wrapper；参数池改用 JSON 配置；添加单元测试和 shellcheck 验证。

## 改动范围

- `scripts/cron-wrappers/auto-tuner.sh` → bash wrapper（约50行）
- `scripts/cron-wrappers/auto-tuner.py` → 主逻辑（约400行）
- `scripts/cron-wrappers/auto-tuner.json` → 参数池配置
- `scripts/cron-wrappers/test_auto_tuner.py` → 单元测试

## 架构设计

```
auto-tuner.sh (bash wrapper)
├── set -u pipefail (安全模式)
├── parse --dry-run flag
├── load env vars from .env
├── call auto-tuner.main() via python3
└── handle exit code

auto-tuner.py (Python core)
├── config: load parameters from JSON file
├── state: load/save state.json with atomic write
├── metrics: read daily-summary-history.jsonl
├── tuner: select parameter, determine direction, validate step
├── executor: write to .env, notify gateway restart
├── logger: record to auto-tuner-log.jsonl
└── utils: helper functions (validate_step, is_converged, etc.)

test_auto_tuner.py (unit tests)
├── test_parameter_selection
├── test_direction_determination
├── test_step_validation
├── test_state_persistence
└── test_metrics_extraction
```

## Fix A: Bash → Python rewrite

### auto-tuner.sh (新文件，约50行)

```bash
#!/bin/bash
set -uo pipefail

DRY_RUN=false
for arg in "$@"; do [[ "$arg" == "--dry-run" ]] && DRY_RUN=true; done

python3 /mnt/d/HermesProject/scripts/cron-wrappers/auto-tuner.py \
    --dry-run "$DRY_RUN" \
    --env-file "${HERMES_HOME:-/root/.hermes}/.env" \
    --history-file "${HERMES_HOME:-/root/.hermes}/data/flywheel/daily-summary-history.jsonl" \
    --log-file "${HERMES_HOME:-/root/.hermes}/data/flywheel/auto-tuner-log.jsonl" \
    --state-file "${HERMES_HOME:-/root/.hermes}/data/flywheel/auto-tuner-state.json" \
    --pause-file "${HERMES_HOME:-/root/.hermes}/data/flywheel/auto-tuner.pause" \
    --config-file "$(dirname "$0")/auto-tuner.json" || exit $?
```

### auto-tuner.py (核心逻辑，约400行)

将原 bash 中的 Python inline code 提取为纯 Python 模块函数：

- `load_state(state_file)` → load JSON, atomic read/write
- `save_state(state_file, state)` → atomic write (tmp + mv)  
- `read_env_param(env_file, param_name)` → read from .env
- `write_env_param(env_file, param_name, new_value)` → sed replacement or append
- `extract_metrics(history_file, today, yesterday)` → single pass through JSONL
- `select_parameter(param_defs, state)` → priority-based selection with untried params check
- `determine_direction(current_val, min_val, max_val, step, feedback_metrics, metric_diff, last_tune)` → improved logic using metric_diff_json
- `validate_step(old_val, new_val, step)` → integer vs float handling with absolute diff for integers
- `is_param_converged(state, param_name)` → check no_change_count >= 3 or locked/suspended flags

## Fix B: PARAM_DEFS as JSON config

### auto-tuner.json (新文件)

```json
[
    {
        "param": "KN_MIN_SCORE",
        "default": 0.6,
        "min": 0.4,
        "max": 0.8,
        "step": 0.05,
        "feedback_metrics": ["kn_avg_score", "router_empty_pct"]
    },
    {
        "param": "sag_max_inject",
        "default": 3,
        "min": 2,
        "max": 6,
        "step": 1,
        "feedback_metrics": ["sag_total_kept"]
    },
    {
        "param": "sag_search_top_k",
        "default": 3,
        "min": 3,
        "max": 10,
        "step": 1,
        "feedback_metrics": ["sag_merge_zero_pct"]
    },
    {
        "param": "token_budget_hindsight_ratio",
        "default": 0.4,
        "min": 0.3,
        "max": 0.6,
        "step": 0.05,
        "feedback_metrics": ["memory_hindsight_count", "sag_total_kept"]
    },
    {
        "param": "sag_search_threshold",
        "default": 0.5,
        "min": 0.3,
        "max": 0.8,
        "step": 0.05,
        "feedback_metrics": ["sag_on_pct", "sag_total_kept"]
    },
    {
        "param": "token_budget",
        "default": 4000,
        "min": 2000,
        "max": 8000,
        "step": 500,
        "feedback_metrics": ["token_exhaust_pct"]
    }
]
```

Python loader: `with open(config_path) as f: param_defs = json.load(f)`

## Fix C: Add shellcheck validation

在 deploy/deploy.sh 或 cron wrapper中添加：

```bash
if command -v shellcheck &>/dev/null; then
    shellcheck -x scripts/cron-wrappers/auto-tuner.sh || echo "[WARN] shellcheck errors above" fi fi ``` 

## Fix D: Unit tests for key functions

### test_auto_tuner.py (新增文件)

测试内容：

1. **test_parameter_selection** - verify that untried parameters are prioritized over tried ones when state exists only for some params

2. **test_direction_determination** - verify direction logic uses metric_diff correctly (up/down based on improvement count)

3. **test_validate_step** - verify integer steps use absolute diff (<=1 step allowed), float steps use percentage rule (<=20%)

4. **test_state_persistence** - verify save/load works correctly with atomic tmp+mv pattern

5. **test_extract_metrics** - verify single-read extraction of today/yesterday metrics from JSONL history file

6. **test_is_param_converged** - verify convergence detection with no_change_count >= 3 and locked/suspended flags

7. **test_new_params_added** - verify sag_search_threshold and token_budget appear in param_defs after loading JSON config

## Execution Flow

1. developer creates auto-tuner.py with all core logic refactored from bash script's Python inline code blocks
   
2. developer creates auto-tuner.json config replacing PARAM_DEFS array
   
3. developer updates auto-tuner.sh to be a thin wrapper calling the Python module
   
4. developer writes unit tests in test_auto_tuner.py
   
5. main session runs bash -n on new auto-tuner.sh
   
6. main session runs python3 -m pytest test_auto_tuner.py
   
7. main session runs original dry-run test on the new script
   
8. deploy to /root/.hermes/scripts/auto-tuner.sh and /root/.hermes/scripts/auto-tuner.py
   
9. run full integration test with actual environment data

## Verification Criteria

- [ ] bash -n passes on auto-tuner.sh (wrapper) ✓ 
- [ ] python3 -m pytest test_auto_tuner.py passes ✓ 
- [ ] --dry-run produces correct output (selected sag_max_inject not KN_MIN_SCORE) ✓ 
- [ ] All original functionality preserved (state persistence, log recording, env modification) ✓ 
- [ ] New parameters loaded from JSON config ✓ 
- [ ] No hardcoded PARAM_DEFS in bash script ✓ 

## Not Changed

- Does not modify flywheel-health-report.sh (call chain unchanged)  
- Does not change .env format or location  
- Does not remove existing safety mechanisms (backup env, step validation)  
- Does not alter the core tuning algorithm's business logic  
