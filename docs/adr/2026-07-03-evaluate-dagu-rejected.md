# ADR: 评估 Dagu 调度框架 — 评估后不采用

> 日期: 2026-07-03
> 状态: 已决策（评估后不采用）
> 决策者: 项目负责人

## 背景

HermesProject 当前有 12 个 no_agent shell 定时任务 + 2 个 agent-mode 任务，通过 Hermes 内置 cron + `cron_common.sh` wrapper 模式管理。TASKBOARD 中 INFRA-02（统一调度框架）的触发条件「任务 > 8」已满足（14 > 8），启动评估。

评估了 Dagu（github.com/dagucloud/dagu, 3.6k stars, Go 二进制, YAML 声明式 DAG）作为替代方案。

## 评估过程

1. 安装 Dagu v2.9.1（单 Go 二进制, 44MB）
2. 创建试点 DAG `system-health-check`，手动触发验证
3. 逐步迁移全部 10 个 no_agent 任务到 Dagu DAG
4. 9/10 DAG 手动触发 exit 0（clustering-analysis 超时未完成）
5. 暂停对应 Hermes cron，Dagu 独跑

## 评估结论：不采用

| 维度 | Dagu 表现 | 问题 |
|------|----------|------|
| DAG 执行 | 9/10 通过 | clustering-analysis 300s 超时 |
| 环境隔离 | 需手动声明 env | lark-cli 子进程找不到配置，需 symlink hack |
| 飞书通知 | 需额外适配 | `cron_common.sh` 的 `cron_notify` 在 Dagu 子进程下 warn |
| 自愈盲区 | 无覆盖 | Dagu 挂了 Hermes cron 不会自动恢复 |
| 收益 vs 成本 | 低 | 10 个独立 shell 无 DAG 依赖，wrapper 已稳定数月 |

**核心判断**：10 个独立 shell 脚本零 DAG 依赖，`cron_common.sh` + flock 已稳定运行数月。Dagu 引入环境鸿沟 + 进程开销 + 自愈盲区，换来的声明式 YAML 和 Web UI 对当前规模不构成实际收益。

## 保留的修复

评估过程中发现 `cron_common.sh` 的 `cron_warn()` 函数存在 `set -e` 下 latent bug：`[[ "$OVERALL_STATUS" == "success" ]] && OVERALL_STATUS="partial"` 在第二次调用时返回 1，`set -e` 杀进程。已加 `return 0` 修复，影响全部 8 个使用 `cron_common.sh` 的任务。此修复独立于 Dagu 评估，予以保留。

## 清理

- Dagu 二进制 `/usr/local/bin/dagu` — 已删除
- Dagu 配置 `/root/.config/dagu/` — 已删除
- Dagu 数据 `/root/.local/share/dagu/` — 已删除
- systemd 服务 `/etc/systemd/system/dagu.service` — 已删除
- lark-cli symlink `/root/.lark-cli/config.json` — 已删除
- Hermes cron 12 个任务 — 全部恢复正常调度

## 后续触发条件

INFRA-02 保持「条件触发型蓝图」状态，触发条件不变：
- 出现真正的跨任务 DAG 依赖
- 需要任务运行状态面板成为刚需
- 当前 wrapper 模式出现实际冲突或痛点
