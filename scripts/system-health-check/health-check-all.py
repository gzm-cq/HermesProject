#!/usr/bin/env python3
"""
health-check-all.py — 全量健康巡检，固定命令，零LLM
输出：stdout 完整 JSON，stderr 日志
"""

import json, os, subprocess, sys, glob, re
from datetime import datetime, timezone

TMPDIR = "/tmp/health-check-py"
os.makedirs(TMPDIR, exist_ok=True)

# Exclude self and parent from process counting (pgrep -f matches Hermes's shell wrapper)
_SCRIPT_PID = os.getpid()
_PARENT_PID = os.getppid()

def count_processes(pattern):
    """Count processes matching `pattern` in cmdline, excluding self & parent."""
    out, _, _ = run(f"ps -eo pid=,args= 2>/dev/null || true")
    count = 0
    for line in out.strip().split('\n'):
        parts = line.strip().split(None, 1)
        if not parts: continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid in (_SCRIPT_PID, _PARENT_PID):
            continue
        cmd = parts[1] if len(parts) > 1 else ""
        if cmd.startswith("postgres:"):   # skip postgres backend connections
            continue
        if re.search(pattern, cmd):
            count += 1
    return count


def detect_duplicate_processes(pattern: str) -> bool:
    """检测是否有重复进程（排除 granian worker 父子关系）。

    如果多个进程匹配同一 pattern 且存在父子关系 → 是 granian worker 模型，不算重复。
    如果多个进程匹配且没有父子关系 → 是真的重复。
    """
    out, _, _ = run("ps -eo pid=,ppid=,args= 2>/dev/null || true")
    pids: list[tuple[int, int, str]] = []
    for line in out.strip().split("\n"):
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if pid in (_SCRIPT_PID, _PARENT_PID):
            continue
        cmd = parts[2]
        if re.search(pattern, cmd):
            pids.append((pid, ppid, cmd))

    if len(pids) <= 1:
        return False  # 0 或 1 个进程，不重复

    # 检查是否所有额外进程都是主进程的子进程
    # granian worker 模式：main_pid → worker1, worker2, ...
    ppid_set = {ppid for pid, ppid, _ in pids}
    pid_set = {pid for pid, _, _ in pids}

    # 如果所有进程的 ppid 都在 pid_set 中（父子链），则是 worker 模型
    non_root = [pid for pid, ppid, _ in pids if ppid not in pid_set]
    if len(non_root) <= 1:
        return False  # 只有一个根进程，其余都是子节点 → 正常 worker 模型

    return True  # 多个根进程 → 真的重复

_SYSTEMD_PIDS = None

def get_systemd_pids():
    """Cache systemd service PIDs — these are expected to have ppid=1, not orphans."""
    global _SYSTEMD_PIDS
    if _SYSTEMD_PIDS is not None:
        return _SYSTEMD_PIDS
    out, _, _ = run(
        "for svc in hermes-gateway hindsight-daemon axiom-wiki-mcp-sse "
        "codegraph-mcp postgres-mcp sag sag-mcp moonbridge; do "
        "systemctl show -P MainPID \"$svc\" 2>/dev/null; done || true"
    )
    pids = set()
    for line in out.strip().split('\n'):
        try:
            p = int(line.strip())
            if p > 0:
                pids.add(p)
        except ValueError:
            pass
    _SYSTEMD_PIDS = pids
    return pids

def log(msg):
    print(f"[health-check-all] {msg}", file=sys.stderr)

def run(cmd, timeout=10, shell=None, **kwargs):
    """Run a command, return (stdout, stderr, rc).

    If ``cmd`` is a list, uses ``shell=False`` (secure).
    If ``cmd`` is a string, uses ``shell=True`` (backward compat for pipes/redirects).
    Pass ``shell=True`` or ``shell=False`` explicitly to override auto-detection.
    """
    if shell is None:
        shell = isinstance(cmd, str)
    try:
        r = subprocess.run(cmd, shell=shell, capture_output=True, text=True,
                          timeout=timeout, **kwargs)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", -1
    except Exception as e:
        return "", str(e), -1


