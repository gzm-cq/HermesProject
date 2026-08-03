from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime

from ..config import TH, _CRON_TO_FLYWHEEL
from ..parsers import scan_cron_log_errors


def analyze_cron_jobs(states: dict[str, dict],
                      cron_log_dir: Path,
                      now: datetime) -> tuple[list[dict], dict, dict]:
    issues = []
    table = {}
    elapsed_annotations = {}

    # Collect elapsed times for deviation detection
    elapsed_by_task: dict[str, list[float]] = defaultdict(list)

    for name, s in sorted(states.items()):
        status = s.get("status", "unknown")
        run_at = s.get("run_at", "—")
        elapsed = s.get("elapsed_seconds", 0)
        last_error = s.get("last_error", "")
        table[name] = {"status": status, "run_at": run_at, "elapsed": elapsed}

        if status == "fail":
            issues.append({
                "severity": "P0",
                "flywheel": _CRON_TO_FLYWHEEL.get(name, name),
                "desc": f"Cron `{name}` 运行失败",
                "detail": last_error or f"status={status}",
            })

        # Collect elapsed for deviation analysis (only success runs with positive elapsed)
        if status == "success" and isinstance(elapsed, (int, float)) and elapsed > 0:
            elapsed_by_task[name].append(float(elapsed))

    # Check elapsed deviation per task
    for name, times in elapsed_by_task.items():
        if len(times) < 3:
            continue  # not enough history
        mean_t = statistics.mean(times)
        stdev_t = statistics.stdev(times) if len(times) > 1 else 0
        latest = times[-1]
        if stdev_t > 0 and abs(latest - mean_t) > TH["elapsed_deviation_sigma"] * stdev_t:
            direction = "↑ 变慢" if latest > mean_t else "↓ 变快"
            pct = ((latest - mean_t) / mean_t) * 100
            if abs(pct) > TH["elapsed_significant_pct"]:  # only report significant changes
                issues.append({
                    "severity": "P1",
                    "flywheel": _CRON_TO_FLYWHEEL.get(name, name),
                    "desc": f"`{name}` 执行耗时异常 ({direction}: {pct:+.0f}%)",
                    "detail": f"历史均值 {mean_t:.0f}s, 本次 {latest:.0f}s",
                })
                elapsed_annotations[name] = f"⚠️ {pct:+.0f}% vs 均值 {mean_t:.0f}s"

    # Scan cron logs for hidden errors
    hidden_errors = scan_cron_log_errors(cron_log_dir, states, now)
    for name, errs in hidden_errors.items():
        issues.append({
            "severity": "P1",
            "flywheel": _CRON_TO_FLYWHEEL.get(name, name),
            "desc": f"`{name}` 运行成功但日志含错误",
            "detail": errs[0][:200],
        })

    return issues, table, elapsed_annotations
