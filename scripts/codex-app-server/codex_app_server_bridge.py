"""
Codex App Server MCP Bridge

Connects to a running codex app-server via WebSocket and exposes
Hermes MCP tools for long-running task management.

Protocol: JSON-RPC over WebSocket
  - initialize → connect
  - thread/start → create thread
  - turn/start → submit prompt (with auto-approve)
  - thread/list → list threads
  - turn/interrupt → cancel running turn

Usage:
  python3 codex_app_server_bridge.py [--port 9877] [--app-server-ws ws://127.0.0.1:9876]
"""

import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, Optional

try:
    import websockets
except ImportError:
    print("Missing websockets. Install: pip install websockets")
    sys.exit(1)

# FastMCP is only needed for the MCP server mode
try:
    import mcp.server.fastmcp as _fastmcp
    FastMCP = _fastmcp.FastMCP
except ImportError:
    FastMCP = None


# ---------------------------------------------------------------------------
# Codex App Server Client
# ---------------------------------------------------------------------------

class CodexAppServerClient:
    """WebSocket JSON-RPC client for codex app-server."""

    def __init__(self, ws_url: str = "ws://127.0.0.1:9876"):
        self.ws_url = ws_url
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._request_id = 0
        self._thread_info: dict[str, dict] = {}  # thread_id -> {status, preview, ...}

    async def connect(self) -> None:
        if self._ws is not None:
            return
        self._ws = await websockets.connect(self.ws_url, max_size=2**24)
        # Initialize
        result = await self._rpc("initialize", {
            "clientInfo": {
                "name": "hermes-codex-bridge",
                "title": "Hermes Codex Bridge",
                "version": "0.1.0",
            },
            "capabilities": {},
        })
        print(f"[codex-bridge] Initialized: userAgent={result.get('userAgent', '?')}")
        print(f"[codex-bridge] codexHome={result.get('codexHome', '?')}")

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _rpc(self, method: str, params: Any = None) -> Any:
        """Send a JSON-RPC request and wait for response."""
        assert self._ws is not None, "Not connected"
        self._request_id += 1
        req_id = self._request_id
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        try:
            assert self._ws is not None
            await self._ws.send(json.dumps(request))
            # Wait for the matching response
            while True:
                msg = await asyncio.wait_for(self._ws.recv(), timeout=3600)
                data = json.loads(msg)
                # Check if this is a response to our request
                if "id" in data and data["id"] == req_id:
                    if "error" in data:
                        err = data["error"]
                        raise RuntimeError(f"RPC error {err.get('code', '?')}: {err.get('message', '?')}")
                    result = data.get("result", {})
                    return result
                # Check if this is a server request (approval, etc.)
                elif "method" in data and "id" in data:
                    await self._handle_server_request(data)
                # Check if this is a notification
                elif "method" in data and "id" not in data:
                    self._handle_notification(data)
                # else: response to someone else? shouldn't happen
        except asyncio.TimeoutError:
            raise TimeoutError(f"RPC call {method} timed out")
        except Exception:
            self._pending.pop(req_id, None)
            raise
        finally:
            self._pending.pop(req_id, None)

    async def _handle_server_request(self, data: dict) -> None:
        """Handle a server-to-client request (approval, tool call, etc.)."""
        method = data.get("method", "")
        req_id = data.get("id")
        params = data.get("params", {})

        if method == "execCommandApproval":
            # Auto-approve command execution
            cmd = " ".join(params.get("command", []))
            print(f"[codex-bridge] Auto-approving command: {cmd[:100]}")
            response = {"jsonrpc": "2.0", "id": req_id, "result": {"decision": "approve"}}
            await self._ws.send(json.dumps(response))

        elif method == "applyPatchApproval":
            # Auto-approve file patches
            print(f"[codex-bridge] Auto-approving patch")
            response = {"jsonrpc": "2.0", "id": req_id, "result": {"decision": "approve"}}
            await self._ws.send(json.dumps(response))

        elif method == "item/commandExecution/requestApproval":
            # Auto-approve command execution request
            cmd = params.get("command", "")
            print(f"[codex-bridge] Auto-approving command execution: {str(cmd)[:100]}")
            response = {"jsonrpc": "2.0", "id": req_id, "result": {"decision": "approve"}}
            await self._ws.send(json.dumps(response))

        elif method == "item/fileChange/requestApproval":
            print(f"[codex-bridge] Auto-approving file change")
            response = {"jsonrpc": "2.0", "id": req_id, "result": {"decision": "approve"}}
            await self._ws.send(json.dumps(response))

        elif method == "item/permissions/requestApproval":
            print(f"[codex-bridge] Auto-approving permissions request")
            response = {"jsonrpc": "2.0", "id": req_id, "result": {"decision": "approve"}}
            await self._ws.send(json.dumps(response))

        elif method == "currentTime/read":
            import datetime
            now = datetime.datetime.now()
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "timezone": "Asia/Shanghai",
                    "timestamp": int(now.timestamp()),
                    "iso8601": now.isoformat(),
                },
            }
            await self._ws.send(json.dumps(response))

        else:
            print(f"[codex-bridge] Unhandled server request: {method}")
            response = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": f"Unhandled: {method}"}}
            await self._ws.send(json.dumps(response))
            return

        # Send auto-approval for all approval requests
        response = {"jsonrpc": "2.0", "id": req_id, "result": {"decision": "approve"}}
        await self._ws.send(json.dumps(response))

    def _handle_notification(self, data: dict) -> None:
        """Handle a server notification."""
        method = data.get("method", "")
        params = data.get("params", {})

        if method == "thread/started":
            thread = params.get("thread", {})
            tid = thread.get("id", "?")
            print(f"[codex-bridge] Thread started: {tid}")

        elif method == "thread/status/changed":
            tid = params.get("threadId", "?")
            status = params.get("status", {})
            stype = status.get("type", "?")
            print(f"[codex-bridge] Thread {tid} status: {stype}")
            if tid in self._thread_info:
                self._thread_info[tid]["status"] = status

        elif method == "turn/completed":
            tid = params.get("threadId", "?")
            turn = params.get("turn", {})
            print(f"[codex-bridge] Turn completed for thread {tid}: status={turn.get('status', '?')}")

        else:
            print(f"[codex-bridge] Unhandled notification: {method}")

    # ---- Public API ----

    async def start_task(self, prompt: str, cwd: str = "/mnt/d/HermesProject",
                         sandbox: str = "danger-full-access") -> dict:
        """Start a new Codex task. Returns thread info."""
        await self.connect()

        # 1. Create a thread
        thread_result = await self._rpc("thread/start", {
            "cwd": cwd,
            "sandbox": sandbox,
            "approvalPolicy": "never",
            "ephemeral": False,
        })
        thread = thread_result.get("thread", thread_result)
        tid = thread.get("id", "")
        self._thread_info[tid] = {"status": {"type": "idle"}, "preview": "", "cwd": cwd}

        # 2. Submit the prompt as a turn
        sandbox_param = {"type": "dangerFullAccess"} if sandbox == "danger-full-access" else {"type": sandbox}
        turn_result = await self._rpc("turn/start", {
            "threadId": tid,
            "input": [{"type": "text", "text": prompt}],
            "approvalPolicy": "never",
            "sandboxPolicy": sandbox_param,
        })
        turn = turn_result.get("turn", turn_result)
        turn_id = turn.get("id", "")

        self._thread_info[tid]["turn_id"] = turn_id
        self._thread_info[tid]["status"] = {"type": "active"}
        return {
            "thread_id": tid,
            "turn_id": turn_id,
            "status": "running",
            "cwd": cwd,
        }

    async def list_tasks(self, limit: int = 10) -> list[dict]:
        """List threads/tasks."""
        await self.connect()
        result = await self._rpc("thread/list", {"limit": limit, "archived": False})
        threads = result.get("data", result.get("threads", []))
        tasks = []
        for t in threads:
            status = t.get("status", {})
            stype = status.get("type", "unknown")
            tasks.append({
                "thread_id": t.get("id", ""),
                "status": stype,
                "preview": t.get("preview", ""),
                "cwd": t.get("cwd", ""),
                "created_at": t.get("createdAt", 0),
                "updated_at": t.get("updatedAt", 0),
            })
        return tasks

    async def get_task_status(self, thread_id: str) -> dict:
        """Get the status of a specific task."""
        # Use thread/list with filter, or read from cache
        if thread_id in self._thread_info:
            info = self._thread_info[thread_id]
            return {
                "thread_id": thread_id,
                "status": info.get("status", {}).get("type", "unknown"),
                "preview": info.get("preview", ""),
                "cwd": info.get("cwd", ""),
            }
        # Try to find it via thread/list
        tasks = await self.list_tasks(limit=100)
        for t in tasks:
            if t["thread_id"] == thread_id:
                return t
        return {"thread_id": thread_id, "status": "not_found"}

    async def cancel_task(self, thread_id: str) -> dict:
        """Cancel a running task by interrupting its turn."""
        await self.connect()
        info = self._thread_info.get(thread_id, {})
        turn_id = info.get("turn_id", "")
        if not turn_id:
            return {"thread_id": thread_id, "status": "no_active_turn"}
        try:
            result = await self._rpc("turn/interrupt", {
                "threadId": thread_id,
                "turnId": turn_id,
            })
            return {"thread_id": thread_id, "status": "cancelled", "result": result}
        except Exception as e:
            return {"thread_id": thread_id, "status": "cancel_failed", "error": str(e)}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp: Any = None
