#!/usr/bin/env python3
"""SAG MCP Token Bridge - injects fresh JWT on every request (aiohttp/HTTP-1.1).

aiohttp 版：支持 HTTP/1.1 + chunked + SSE 长连接，兼容 MCP Streamable HTTP 协议。
保留 401 自愈：遇上游 HTTPError(401) 自动调 /api/v1/auth/login 刷新并重试一次。
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

SAG_BASE = os.environ.get("SAG_BASE", "http://127.0.0.1:4173")
TOKEN_PATH = Path(os.environ.get("SAG_TOKEN_PATH", "/root/.hermes/.sag_token"))
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "4176"))
REQUEST_TIMEOUT = float(os.environ.get("BRIDGE_TIMEOUT", "300"))
# 透传到客户端的响应头白名单（排除 hop-by-hop 与连接管理头，由 aiohttp 自行处理）
PASSTHROUGH = {
    "content-type", "cache-control", "x-accel-buffering",
    "x-request-id", "mcp-session-id",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sag-mcp-bridge")

_client: ClientSession | None = None


def get_session() -> ClientSession:
    global _client
    if _client is None or _client.closed:
        _client = ClientSession(timeout=ClientTimeout(total=REQUEST_TIMEOUT))
    return _client


def _read_token() -> str:
    try:
        tok = TOKEN_PATH.read_text().strip()
        if tok:
            return tok
    except OSError:
        pass
    return ""


async def refresh_token() -> str:
    """调 /api/v1/auth/login 换发新 JWT 并回写 .sag_token。返回新 token（失败返回空串）。"""
    try:
        payload = json.dumps({"name": "hermes"}).encode()
        async with get_session().post(
            f"{SAG_BASE}/api/v1/auth/login",
            data=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status != 200:
                logger.error("Token refresh failed: HTTP %d", resp.status)
                return ""
            tok = (await resp.json()).get("access_token", "")
            if tok:
                TOKEN_PATH.write_text(tok)
                os.chmod(TOKEN_PATH, 0o600)
                logger.info("Token refreshed OK")
                return tok
    except Exception as e:
        logger.error("Token refresh failed: %s", e)
    return ""


def get_token() -> str:
    """优先读文件；文件缺失时才刷新。"""
    tok = _read_token()
    if tok:
        return tok
    return asyncio.run(refresh_token())


async def _proxy(request: web.Request, method: str) -> web.StreamResponse:
    """代理请求到 SAG，透传 SSE 流。遇 401 自动刷新 token 重试一次。"""
    url = f"{SAG_BASE}{request.raw_path}"
    body = await request.read()

    headers = {}
    for k, v in request.headers.items():
        if k.lower() not in ("host", "authorization", "content-length", "connection", "transfer-encoding"):
            headers[k] = v
    token = get_token()
    headers["Authorization"] = f"Bearer {token}"

    session = get_session()
    req_kwargs: dict = dict(headers=headers)
    if body:
        req_kwargs["data"] = body

    for attempt in range(2):  # 第 2 次为 401 刷新重试
        try:
            resp = await session.request(
                method, url,
                headers=headers,
                data=body if body else None,
                timeout=ClientTimeout(total=REQUEST_TIMEOUT),
            )
        except Exception as e:
            logger.error("Upstream request error: %s", e)
            return web.Response(status=502, text=f"Upstream error: {e}")

        if resp.status == 401 and attempt == 0:
            # 释放当前响应再刷新重试
            resp.release()
            logger.info("Got 401, refreshing token and retrying")
            token = await refresh_token()
            if not token:
                return web.Response(status=502, text="Token refresh failed")
            headers["Authorization"] = f"Bearer {token}"
            continue

        # 构造流式响应，透传关键头
        sse = web.StreamResponse(status=resp.status)
        for k, v in resp.headers.items():
            if k.lower() in PASSTHROUGH:
                sse.headers[k] = v
        # SSE 流必需：关闭缓冲，逐块转发
        sse.enable_chunked_encoding()
        await sse.prepare(request)
        try:
            async for chunk in resp.content.iter_chunked(4096):
                await sse.write(chunk)
        finally:
            await resp.release()
        await sse.write_eof()
        return sse

    return web.Response(status=502, text="Max retries exceeded")


async def handle_get(request: web.Request) -> web.StreamResponse:
    return await _proxy(request, "GET")


async def handle_post(request: web.Request) -> web.StreamResponse:
    return await _proxy(request, "POST")


async def handle_delete(request: web.Request) -> web.StreamResponse:
    return await _proxy(request, "DELETE")


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "base": SAG_BASE, "token_file": str(TOKEN_PATH)})


async def main() -> None:
    app = web.Application()
    app.router.add_get("/mcp/", handle_get)
    app.router.add_post("/mcp/", handle_post)
    app.router.add_delete("/mcp/", handle_delete)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", BRIDGE_PORT)
    await site.start()
    logger.info("SAG MCP bridge (aiohttp) listening on port %d → %s", BRIDGE_PORT, SAG_BASE)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
