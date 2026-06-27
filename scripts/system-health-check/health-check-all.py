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
        "for svc in hermes-gateway hindsight-daemon litellm axiom-wiki-mcp-sse; do "
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

def run(cmd, timeout=10, **kwargs):
    """Run shell command, return (stdout, stderr, rc)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout, **kwargs)
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
# 2. LiteLLM
# ============================================
def check_litellm():
    proc_count = count_processes(r'litellm.*--config')
    dup = detect_duplicate_processes(r'litellm.*--config')

    out, _, _ = run("curl -s http://127.0.0.1:4142/health/liveliness --max-time 5")
    liveliness = out or "unreachable"

    # Do NOT call /model/info here. In DB-backed LiteLLM setups, /model/info
    # may try to infer providers from provider-native model names (for example
    # ark-code-latest) and emit false LLM Provider NOT provided errors even
    # though normal chat completions route correctly via custom_openai. Count
    # configured models directly from the LiteLLM DB instead.
    out, err, rc = run(
        "docker exec shared-postgres psql -h localhost -p 5432 -U postgres -d litellm "
        "-At -c 'SELECT count(*) FROM \"LiteLLM_ProxyModelTable\"'",
        timeout=10,
    )
    models_online = 0
    model_count_error = ""
    if rc == 0 and out.strip():
        try:
            models_online = int(out.strip().split("\n")[-1])
        except ValueError:
            model_count_error = out[:200]
    else:
        model_count_error = (err or out or "unknown error")[:200]

    out, _, _ = run("curl -s -o /dev/null -w '%{http_code}' "
                    "http://127.0.0.1:4142/ui/login --max-time 5")
    ui_reachable = out in ("200", "302", "307")

    st = "ok"
    if proc_count == 0: st = "fail"
    elif dup: st = "warn"
    elif 'alive' not in liveliness.lower() and liveliness not in ("healthy",): st = "warn"
    elif models_online <= 0: st = "warn"

    write_check("litellm", st, {
        "process_alive": proc_count > 0,
        "process_count": proc_count,
        "duplicate_detected": dup,
        "liveliness": liveliness,
        "models_online": models_online,
        "model_count_source": "db:LiteLLM_ProxyModelTable",
        "model_count_error": model_count_error,
        "ui_reachable": ui_reachable,
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

    out, _, _ = run(
        "PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres -d hindsight "
        "-c \"SELECT 1 AS alive\" --no-align -t 2>/dev/null || echo 'failed'"
    )
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
# 4. Dify
# ============================================
def check_dify():
    out, _, _ = run(
        "docker ps --filter 'name=dify' --format '{{.Names}}' 2>/dev/null || true"
    )
    names = [n.strip() for n in out.split("\n") if n.strip()]
    container_count = len(names)
    all_up = True
    for name in names:
        s_out, _, _ = run(
            f"docker ps --filter 'name={name}' --filter 'status=running' "
            f"--format '{{.Names}}' 2>/dev/null || true"
        )
        if not s_out.strip():
            all_up = False
            break

    dupes = set()
    seen = set()
    for n in names:
        if n in seen: dupes.add(n)
        seen.add(n)
    has_dupes = len(dupes) > 0

    out, _, _ = run(
        "docker exec dify-api-1 curl -s http://localhost:5001/health --max-time 5 "
        "2>/dev/null || echo 'unreachable'"
    )
    api_health = out[:200] if out else "unreachable"

    out, _, _ = run(
        "docker ps --filter 'name=dify-web-1' --filter 'status=running' "
        "--format '{{.Status}}' 2>/dev/null || echo 'not running'"
    )
    web_reachable = out != "not running" and "Up" in out

    st = "ok"
    if container_count == 0: st = "fail"
    elif not all_up: st = "warn"
    elif has_dupes: st = "warn"

    write_check("dify", st, {
        "containers_running": container_count,
        "container_names": names,
        "all_up": all_up,
        "duplicate_detected": has_dupes,
        "api_health": api_health,
        "web_reachable": web_reachable,
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

    out, _, _ = run(
        "PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres "
        "-t -A -c \"SELECT count(*) FROM pg_stat_activity WHERE state = 'active';\" "
        "2>/dev/null || echo 0"
    )
    active_conn = int(out or 0)

    out, _, _ = run(
        "PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres "
        "-t -A -c \"SELECT datname FROM pg_database WHERE datistemplate = false;\" "
        "2>/dev/null || true"
    )
    dbs = [d.strip() for d in out.split("\n") if d.strip()]

    out, _, _ = run(
        "PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres -d hindsight "
        "-t -A -c \"SELECT count(*) FROM pg_extension WHERE extname = 'vector';\" "
        "2>/dev/null || echo 0"
    )
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
# All 6 MCP servers from config.yaml (mcp_servers section)
MCP_SERVERS = ["axiom-wiki", "postgres", "filesystem", "codegraph", "openclaw", "windows-mcp"]
MCP_PATTERNS = {
    "axiom-wiki":      r'axiom-wiki',
    "postgres":        r'server-postgres',
    "filesystem":      r'server-filesystem',
    "codegraph":       r'codegraph.*(?:serve.*mcp|sse-bridge)',
    "openclaw":        r'openclaw-sse-bridge',
}
# windows-mcp runs on Windows host, checked via HTTP endpoint
WINDOWS_MCP_URL = "http://127.0.0.1:8000/sse"
WINDOWS_MCP_TIMEOUT = 5

def check_mcp():
    out, _, _ = run("systemctl show -P MainPID hermes-gateway 2>/dev/null || echo 0")
    gateway_pid = int(out or 0)

    out, _, _ = run("grep -c 'mcp_servers:' /root/.hermes/config.yaml 2>/dev/null || echo 0")
    config_present = int(out.strip().split('\\n')[-1] or 0) > 0

    # Count each MCP server separately
    server_counts = {}
    for name, pattern in MCP_PATTERNS.items():
        c = count_processes(pattern)
        server_counts[name] = c

    # List all MCP PIDs excluding self & parent
    out, _, _ = run("ps -eo pid=,args= 2>/dev/null || true")
    mcp_pids = []
    for line in out.strip().split('\\n'):
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
        "docker ps -a --filter 'name=dify' --filter 'status=exited' "
        "--format '{{.Names}}' 2>/dev/null | wc -l || echo 0"
    )
    dead_dify = int(out or 0)

    out, _, _ = run(
        "docker ps -a --filter 'status=created' --format '{{.Names}}' "
        "2>/dev/null | wc -l || echo 0"
    )
    dangling = int(out or 0)

    st = "ok"
    if orphan_count > 5: st = "warn"
    if dead_dify > 0: st = "warn"

    write_check("orphan_scan", st, {
        "orphan_count": orphan_count,
        "orphan_pids": orphans,
        "dead_dify_containers": dead_dify,
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
        "litellm": check_litellm,
        "hindsight": check_hindsight,
        "dify": check_dify,
        "postgres": check_postgres,
        "mcp": check_mcp,
    }
    
    with ThreadPoolExecutor(max_workers=7) as executor:
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
