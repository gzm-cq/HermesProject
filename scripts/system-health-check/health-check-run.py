#!/usr/bin/env python3
"""
health-check-run.py — 全量健康巡检 + 飞书推送
依赖：health-check-all.py (共 7 项检查)
输出：stdout 摘要，通过 lark-cli --markdown 推送到飞书
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

LARK_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "")
SCRIPT = "/root/.hermes/scripts/health-check-all.py"
STATUS_EMOJI = {"ok": "✅", "warn": "⚠️", "fail": "🔴"}


def run_checks() -> dict:
    result = subprocess.run(
        ["python3", SCRIPT],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        print(f"脚本失败 (exit {result.returncode}): {result.stderr[:500]}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败: {e}", file=sys.stderr)
        print(f"stdout 前 500 字: {result.stdout[:500]}", file=sys.stderr)
        sys.exit(1)


def format_summary(data: dict) -> tuple[str, bool]:
    meta = data.pop("_meta", {})
    ts = meta.get("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    local_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    services = ["hermes", "bifrost", "hindsight", "sag", "postgres", "dashboard", "mcp", "orphan_scan", "memory_files"]
    lines = [f"# 🏥 系统健康巡检报告", f"**时间**: {local_time}", ""]

    results = {}
    all_ok = True
    for svc in services:
        info = data.get(svc, {})
        status = info.get("status", "fail")
        results[svc] = status
        if status != "ok":
            all_ok = False

    # 概览行
    emojis = " ".join(STATUS_EMOJI.get(results.get(s, "fail"), "❓") for s in services)
    n_ok = sum(1 for s in services if results.get(s) == "ok")
    lines.append(f"{emojis}")
    lines.append(f"**{n_ok}/{len(services)}** 项正常 | 巡检耗时 < 30s")
    lines.append("")

    # 逐项详情
    for svc in services:
        info = data.get(svc, {})
        status = info.get("status", "fail")
        emoji = STATUS_EMOJI.get(status, "❓")
        checks = info.get("checks", {})

        if svc == "hermes":
            alive = '✅' if checks.get('process_alive') else '❌'
            api = '✅' if str(checks.get('api_endpoint','')).startswith('200') else '❌'
            detail = f"进程 {checks.get('process_count', '?')} 个 {alive} | API {api}"
        elif svc == "bifrost":
            alive = '✅' if checks.get('process_alive') else '❌'
            container = str(checks.get('container_status',''))
            cok = '✅' if container == 'healthy' else '❌'
            models = checks.get('models_online', '?')
            detail = f"容器 {alive} | 健康 {cok} | 模型 {models} 个在线"
        elif svc == "hindsight":
            alive = '✅' if checks.get('process_alive') else '❌'
            health_raw = str(checks.get('health_endpoint',''))
            healthy = '✅' if 'healthy' in health_raw.lower() else '❌'
            pg = '✅' if checks.get('pg_connection') else '❌'
            detail = f"进程 {checks.get('process_count', '?')} 个 {alive} | 健康 {healthy} | PG {pg}"
        elif svc == "sag":
            alive = '✅' if checks.get('sag_process_alive') else '❌'
            mcp = '✅' if checks.get('sag_mcp_process_alive') else '❌'
            http = '✅' if checks.get('http_endpoint','') not in ('000','','') else '❌'
            pg = '✅' if checks.get('pg_connection') else '❌'
            detail = f"SAG 进程 {alive} | MCP {mcp} | HTTP {http} | PG {pg}"
        elif svc == "postgres":
            detail = f"连接数 {checks.get('active_connections', '?')} | pgvector {'✅' if checks.get('pgvector_enabled') else '❌'} | 磁盘 {checks.get('disk_usage_pct', '?')}%"
        elif svc == "mcp":
            up = checks.get('servers_up', '?')
            exp = checks.get('expected_servers', '?')
            sc = checks.get('server_counts', {})
            wmcp_ok = checks.get('windows_mcp_reachable', False)
            wmcp_http = checks.get('windows_mcp_http', 'N/A')
            parts = [f"{up}/{exp} 个在线"]
            sorted_names = ["axiom-wiki", "postgres", "codegraph", "sag", "windows-mcp"]
            for name in sorted_names:
                cnt = sc.get(name, 0)
                if name == "windows-mcp":
                    e = '✅' if wmcp_ok else '❌'
                    parts.append(f"win-mcp {e}({wmcp_http})")
                else:
                    e = '✅' if cnt > 0 else '❌'
                    parts.append(f"{name[:5]} {e}")
            detail = " | ".join(parts)
        elif svc == "orphan_scan":
            orphans = checks.get('orphan_pids', [])
            dead = checks.get('dead_containers', 0)
            detail = f"异常进程 {len(orphans)} 个 | 死容器 {dead}" if orphans or dead else "无异常进程"
        elif svc == "memory_files":
            parts = []
            for name in ["MEMORY.md", "USER.md"]:
                mf = checks.get(name, {})
                if "error" in mf:
                    parts.append(f"{name} ❌ {mf['error']}")
                else:
                    chars = mf.get("chars", 0)
                    limit = mf.get("limit", 0)
                    pct = mf.get("pct", 0)
                    e = "🟢" if pct < 75 else ("🟡" if pct < 90 else "🔴")
                    parts.append(f"{name} {e} {pct:.0f}% ({chars:,}/{limit:,})")
            detail = " | ".join(parts)
        else:
            detail = str(checks)

        lines.append(f"{emoji} **{svc.upper()}**: {detail}")

    if not all_ok:
        lines.append("")
        lines.append("### ❌ 异常项")
        for svc in services:
            if results.get(svc) != "ok":
                info = data.get(svc, {})
                notes = info.get("notes", "")
                lines.append(f"- **{svc.upper()}**: {info.get('status', 'fail')}")
                if notes:
                    lines.append(f"  备注：{notes}")

    lines.append("")
    lines.append("---")
    lines.append("_自动巡检 · 每日 8:00_")
    return "\n".join(lines), all_ok


def push_to_feishu(summary: str, dry_run: bool = False):
    if dry_run:
        print("\n--- DRY RUN ---\n")
        print(summary)
        print("\n--- DRY RUN END ---\n")
        return

    if not LARK_CHAT_ID:
        print("⚠️ 未配置 FEISHU_CHAT_ID，跳过飞书推送", file=sys.stderr)
        return

    # 用 lark-cli 推送 markdown 内容（--markdown 接受文本内容，非文件路径）
    result = subprocess.run(
        ["lark-cli", "im", "+messages-send",
         "--chat-id", LARK_CHAT_ID,
         "--markdown", summary,
         "--as", "bot"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode == 0:
        print(f"✅ 飞书推送成功: chat={LARK_CHAT_ID}")
    else:
        print(f"❌ 飞书推送失败: {result.stderr[:300]}", file=sys.stderr)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv

    print(f"🏥 健康巡检开始...")
    data = run_checks()
    print(f"✅ 巡检完成，生成摘要...")

    summary, all_ok = format_summary(data)

    if dry_run or not all_ok:
        push_to_feishu(summary, dry_run=dry_run)
        if not dry_run:
            issues_count = sum(1 for svc in data if data.get(svc, {}).get("status") != "ok")
            print(f"⚠️ 检测到 {issues_count} 项异常，已推送飞书通知")
    else:
        print(f"✅ 所有服务正常，跳过飞书通知 (no-news-good-news)")

    print(f"✅ 完成: {datetime.now().strftime('%H:%M:%S')}")
