#!/usr/bin/env python3
"""cache_monitor.py — skill 匹配缓存命中率监控（B 方案可观测性）。

读取生产缓存文件（~/.hermes/data/skill_match_cache.json）中的 stats 字段，
报告全局命中率 + 最近 24 小时窗口趋势。可选解析 trace.log 交叉验证。

用法:
    python3 cache_monitor.py [--cache PATH] [--trace LOG] [--raw]

示例:
    python3 cache_monitor.py
    python3 cache_monitor.py --trace /root/.hermes/plugins/knowledge-navigation/trace.log
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_CACHE = Path.home() / ".hermes" / "data" / "skill_match_cache.json"
DEFAULT_TRACE = Path.home() / ".hermes" / "plugins" / "knowledge-navigation" / "trace.log"


def load_stats(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {"error": f"cache 文件不存在: {cache_path}"}
    with open(cache_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    stats = payload.get("stats") or {}
    stats["entries"] = len(payload.get("entries", []))
    stats["cache_path"] = str(cache_path)
    return stats


def load_trace(trace_path: Path) -> dict:
    """从 trace.log 统计 cache-hit / cache-miss 行（交叉验证）。"""
    if not trace_path.exists():
        return {"error": f"trace 文件不存在: {trace_path}"}
    hits = misses = 0
    hits_by_query: dict[str, int] = {}
    with open(trace_path, "r", encoding="utf-8") as f:
        for line in f:
            if "Skill match (cache-hit)" in line:
                hits += 1
            elif "Skill match (cache-miss)" in line:
                misses += 1
    return {
        "trace_hits": hits,
        "trace_misses": misses,
        "trace_hit_rate": round(hits / (hits + misses), 4) if (hits + misses) else 0.0,
    }


def format_report(stats: dict, trace: dict | None = None) -> str:
    if "error" in stats:
        return stats["error"]

    lines = [
        "═" * 52,
        "  Skill Match Cache 命中率监控",
        "═" * 52,
        f"  缓存文件     : {stats.get('cache_path', '')}",
        f"  条目数       : {stats.get('entries', 0)} / {stats.get('max_entries', '?')}",
        f"  阈值         : ctx={stats.get('ctx_threshold')} query={stats.get('query_threshold')}",
        f"  TTL          : {stats.get('ttl_seconds', 0) / 3600:.0f}h",
        "─" * 52,
        f"  lookup 总次数: {stats.get('total_lookups', 0)}",
        f"  命中/未命中  : {stats.get('total_hits', 0)} / {stats.get('total_misses', 0)}",
        f"  写入次数     : {stats.get('total_stores', 0)}",
        f"  ★ 全局命中率 : {stats.get('hit_rate', 0) * 100:.1f}%",
    ]

    if trace and "error" not in trace:
        lines += [
            "─" * 52,
            "  trace.log 交叉验证:",
            f"    hit/miss  : {trace.get('trace_hits', 0)} / {trace.get('trace_misses', 0)}",
            f"    ★ 命中率  : {trace.get('trace_hit_rate', 0) * 100:.1f}%",
        ]

    hourly = stats.get("hourly", [])
    if hourly:
        lines += ["─" * 52, "  最近 24h 窗口:"]
        for h in hourly[-24:]:
            lookups = h.get("lookups", 0)
            rate = (h.get("hits", 0) / lookups * 100) if lookups else 0
            bar = "█" * int(rate / 5)
            lines.append(f"    {h['hour']}  {lookups:>4}次  命中率 {rate:5.1f}%  {bar}")
    lines.append("═" * 52)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="skill match cache 命中率监控")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--trace", type=Path, default=None)
    ap.add_argument("--raw", action="store_true", help="输出原始 JSON")
    args = ap.parse_args()

    stats = load_stats(args.cache)
    trace = None
    if args.trace is not None:
        trace = load_trace(args.trace)

    if args.raw:
        out = dict(stats)
        if trace and "error" not in trace:
            out["trace"] = trace
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(format_report(stats, trace))
    return 0


if __name__ == "__main__":
    sys.exit(main())