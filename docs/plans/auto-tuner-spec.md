# Auto-Tuner: 参数自优化飞轮

## 目标
飞轮报告每天产出指标后，自动选一个参数做小幅调整，第二天看指标变化，保留或回滚，持续迭代到收敛。

## 改动范围

### 新增文件
- `scripts/cron-wrappers/auto-tuner.sh` — 自优化脚本

### 修改文件
- `scripts/cron-wrappers/flywheel-health-report.sh` — 末尾追加调用 auto-tuner.sh

### 配置文件（不改代码，运行时改 .env）
- `/root/.hermes/.env` — 运行时由 auto-tuner 修改

## 问题清单

### 1. auto-tuner.sh 核心逻辑
- [ ] 读取 `data/flywheel/daily-summary-history.jsonl` 获取最近 2 天指标
- [ ] 从参数池中选一个未收敛的参数
- [ ] 判断调优方向（当前值 + 步长）
- [ ] 写入 .env 文件
- [ ] 重启 hermes-gateway.service
- [ ] 记录本次调优操作到 `data/flywheel/auto-tuner-log.jsonl`

### 2. 参数池定义
- [ ] `KN_MIN_SCORE`: 当前 0.6, 范围 [0.4, 0.8], 步长 0.05, 反馈指标: avg_score + empty_rate
- [ ] `sag_max_inject`: 当前 3, 范围 [2, 6], 步长 1, 反馈指标: sag_inject_count
- [ ] `sag_search_top_k`: 当前 3, 范围 [3, 10], 步长 1, 反馈指标: sag_zero_rate
- [ ] `token_budget_hindsight_ratio`: 当前 0.4, 范围 [0.3, 0.6], 步长 0.05, 反馈指标: hs_kept + sag_kept
- [ ] `lambda_mrr`: 当前 0.5, 范围 [0.3, 0.7], 步长 0.1, 反馈指标: (无直接指标，暂不启用)

### 3. 收敛判断
- [ ] 连续 3 次调优指标无变化 → 锁定，换下一个参数
- [ ] 某方向恶化 2 次 → 反向调或跳过
- [ ] 所有参数收敛 → 暂停，30 天后重新评估
- [ ] 某参数连续 3 天恶化（无论方向）→ 回滚到初始值，暂停该参数

### 4. 安全机制
- [ ] 一次只动一个参数，步幅不超过 20%
- [ ] 每次改动前备份当前 .env
- [ ] 恶化自动回滚
- [ ] 支持暂停：`touch /root/.hermes/data/flywheel/auto-tuner.pause`

### 5. 集成到飞轮报告
- [ ] flywheel-health-report.sh 末尾追加调用 auto-tuner.sh
- [ ] auto-tuner 的输出追加到飞轮报告尾部

### 6. 测试
- [ ] dry-run 模式：只输出决策，不修改 .env 和 gateway
- [ ] 手动触发验证：`bash auto-tuner.sh --dry-run`
- [ ] 验证读取 daily-summary-history.jsonl 正确
- [ ] 验证参数变更后 gateway 重启成功

## 执行流程
1. developer 实现 auto-tuner.sh
2. 主 session 全量测试
3. 部署（cron-wrappers 项目）
4. 手动验证