# SPEC: Codex App Server 集成

## 目标
Hermes 通过 Codex App Server 提交长时任务（数小时），不阻塞会话，不丢进度。

## 架构

```
Hermes Agent → [Python MCP Client] → WebSocket JSON-RPC → codex app-server
                              ↕
                     systemd: codex-app-server.service
```

## 改动范围

### 1. systemd 服务：`codex-app-server.service`
- 启动 `codex app-server daemon start`
- 监听 `ws://127.0.0.1:9876`
- 带健康检查 `/healthz`
- 依赖：`network-online.target`

### 2. Hermes MCP 工具：`codex-server`
Python 脚本，通过 WebSocket 驱动 app-server JSON-RPC 协议，暴露 3 个工具：

| 工具 | 功能 | 参数 |
|------|------|------|
| `codex_start_task` | 提交任务 | `prompt`, `cwd`, `sandbox?` |
| `codex_task_status` | 查状态 | `task_id` |
| `codex_cancel_task` | 取消任务 | `task_id` |

### 3. 协议处理（关键）
- `initialize` → 连接握手
- `thread/start` → 创建线程，提交任务
- 自动回复 `execCommandApproval` → auto-approve
- 监听 `turn/completed` → 任务完成
- 定期轮询 `thread/list` → 查状态
- `thread/cancel` → 取消

## 问题清单

- [ ] Q1: app-server 的 `execCommandApproval` 能否完全 auto-approve？
- [ ] Q2: 任务完成后结果如何提取（stdout / diff / summary）？
- [ ] Q3: systemd 下 stdio 模式 vs WebSocket 模式哪个更可靠？

## 执行流程

1. 首先生成 protocol schema 确认协议细节
2. 启动 app-server daemon + systemd 服务
3. 写 Python MCP 客户端（先连上初始化）
4. 实现 thread/start + 自动 approval
5. 实现状态查询 + 取消
6. 注册到 Hermes config.yaml
7. 测试长时任务