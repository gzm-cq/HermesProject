# SkillOpt-Runner

Hermes 后置技能优化运行器。从 Hermes 会话历史中挖掘使用模式，用 SkillOpt-Sleep 对 eligible skill 做增量优化，通过后自动写入 SKILL.md。

## 结构

```
skillopt-runner/
├── skillopt_runner.py          # 主入口（3-phase: harvest → rank → optimize）
├── config.yaml                 # 配置（backend/model/top_k/denylist）
├── state.json                  # 运行时状态（skill_last_run + 负反馈累积）
├── tests/
│   ├── conftest.py             # mock Hermes/SkillOpt 依赖
│   └── test_skillopt_runner.py # 核心逻辑测试
└── references/
    └── skillopt-sleep-backend-litellm-patched.py  # LiteLLMBackend 历史补丁备份
```

**外部依赖**：
- `skillopt-sleep`：独立项目，部署在 `/root/.hermes/skillopt-sleep/`（运行时 sys.path 自动添加）
- `skillopt-nightly-run.sh`：cron wrapper，统一管理在 `scripts/cron-wrappers/skillopt-runner/`，由 cron-wrappers 项目部署到 `/root/.hermes/scripts/skillopt-runner/`

## 测试

测试环境需要 mock `skillopt_sleep` 和 `tools.skill_manager_tool`（已在 conftest.py 中完成）。

```bash
# 安装 pytest（可用 Hermes venv 或独立 venv）
pip install pytest

# 运行测试
cd /mnt/d/HermesProject/scripts/skillopt-runner
python -m pytest tests/ -v
```

注意：测试不覆盖真实的 SkillOpt-Sleep 交互（SkillOpt 部分被 mock）。真实集成验证需在部署后跑 `--dry-run`。

## 部署

```bash
cd /mnt/d/HermesProject
./deploy/deploy.sh plan skillopt-runner
./deploy/deploy.sh deploy skillopt-runner --yes
```

## 运行流程

1. **Phase 1 harvest**：从 `state.db` + `sessions/*.jsonl` + `session_*.json` 全量采集会话
2. **Phase 2 rank**：按 usage.json 筛选 eligible skill（排除 denylist + pinned），按使用频率和负反馈评分取 top_k
3. **Phase 3 optimize**：逐 skill 过滤增量会话 → mine tasks → run_sleep_cycle → gate 验证 → 通过后 patch_skill_hermes 写入

## LiteLLMBackend

`skillopt-sleep` 项目源码中已内置 `LiteLLMBackend`，支持 `backend: "litellm"` 配置。`references/` 中的补丁文件仅作历史记录保留。
