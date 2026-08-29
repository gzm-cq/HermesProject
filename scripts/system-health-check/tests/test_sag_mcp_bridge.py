#!/usr/bin/env python3
"""test_sag_mcp_bridge.py — 验证 aiohttp 版 sag-mcp-bridge 的 HTTP/1.1 SSE 与 401 自愈。

不依赖真实 SAG：起一个本地假上游（aiohttp），验证：
1. HTTP/1.1 + chunked SSE 流式转发（修复根因）
2. 401 → 刷新 token → 重试成功（保留自愈逻辑）
3. 健康端点
"""
import asyncio
import json
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

# 导入被测模块（文件名带连字符 sag-mcp-bridge.py，无法直接 import，用 importlib 加载）
import importlib.util

BRIDGE_PATH = Path(__file__).resolve().parent.parent / "sag-mcp-bridge.py"
_spec = importlib.util.spec_from_file_location("sag_mcp_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)  # type: ignore[union-attr]

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")


def bad(msg):
    global FAIL
    FAIL += 1
    print(f"  ❌ {msg}")


async def run_tests():
    # ── 假上游：模拟 SAG /mcp/ 返回 HTTP/1.1 SSE 流 + 一次 401 ──
    upstream_state = {"calls": 0, "good_token": "good-jwt-token-123"}

    async def fake_mcp(request: web.Request) -> web.StreamResponse:
        upstream_state["calls"] += 1
        auth = request.headers.get("Authorization", "")
        # token 不匹配时返回 401（模拟过期 token），匹配则返回 SSE
        if auth != f"Bearer {upstream_state['good_token']}":
            return web.Response(status=401, text="unauthorized")
        # 返回 SSE 长连接（模拟 Streamable HTTP）
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        resp.enable_chunked_encoding()
        await resp.prepare(request)
        await resp.write(b'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n')
        return resp

    async def fake_login(request: web.Request) -> web.Response:
        return web.json_response({"access_token": upstream_state["good_token"]})

    app = web.Application()
    app.router.add_post("/mcp/", fake_mcp)
    app.router.add_post("/api/v1/auth/login", fake_login)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    upstream_port = site._server.sockets[0].getsockname()[1]

    # 临时覆盖 bridge 配置
    bridge.SAG_BASE = f"http://127.0.0.1:{upstream_port}"
    bridge.TOKEN_PATH = Path("/tmp/test_sag_bridge_token")
    bridge.TOKEN_PATH.write_text("bad-expired-token")  # 初始坏 token

    # 临时起 bridge 服务（本进程内 app）
    bapp = web.Application()
    bapp.router.add_get("/mcp/", bridge.handle_get)
    bapp.router.add_post("/mcp/", bridge.handle_post)
    bapp.router.add_delete("/mcp/", bridge.handle_delete)
    bapp.router.add_get("/health", bridge.handle_health)
    brunner = web.AppRunner(bapp)
    await brunner.setup()
    bsite = web.TCPSite(brunner, "127.0.0.1", 0)
    await bsite.start()
    bridge_port = bsite._server.sockets[0].getsockname()[1]

    print("── 测试 1: HTTP/1.1 + SSE 流式转发（根因验证）──")
    async with aiohttp.ClientSession() as client:
        async with client.post(
            f"http://127.0.0.1:{bridge_port}/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        ) as resp:
            if resp.status == 200 and "text/event-stream" in resp.headers.get("Content-Type", ""):
                ok(f"200 + text/event-stream (HTTP {resp.version})")
            else:
                bad(f"status={resp.status} ct={resp.headers.get('Content-Type')}")
            body = await resp.text()
            if '"ok":true' in body:
                ok("SSE body 完整转发")
            else:
                bad(f"body 缺失: {body[:100]}")

    print("── 测试 2: 401 → 刷新 token → 重试成功 ──")
    # 重置坏 token，验证 bridge 自动换发
    bridge.TOKEN_PATH.write_text("bad-expired-token")
    async with aiohttp.ClientSession() as client:
        async with client.post(
            f"http://127.0.0.1:{bridge_port}/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        ) as resp:
            body = await resp.text()
            if '"ok":true' in body:
                ok("401 后自动刷新 token 并重试成功")
            else:
                bad(f"401 自愈失败: status={resp.status} body={body[:100]}")
    # 验证 token 已回写
    if bridge.TOKEN_PATH.read_text().strip() == upstream_state["good_token"]:
        ok("新 token 已回写文件")
    else:
        bad("token 未回写")

    print("── 测试 3: 健康端点 ──")
    async with aiohttp.ClientSession() as client:
        async with client.get(f"http://127.0.0.1:{bridge_port}/health") as resp:
            data = await resp.json()
            if data.get("status") == "ok":
                ok("/health 正常")
            else:
                bad(f"/health: {data}")

    await brunner.cleanup()
    await runner.cleanup()
    bridge.TOKEN_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(run_tests())
    print(f"\n══════════════════════════════")
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