def _psql(database: str, query: str, host: str = "127.0.0.1", port: int = 5434,
          user: str = "postgres", timeout: int = 10) -> tuple[str, str, int]:
    """Run a psql query with password from env var (or ~/.pgpass), no shell.

    Returns (stdout, stderr, rc).
    """
    password = os.environ.get("PGPASSWORD", "")
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    cmd = [
        "psql",
        "-h", host,
        "-p", str(port),
        "-U", user,
        "-d", database,
        "-c", query,
        "--no-align", "-t",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, env=env)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", -1
    except Exception as e:
        return "", str(e), -1

def write_check(name, status, checks):
    """Write check result to temp file."""
    data = {"status": status, "checks": checks}
    with open(os.path.join(TMPDIR, f"{name}.json"), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ============================================
# 1. Hermes
# ============================================
def check_hermes():
    proc_count = count_processes(r'hermes_cli.*gateway')
    dup = proc_count > 1

    out, _, _ = run("curl -s -o /dev/null -w '%{http_code}:%{time_total}' "
                    "http://127.0.0.1:8642/health --max-time 5 2>/dev/null || echo 'unreachable'")
    api_endpoint = out if out else "unreachable"

    out, _, _ = run("tail -3 ~/.hermes/logs/gateway.log 2>/dev/null | "
                    "grep -ciE 'error|traceback|exception' 2>/dev/null; echo ''")
    errors = int(out.strip().split('\n')[0] or 0)

    st = "ok"
    if proc_count == 0: st = "fail"
    elif dup: st = "warn"
    elif not api_endpoint.startswith("200"): st = "warn"

    write_check("hermes", st, {
        "process_alive": proc_count > 0,
        "process_count": proc_count,
        "duplicate_detected": dup,
        "api_endpoint": api_endpoint,
        "recent_errors": errors,
    })

# ============================================
# 2. Bifrost（替代 LiteLLM，2026-08-02 迁移）
# ============================================
def check_bifrost():
    # Bifrost 是 Docker 容器（network host），不是 systemd 进程
    out, _, _ = run("docker ps --filter name=^bifrost$ --format '{{.Names}}'")
    proc_alive = "bifrost" in out

    # Docker 健康状态
    out, _, _ = run("docker inspect bifrost --format '{{.State.Health.Status}}' 2>/dev/null")
    health_status = out.strip() or "unknown"

    # API 健康
    out, _, _ = run("curl -s http://127.0.0.1:4142/health --max-time 5")
    api_health = out or "unreachable"
    api_ok = '"status":"ok"' in api_health.replace(" ", "")

    # 模型列表（从 config 文件统计，/v1/models 只返回部分模型）
    out, _, _ = run("cat /root/.bifrost/data/config.json 2>/dev/null || echo '{}'")
    models_online = 0
    model_count_error = ""
    if out.strip():
        try:
            import json as _json
            _cfg = _json.loads(out)
            _providers = _cfg.get("providers", {})
            _all_models = set()
            for _pname, _pdata in _providers.items():
                for _k in _pdata.get("keys", []):
                    for _m in _k.get("models", []):
                        _all_models.add(_m)
            models_online = len(_all_models)
        except Exception as e:
            model_count_error = str(e)[:200]

    st = "ok"
    if not proc_alive: st = "fail"
    elif health_status == "unhealthy": st = "warn"
    elif not api_ok: st = "warn"
    elif models_online <= 0: st = "warn"

    write_check("bifrost", st, {
        "process_alive": proc_alive,
        "container_status": health_status,
        "api_health": api_health,
        "models_online": models_online,
        "model_count_source": "api:/v1/models",
        "model_count_error": model_count_error,
    })

# ============================================
# 3. Hindsight
# ============================================
def check_hindsight():
    proc_count = count_processes(r'hindsight-api')
    dup = proc_count > 1

    # Try port 9177 (actual daemon port), then 2190, then 8000
    out, _, rc = run("curl -s http://127.0.0.1:9177/health --max-time 5")
    if rc != 0:
        out, _, rc = run("curl -s http://127.0.0.1:2190/health --max-time 5")
    if rc != 0:
        out, _, _ = run("curl -s http://127.0.0.1:8000/health --max-time 5")
    health_resp = out or "unreachable"

    out, _, rc = _psql("hindsight", "SELECT 1 AS alive")
    pg_ok = out == "1"

    st = "ok"
    if proc_count == 0: st = "fail"
    elif dup: st = "warn"
    elif not pg_ok: st = "warn"

    write_check("hindsight", st, {
        "process_alive": proc_count > 0,
        "process_count": proc_count,
        "duplicate_detected": dup,
        "health_endpoint": health_resp[:100],
        "pg_connection": pg_ok,
    })

# ============================================
# 4. SAG (SQL-Retrieval Augmented Generation)
# ============================================
def check_sag():
    """Check SAG server + SAG MCP SSE bridge via systemd + HTTP."""
    # SAG main server (port 8080 internally, but we check via systemd)
    out, _, _ = run("systemctl show -P MainPID sag.service 2>/dev/null || echo 0")
    sag_pid = int(out or 0)
    sag_alive = sag_pid > 0

    # SAG MCP SSE bridge (port 4175)
    out, _, _ = run("systemctl show -P MainPID sag-mcp.service 2>/dev/null || echo 0")
    sag_mcp_pid = int(out or 0)
    sag_mcp_alive = sag_mcp_pid > 0

    # HTTP health check on MCP SSE port
    out, _, rc = run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:4175/ --max-time 5 2>/dev/null || echo '000'")
    sag_http = out.strip() if out else "000"
    sag_reachable = sag_http not in ("000", "")

    # DB connectivity (sag_lite database in shared-postgres:5434)
    out, _, rc = _psql("sag_lite", "SELECT 1 AS alive")
    sag_pg_ok = out.strip() == "1"

    st = "ok"
    if not sag_alive and not sag_mcp_alive: st = "fail"
    elif not sag_reachable: st = "warn"
    elif not sag_pg_ok: st = "warn"

    write_check("sag", st, {
        "sag_process_alive": sag_alive,
        "sag_mcp_process_alive": sag_mcp_alive,
        "sag_pid": sag_pid,
        "sag_mcp_pid": sag_mcp_pid,
        "http_endpoint": sag_http,
        "pg_connection": sag_pg_ok,
    })

# ============================================
# 5. PostgreSQL
# ============================================
def check_postgres():
    # Postgres runs in Docker (shared-postgres on :5434)
    out, _, _ = run(
        "docker ps --filter 'name=shared-postgres' --filter 'status=running' "
        "--format '{{.Names}}' 2>/dev/null || true"
    )
    pg_alive = bool(out.strip())
    pg5434 = 1 if pg_alive else 0
    pg_total = pg5434
    other_port = False

    out, _, _ = _psql("postgres", "SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
    active_conn = int(out or 0)

    out, _, _ = _psql("postgres", "SELECT datname FROM pg_database WHERE datistemplate = false")
    dbs = [d.strip() for d in out.split("\n") if d.strip()]

    out, _, _ = _psql("hindsight", "SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
    pgvector = int(out or 0)

    out, _, _ = run("df -h / | tail -1 | awk '{print $5}' | tr -d '%' || echo 0")
    disk_pct = int(out or 0)

    st = "ok"
    if pg5434 == 0: st = "fail"
    elif other_port: st = "warn"
    elif disk_pct > 85: st = "warn"

    write_check("postgres", st, {
        "process_alive": pg5434 > 0,
        "process_5434_count": pg5434,
        "total_postgres": pg_total,
        "other_port_detected": other_port,
        "active_connections": active_conn,
        "databases": dbs,
        "pgvector_enabled": pgvector > 0,
        "disk_usage_pct": disk_pct,
    })

# ============================================
# 6. MCP
# ============================================
# All 5 MCP servers from config.yaml (mcp_servers section)
MCP_SERVERS = ["axiom-wiki", "postgres", "codegraph", "sag", "windows-mcp"]
MCP_PATTERNS = {
    "axiom-wiki":  r'axiom-wiki-mcp-sse\.mjs',
    "postgres":    r'postgres-mcp-sse\.mjs',
    "codegraph":   r'codegraph-mcp-sse\.mjs',
}
# windows-mcp runs on Windows host, checked via HTTP endpoint
WINDOWS_MCP_URL = "http://127.0.0.1:8000/sse"
WINDOWS_MCP_TIMEOUT = 5

def check_mcp():
    out, _, _ = run("systemctl show -P MainPID hermes-gateway 2>/dev/null || echo 0")
    gateway_pid = int(out or 0)

    out, _, _ = run("grep -c 'mcp_servers:' /root/.hermes/config.yaml 2>/dev/null || echo 0")
    config_present = int(out.strip().split('\n')[-1] or 0) > 0

    # Count each MCP server separately
    server_counts = {}
    for name, pattern in MCP_PATTERNS.items():
        c = count_processes(pattern)
        server_counts[name] = c
    # sag checked via systemd (not an SSE bridge process pattern)
    out, _, _ = run("systemctl is-active sag.service 2>/dev/null || echo inactive")
    server_counts["sag"] = 1 if out.strip() == "active" else 0

    # List all MCP PIDs excluding self & parent
    out, _, _ = run("ps -eo pid=,args= 2>/dev/null || true")
    mcp_pids = []
    for line in out.strip().split('\n'):
        parts = line.strip().split(None, 1)
        if not parts: continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid in (_SCRIPT_PID, _PARENT_PID):
            continue
        cmd = parts[1] if len(parts) > 1 else ""
        if cmd.startswith("postgres:"):
            continue
        if any(re.search(pat, cmd) for pat in MCP_PATTERNS.values()):
            mcp_pids.append(pid)

    # Check windows-mcp via HTTP endpoint (Windows host)
    wmcp_reachable = False
    wmcp_http = ""
    out2, err2, rc2 = run(
        f'curl -s -o /dev/null -w "%{{http_code}}" '
        f'--connect-timeout {WINDOWS_MCP_TIMEOUT} --max-time {WINDOWS_MCP_TIMEOUT+3} '
        f'{WINDOWS_MCP_URL}',
        timeout=WINDOWS_MCP_TIMEOUT+5
    )
    wmcp_http = out2.strip()
    wmcp_reachable = wmcp_http not in ("", "000", "fail")
    server_counts["windows-mcp"] = 1 if wmcp_reachable else 0

    servers_up = sum(1 for c in server_counts.values() if c > 0)
    expected = len(MCP_SERVERS)

    st = "ok"
    if servers_up == 0: st = "fail"
    elif servers_up < expected: st = "warn"

    write_check("mcp", st, {
        "servers_up": servers_up,
        "expected_servers": expected,
        "server_counts": server_counts,
        "config_present": config_present,
        "gateway_pid": gateway_pid,
        "mcp_pids": mcp_pids,
        "windows_mcp_reachable": wmcp_reachable,
        "windows_mcp_http": wmcp_http,
    })


# ============================================
# 7. Dashboard (Hermes Agent Dashboard, port 9119)
# ============================================
def check_dashboard():
    """Check Hermes Dashboard service via systemd + HTTP port 9119.
    Dashboard depends on hermes-gateway.service (After=), so it's
    expected to be down if the gateway is down."""
    out, _, _ = run("systemctl show -P MainPID hermes-dashboard.service 2>/dev/null || echo 0")
    db_pid = int(out or 0)
    db_alive = db_pid > 0

    out, _, _ = run("systemctl is-active hermes-dashboard.service 2>/dev/null || echo inactive")
    svc_active = out.strip() == "active"

    # HTTP check — dashboard returns HTML on /
    out, _, _ = run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9119/ --max-time 5 2>/dev/null || echo '000'")
    db_http = out.strip() if out else "000"
    db_reachable = db_http not in ("000", "")

    st = "ok"
    if not db_alive and not svc_active:
        st = "fail"
    elif not db_reachable:
        st = "warn"

    write_check("dashboard", st, {
        "process_alive": db_alive,
        "svc_active": svc_active,
        "pid": db_pid,
        "http_endpoint": db_http,
        "port": 9119,
    })

# ============================================
# 8. Moon Bridge (Responses API converter)
# ============================================
def check_moonbridge():
    """Check Moon Bridge service via systemd + port 38440."""
    out, _, _ = run("systemctl show -P MainPID moonbridge.service 2>/dev/null || echo 0")
    mb_pid = int(out or 0)
    mb_alive = mb_pid > 0

    # HTTP check — moonbridge returns 404 on / which means it's alive
    out, _, _ = run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:38440/ --max-time 5 2>/dev/null || echo '000'")
    mb_http = out.strip() if out else "000"
    mb_reachable = mb_http not in ("000", "")

    st = "ok"
    if not mb_alive: st = "fail"
    elif not mb_reachable: st = "warn"

    write_check("moonbridge", st, {
        "process_alive": mb_alive,
        "pid": mb_pid,
        "http_endpoint": mb_http,
        "port": 38440,
    })


# ============================================
# 8. Memory Files Usage
# ============================================
MEMORY_FILES = [
    ("MEMORY.md", "/root/.hermes/memories/MEMORY.md", 50000),
    ("USER.md",   "/root/.hermes/memories/USER.md",   15000),
]

def check_memory_files():
    checks = {}
    pcts = []
    for name, path, limit in MEMORY_FILES:
        try:
            with open(path) as f:
                size = len(f.read())
            pct = round(size / limit * 100, 1)
            pcts.append(pct)
            checks[name] = {
                "chars": size,
                "limit": limit,
                "pct": pct,
                "status": "ok" if pct < 75 else ("warn" if pct < 90 else "fail"),
            }
        except Exception as e:
            checks[name] = {"error": str(e)}

    max_pct = max(pcts) if pcts else 0
    st = "ok"
    if max_pct >= 90: st = "fail"
    elif max_pct >= 75: st = "warn"
    write_check("memory_files", st, checks)
    log(f"  memory_files: {st} ({max_pct}%)")

# ============================================
# 8. Orphan Scan
# ============================================
def check_orphans():
    out, _, _ = run("ps -eo pid=,ppid=,args= 2>/dev/null || true")
    orphans = []
    for line in out.strip().split('\n'):
        parts = line.strip().split(None, 2)
        if not parts: continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except (ValueError, IndexError):
            continue
        if pid in (_SCRIPT_PID, _PARENT_PID):
            continue
        if pid in get_systemd_pids():
            continue
        cmd = parts[2] if len(parts) > 2 else ""
        if 'hermes' in cmd.lower() and ppid == 1:
            orphans.append(pid)
    orphan_count = len(orphans)

    out, _, _ = run(
        "docker ps -a --filter 'status=exited' "
        "--format '{{.Names}}' 2>/dev/null | wc -l || echo 0"
    )
    dead_containers = int(out or 0)

    out, _, _ = run(
        "docker ps -a --filter 'status=created' --format '{{.Names}}' "
        "2>/dev/null | wc -l || echo 0"
    )
    dangling = int(out or 0)

    st = "ok"
    if orphan_count > 5: st = "warn"
    if dead_containers > 5: st = "warn"

    write_check("orphan_scan", st, {
        "orphan_count": orphan_count,
        "orphan_pids": orphans,
        "dead_containers": dead_containers,
        "dangling_containers": dangling,
    })

# ============================================
# MAIN
# ============================================
if __name__ == "__main__":
    log("starting")

    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    checks = {
        "hermes": check_hermes,
        "bifrost": check_bifrost,
        "hindsight": check_hindsight,
        "sag": check_sag,
        "postgres": check_postgres,
        "mcp": check_mcp,
        "dashboard": check_dashboard,
        "moonbridge": check_moonbridge,
    }
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fn): name for name, fn in checks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
                log(f"  {name}: ok")
            except Exception as e:
                log(f"  {name}: FAILED: {e}")
                write_check(name, "fail", {"error": str(e)})

    check_orphans()
    log("  orphan_scan: ok")

    check_memory_files()

    # Merge
    result = {}
    for f in sorted(glob.glob(os.path.join(TMPDIR, "*.json"))):
        name = os.path.splitext(os.path.basename(f))[0]
        with open(f) as fh:
            result[name] = json.load(fh)
    
    result["_meta"] = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script_version": "1.0.0"
    }
    
    print(json.dumps(result, indent=2, ensure_ascii=False))
    log("complete")
