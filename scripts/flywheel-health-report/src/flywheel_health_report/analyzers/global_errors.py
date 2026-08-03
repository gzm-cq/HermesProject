from __future__ import annotations

import re
from collections import Counter

from ..config import TH


def analyze_global_errors(error_log_path: Path, filter_date: str) -> tuple[list[dict], dict, dict]:
    """Analyze global errors from logs/errors.log."""
    if not error_log_path.exists():
        return [], {"status": "no_data"}, {}

    levels: Counter = Counter()
    modules: Counter = Counter()
    error_keywords: Counter = Counter()
    total_lines = 0
    date_lines = 0
    filtered_errors = 0

    line_re = re.compile(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}[,\d]* (\w+) ([\w\.]+):")

    def is_restart_cascade_noise(level: str, module: str, line: str) -> bool:
        if level != "ERROR":
            return False
        low = line.lower()
        if module == "asyncio":
            if "unclosed client session" in low or "unclosed connector" in low:
                return True
            # Task exception 通常由连接关闭触发，无需额外匹配 connectionclosedok
            if "task exception was never retrieved" in low:
                return True
        if module == "lark":
            # 实际日志格式: "receive message loop exit, err: sent 1000 (OK)"
            if "receive message loop exit" in low and "1000" in line:
                return True
        if module.startswith("gateway.platforms.weixin") and "rate limited" in low:
            return True
        # MCP SSE reader 错误（网关重启时连接中断）
        if module.startswith("mcp") and ("sse" in low or "sse_reader" in low):
            return True
        if "hindsight" in low and ("daemon" in low or "not ready" in low or "unavailable" in low):
            return True
        return False

    with open(error_log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            m = line_re.match(line)
            if m:
                date = m.group(1)
                level = m.group(2)
                module = m.group(3)
                if date == filter_date:
                    date_lines += 1
                    if is_restart_cascade_noise(level, module, line):
                        filtered_errors += 1
                        continue
                    levels[level] += 1
                    modules[module] += 1
                    low = line.lower()
                    for kw in ["error", "exception", "traceback", "failed",
                               "timeout", "denied", "connection", "not found"]:
                        if kw in low:
                            error_keywords[kw] += 1

    if date_lines == 0:
        return [], {"status": "no_data", "total_in_log": total_lines}, {}

    error_count = levels.get("ERROR", 0)
    warning_count = levels.get("WARNING", 0)
    total_issue = error_count + warning_count
    error_pct = round(error_count / total_issue * 100, 1) if total_issue else 0

    top_modules = [{"module": m, "count": c} for m, c in modules.most_common(10)]
    top_keywords = [{"keyword": k, "count": c} for k, c in error_keywords.most_common()]

    issues = []
    if error_count > 0:
        if error_pct > TH["error_rate_high_pct"]:
            issues.append({
                "severity": "P1",
                "flywheel": "系统",
                "desc": f"全局 ERROR 占比 {error_pct}%（阈值 {TH['error_rate_high_pct']}%）",
                "detail": f"当日 {error_count} 条 ERROR / {total_issue} 条问题日志",
            })
        else:
            issues.append({
                "severity": "P2",
                "flywheel": "系统",
                "desc": f"当日有 {error_count} 条 ERROR 日志",
                "detail": "建议关注 top 模块的错误趋势",
            })

    results = {
        "total_logs": total_lines,
        "date_logs": date_lines,
        "error_count": error_count,
        "warning_count": warning_count,
        "filtered_errors": filtered_errors,
        "error_pct": error_pct,
        "top_modules": top_modules,
        "top_keywords": top_keywords,
    }
    trend = {}
    return issues, results, trend
