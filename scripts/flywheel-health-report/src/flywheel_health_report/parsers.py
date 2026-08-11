"""parsers.py — 数据加载/解析/持久化。

从 flywheel-health-report.py L191-351 搬入。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import ACTIVE_CRON_JOBS, JOBS_JSON_SUBPATH


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save_json(path: Path, data: dict) -> None:
    """Write JSON to file, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_jsonl(path: Path) -> list[dict]:
    results = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except (FileNotFoundError, OSError):
        pass
    return results


def _rotate_jsonl(path: Path, keep: int = 30) -> None:
    """Trim JSONL file to last N records."""
    records = _load_jsonl(path)
    if len(records) <= keep:
        return
    records = records[-keep:]
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_daily_summary(data_flywheel: Path, summary: dict) -> None:
    """Append daily summary to history JSONL, dedup by date, keep last 30 days."""
    path = data_flywheel / "daily-summary-history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = _load_jsonl(path)
    date = summary.get("date")
    records = [r for r in records if r.get("date") != date]
    records.append(summary)
    records = records[-30:]
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_daily_summary(data_flywheel: Path) -> list[dict]:
    """Load daily summary history."""
    path = data_flywheel / "daily-summary-history.jsonl"
    return _load_jsonl(path)


def parse_cron_states(cron_state_dir: Path) -> dict[str, dict]:
    states = {}
    if not cron_state_dir.is_dir():
        return states
    for f in sorted(cron_state_dir.glob("*.json")):
        data = _load_json(f)
        if data and isinstance(data, dict):
            name = data.get("job_name", f.stem)
            if name not in ACTIVE_CRON_JOBS:
                continue
            states[name] = data
    return states


def parse_cron_jobs_json(hermes_home: Path,
                         existing_states: dict[str, dict]) -> dict[str, dict]:
    """Parse cron/jobs.json to supplement jobs that lack cron-state files.

    Jobs tracked by cron_common.sh write rich state files to cron-state/.
    Agent-based or standalone-script jobs (e.g. dream-daily, 每周深度研究)
    only have their status in jobs.json. This function reads jobs.json and
    returns entries for ACTIVE_CRON_JOBS not already in *existing_states*.
    """
    supplementary: dict[str, dict] = {}
    jobs_path = hermes_home / JOBS_JSON_SUBPATH
    data = _load_json(jobs_path)
    if not data or not isinstance(data, dict):
        return supplementary
    jobs = data.get("jobs", [])
    if not isinstance(jobs, list):
        return supplementary

    # Map jobs.json status → cron-state status
    _STATUS_MAP = {"ok": "success", "error": "fail", "skipped": "skipped"}

    for job in jobs:
        if not isinstance(job, dict):
            continue
        name = job.get("name", "")
        if not name or name not in ACTIVE_CRON_JOBS:
            continue
        if name in existing_states:
            continue  # cron-state file already has richer data
        if not job.get("enabled", True):
            continue  # skip disabled jobs

        last_status = job.get("last_status", "unknown")
        supplementary[name] = {
            "job_name": name,
            "status": _STATUS_MAP.get(last_status, "unknown"),
            "run_at": job.get("last_run_at", "—"),
            "elapsed_seconds": 0,  # not tracked in jobs.json
            "last_error": job.get("last_error") or "",
            "source": "jobs.json",  # mark origin for report rendering
        }
    return supplementary


def parse_trace_log(trace_path: Path,
                    filter_dates: list[str] | None = None) -> dict[str, list[dict]]:
    """Parse trace.log, optionally filtered to entries matching filter_dates."""
    events: dict[str, list[dict]] = {
        "router_mask": [],
        "recall_success": [],
        "recall_empty": [],
        "recall_empty_results": [],
        "recall_error": [],
        "hindsight_fail_kt_fallback": [],
        "multi_hop_expand": [],
        "recall_timeout": [],
        "recall_sag": [],
        "token_usage": [],
        "sag_merge": [],
        "eval_query_bypass": [],
    }
    if not trace_path.is_file():
        return events
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if filter_dates is not None:
                ts = d.get("timestamp", "")
                if not any(ts.startswith(d) for d in filter_dates):
                    continue
            evt = d.get("event", "")
            if evt in events:
                events[evt].append(d)
        except json.JSONDecodeError:
            pass
    return events


def scan_cron_log_errors(cron_log_dir: Path, states: dict[str, dict],
                         now: datetime) -> dict[str, list[str]]:
    """Scan the most recent cron run log for each task, find hidden errors."""
    hidden_errors: dict[str, list[str]] = {}
    for name in states:
        state = states[name]
        run_at = state.get("run_at", "")
        if state.get("status") != "success":
            continue
        if not run_at:
            continue
        try:
            run_date = datetime.fromisoformat(run_at).strftime("%Y%m%d")
        except (ValueError, IndexError):
            continue
        log_files = list(cron_log_dir.glob(f"{name}-{run_date}.log"))
        if not log_files:
            continue
        log_path = log_files[-1]
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        errors = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("原:") or stripped.startswith("新:"):
                continue
            upper = stripped.upper()
            if "ERRORS=0" in upper:
                continue
            if "ERROR" in upper:
                errors.append(stripped[:120])
            elif "TRACEBACK" in upper:
                errors.append("[traceback found — check full log]")
        if errors:
            hidden_errors[name] = errors[:5]
    return hidden_errors
