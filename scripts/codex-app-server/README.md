# codex-app-server

> Hermes 通过 Codex App Server 提交**长时任务**（数小时级）的集成桥接层：Python MCP 桥接进程经 WebSocket JSON-RPC 驱动 codex app-server，暴露长时任务管理工具，不阻塞会话、不丢进度。

## 架构

```
Hermes Agent → [Python MCP Client] → WebSocket JSON-RPC → codex app-server
                              ↕
                     systemd: codex-app-server.service
```

详见同目录 [`SPEC.md`](SPEC.md)（目标、协议、工具清单、执行流程）。

## 组成

| 文件 | 说明 |
|------|------|
| `codex_app_server_bridge.py` | WebSocket JSON-RPC 客户端 + 可选 FastMCP 服务模式，暴露长时任务工具 |
| `schema/` | 协议 JSON schema |
| `SPEC.md` | 集成规格说明 |

## 暴露的 MCP 工具

| 工具 | 功能 | 参数 |
|------|------|------|
| `codex_start_task` | 提交任务 | `prompt`, `cwd`, `sandbox?` |
| `codex_task_status` | 查状态 | `task_id` |
| `codex_cancel_task` | 取消任务 | `task_id` |

## 协议要点

- `initialize` → 连接握手
- `thread/start` → 创建线程并提交任务（自动 `execCommandApproval`）
- `turn/completed` → 任务完成通知
- `thread/list` → 轮询状态
- `thread/cancel` → 取消

## 运行

```bash
# 默认连接 ws://127.0.0.1:9876，MCP 服务端口 9877
python3 codex_app_server_bridge.py [--port 9877] [--app-server-ws ws://127.0.0.1:9876]
```

**依赖**：`websockets`；MCP 服务模式另需 `mcp`（FastMCP）。

> 状态：SPEC 中 Q1–Q3 问题清单仍待验证（auto-approve 可行性、结果提取方式、systemd 下 stdio vs WebSocket 可靠性）。
