# Flywheel Health Report & Auto-Tuner

> Hermes 数据飞轮的健康巡检与参数自优化组件：解析 `trace.log` → 多维度分析器聚合 → KN LLM Judge（mask 级评分）→ Auto-Tuner 参数自优化 → 半自动生效。

## 功能概览

| 阶段 | 模块 | 说明 |
|------|------|------|
| 阶段 0 | `runner.py` | 前置登记，声明内部任务（KN Judge 由阶段 1 内部执行），避免重复跑 LLM |
| 阶段 1 | `cli.py` → `report.py` | 生成每日健康报告，聚合 12 个 analyzer，落库 `daily-summary-history.jsonl`，飞书推送 P0/P1 |
| 阶段 2 | `auto_tuner/tuner.py` | 参数自优化：选参 → 方向决策 → 写 `.env` → 飞书通知人工确认重启 |

**触发**：每日 08:00 cron（`scripts/flywheel-health-report.sh`），离线处理前一日数据。

## 目录结构

```
scripts/flywheel-health-report/
├── scripts/flywheel-health-report.sh   # cron 入口（阶段 0/1/2 串联）
├── src/flywheel_health_report/
│   ├── runner.py                       # 阶段 0 Runner
│   ├── cli.py                         # 阶段 1 CLI 入口
│   ├── report.py                      # 报告生成（12 analyzer 聚合）
│   ├── parsers.py                     # trace.log 解析 + daily-summary 落库
│   ├── config.py                      # 路径常量 + 反馈键/参数定义（PARAM_DEFS）
│   ├── analyzers/                     # 各维度分析器
│   │   ├── router.py                  # Router 4 路 mask / recall 分析
│   │   ├── kn_judge.py                # KN LLM Judge（mask 级 h/kt/sag 评分）
│   │   ├── token_usage.py             # 实际 token 消耗观测（原 token_budget，已移除截断）
│   │   ├── sag_contribution.py        # SAG 召回贡献率
│   │   └── ...                        # 其余 analyzer
│   └── auto_tuner/
│       ├── tuner.py                   # 调优核心逻辑（15 个可调参数）
│       └── notifier.py                # 飞书通知
└── tests/                             # pytest 回归套件
```

## KN LLM Judge（mask 级）

`analyzers/kn_judge.py` 的 `run_judge_within_window(home, since_iso, until_iso)` 从 `trace.log` 采样召回记录，并发调用 LLM 逐条评分，输出**按影响路径拆分**的相关率：

- `kn_judge_relevant_rate_h` / `kn_judge_sample_count_h` — Hindsight 经验路
- `kn_judge_relevant_rate_kt` / `kn_judge_sample_count_kt` — 知识树路
- `kn_judge_relevant_rate_sag` / `kn_judge_sample_count_sag` — SAG 反思路

> ⚠️ **Token 预算已移除（2026-08-10）**：采样窗口固定 `[now-30d, now]`，与报告 CN 日切窗解耦；不再做任何注入前 token 截断，仅观测实际消耗。

## Auto-Tuner

`auto_tuner/tuner.py` 负责让 Router 飞轮**真正产生优化作用**：

- **15 个可调参数**（PARAM_DEFS），覆盖召回阈值、SAG 参数、因果链、熔断冷却等
- **4 桶优先级选参**：virgin-confident → virgin-unconfident → remaining-confident → remaining-unconfident
- **信任门控（per-mask）**：`_feedback_key_trusted` 按 `_h/_kt/_sag` 后缀读取 `daily-summary` 全量记录的样本计数，≥ `mask_min_sample`(12) 才采信 mask 反馈
- **安全机制**：不自动重启（飞书通知人工确认）、收敛锁定、连续恶化暂停并回滚、震荡惩罚、24h 冷却

> 设计细节见 `docs/architecture/data-flywheel-closed-loop.md` 与 `docs/architecture/data-flywheel-system-map.md`。

## 本地开发

```bash
cd scripts/flywheel-health-report
PYTHONPATH=src python -m pytest tests/ -q --ignore=tests/test_feishu_live.py
```

## 部署

```bash
sudo ./deploy/deploy.sh deploy flywheel-health-report --yes
```
