#!/usr/bin/env python3
"""SAG MCP Token Bridge - injects fresh JWT on every request."""
import http.server, urllib.request, urllib.error, json, os, logging
from socketserver import ThreadingMixIn

SAG_BASE = "http://127.0.0.1:4173"
TOKEN_PATH = "/root/.hermes/.sag_token"
BRIDGE_PORT = 4176
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sag-mcp-bridge")

def get_token():
    try:
        tok = open(TOKEN_PATH).read().strip()
        if tok:
            return tok
    except OSError:
        pass
    return refresh_token()

def refresh_token():
    try:
        payload = json.dumps({"name": "hermes"}).encode()
        req = urllib.request.Request(
            f"{SAG_BASE}/api/v1/auth/login", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            tok = json.loads(resp.read()).get("access_token", "")
            if tok:
                with open(TOKEN_PATH, "w") as f:
                    f.write(tok)
                os.chmod(TOKEN_PATH, 0o600)
                logger.info("Token refreshed OK")
                return tok
    except Exception as e:
        logger.error("Token refresh failed: %s", e)
    return ""

PASSTHROUGH = {"content-type", "cache-control", "connection",
               "x-accel-buffering", "x-request-id", "transfer-encoding"}

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._proxy("GET")
    def do_POST(self):
        self._proxy("POST")
    def do_DELETE(self):
        self._proxy("DELETE")

    def _proxy(self, method):
        token = get_token()
        url = f"{SAG_BASE}{self.path}"
        body = None
        cl = self.headers.get("Content-Length")
        if cl:
            body = self.rfile.read(int(cl))
        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ("host", "authorization", "content-length"):
                headers[k] = v
        headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Length"] = str(len(body))
        
        
        try:
            req_obj = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req_obj, timeout=300) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() in PASSTHROUGH:
                        self.send_header(k, v)
                self.end_headers()
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                logger.info("Got 401, refreshing token and retrying")
                refresh_token()
                token = get_token()
                headers["Authorization"] = f"Bearer {token}"
                try:
                    req_obj = urllib.request.Request(url, data=body, headers=headers, method=method)
                    with urllib.request.urlopen(req_obj, timeout=300) as resp:
                        self.send_response(resp.status)
                        for k, v in resp.headers.items():
                            if k.lower() in PASSTHROUGH:
                                self.send_header(k, v)
                        self.end_headers()
                        while True:
                            chunk = resp.read(4096)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    return
                except Exception as e2:
                    logger.error("Retry failed: %s", e2)
                    self.send_error(502, str(e2))
                    return
            logger.error("Upstream error: %s", e)
            self.send_error(e.code, str(e))
        except Exception as e:
            logger.error("Proxy error: %s", e)
            self.send_error(502, str(e))

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

class ThreadingHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", BRIDGE_PORT), Handler)
    logger.info("SAG MCP bridge listening on port %d", BRIDGE_PORT)
    server.serve_forever()
