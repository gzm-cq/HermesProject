# SPEC: sag-mcp-bridge.py 协议升级 — http.server → aiohttp (HTTP/1.1 SSE)

## 背景

`sag-mcp-bridge.py` 是 SAG MCP token 桥接（systemd `sag-mcp-bridge.service`，端口 4176）。
它在每次请求注入 JWT，遇 401 自动刷新重试，是 SAG 401 自愈的核心桥接层。

**故障**：Hermes gateway 连接 sag MCP 失败，报 `SSE stream ended without a response`，
`hermes mcp test sag` 复现，MCP server 'sag' 反复进入 parked 状态，sagrecall 超时。

## 根因

bridge 用 Python `http.server.BaseHTTPRequestHandler`，**默认 HTTP/1.0**。
而 SAG 的 `/mcp/` 是 MCP Streamable HTTP 协议，返回 **HTTP/1.1 + chunked SSE 长连接**：

| | SAG 直连 | bridge 转发 |
|---|---|---|
| HTTP 版本 | HTTP/1.1 200 OK | HTTP/1.0 200 OK |
| 传输编码 | transfer-encoding: chunked | connection: close |
| 流类型 | 标准 SSE 长连接 | 首块后关闭 |

HTTP/1.0 不支</think>