_client: Optional[CodexAppServerClient] = None


def get_client() -> CodexAppServerClient:
    global _client
    if _client is None:
        ws_url = os.environ.get("CODEX_APP_SERVER_WS", "ws://127.0.0.1:9876")
        _client = CodexAppServerClient(ws_url)
    return _client


def _register_mcp_tools() -> None:
    """Register MCP tools. Called from __main__ after FastMCP is available."""
    global mcp
    if mcp is not None or FastMCP is None:
        return
    mcp = FastMCP("codex-server", port=9877)

    @mcp.tool()
    async def codex_start_task(prompt: str, cwd: str = "/mnt/d/HermesProject",
                               sandbox: str = "danger-full-access") -> str:
        """Start a long-running Codex task.

        Args:
            prompt: The task description for Codex
            cwd: Working directory for the task
            sandbox: Sandbox mode (read-only, workspace-write, danger-full-access)
        """
        client = get_client()
        try:
            result = await client.start_task(prompt, cwd, sandbox)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    async def codex_task_status(thread_id: str) -> str:
        """Check the status of a Codex task.

        Args:
            thread_id: The thread ID returned by codex_start_task
        """
        client = get_client()
        try:
            result = await client.get_task_status(thread_id)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e), "thread_id": thread_id}, ensure_ascii=False)

    @mcp.tool()
    async def codex_list_tasks(limit: int = 10) -> str:
        """List all Codex tasks (threads).

        Args:
            limit: Max number of tasks to return
        """
        client = get_client()
        try:
            tasks = await client.list_tasks(limit)
            return json.dumps(tasks, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    async def codex_cancel_task(thread_id: str) -> str:
        """Cancel a running Codex task.

        Args:
            thread_id: The thread ID of the task to cancel
        """
        client = get_client()
        try:
            result = await client.cancel_task(thread_id)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e), "thread_id": thread_id}, ensure_ascii=False)


if __name__ == "__main__":
    if FastMCP is None:
        print("Missing mcp[cli]. Install: pip install 'mcp[cli]'")
        sys.exit(1)
    _register_mcp_tools()
    port = int(os.environ.get("CODEX_BRIDGE_PORT", "9877"))
    print(f"Starting Codex App Server Bridge on port {port}...")
    mcp.run(transport="sse")