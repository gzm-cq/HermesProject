# system-health-check

> 系统健康巡检 — 3-tier 架构每日自动检查各组件状态，异常时飞书告警。

## 架构

| 层级 | 文件 | 职责 |
|------|------|------|
| Ops 执行 | `health-check-all.py` | 采集所有组件指标（服务、进程、DB、MCP、Docker） |
| 格式与推送 | `health-check-run.py` | 格式化结果 + 飞书推送 |
| Cron 调度 | `health-check-cron.sh` | Hermes cron wrapper（cron_common 包装） |

## 巡检项

- Hermes Gateway（`systemctl is-active`）
- Hindsight Daemon
- LiteLLM 网关（HTTP /health）
- Axiom Wiki MCP
- Moon Bridge / codex-bridge
- Dify 容器
- PostgreSQL（5434 / 5432）
- MCP 服务状态

## 部署

```bash
cd deploy && ./deploy/deploy.sh deploy system-health-check --yes
```

## Cron

- 排期：工作日 9:00
- no_agent 脚本：`health-check-cron.sh`
- 交付：local（结果写入  ~/.hermes/cron/output/）
