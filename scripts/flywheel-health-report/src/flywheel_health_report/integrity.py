"""integrity.py — 完整性检查。

从 flywheel-health-report.py L1424-1547 搬入。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import (
    REQUIRED_OUTPUTS,
    FLYWHEEL_DEPENDENCIES,
    ACTIVE_CRON_JOBS,
    EXCLUDED_STATE_FILES,
    TH,
    _CRON_TO_FLYWHEEL,
)
from .parsers import _load_json


def check_output_integrity(home: Path) -> list[dict]:
    """Check that critical output files exist and are valid JSON or JSONL."""
    JSONL_FILES = {"clustering_baseline_prev.json"}
    issues = []
    for fw, subpath in REQUIRED_OUTPUTS.items():
        fpath = home / subpath
        if not fpath.exists() or fpath.stat().st_size == 0:
            issues.append({
                "severity": "P1",
                "flywheel": fw,
                "desc": f"产出文件 {fpath.name} 缺失或为空",
                "detail": f"路径: {fpath}",
            })
            continue
        try:
            raw = fpath.read_text(encoding="utf-8").strip()
            if fpath.name in JSONL_FILES:
                lines = [l for l in raw.splitlines() if l.strip()]
                if not lines:
                    issues.append({
                        "severity": "P1",
                        "flywheel": fw,
                        "desc": f"产出文件 {fpath.name} 为空",
                        "detail": f"路径: {fpath}",
                    })
                else:
                    for i, line in enumerate(lines):
                        json.loads(line)
            else:
                data = json.loads(raw)
                if not data:
                    issues.append({
                        "severity": "P1",
                        "flywheel": fw,
                        "desc": f"产出文件 {fpath.name} 解析为空",
                        "detail": f"路径: {fpath}",
                    })
        except json.JSONDecodeError as e:
            issues.append({
                "severity": "P1",
                "flywheel": fw,
                "desc": f"产出文件 {fpath.name} JSON 损坏",
                "detail": str(e)[:200],
            })
    return issues


def check_dependency_chain(states: dict[str, dict]) -> list[dict]:
    """Check that upstream tasks ran successfully before downstream tasks."""
    issues = []
    for downstream, upstreams in FLYWHEEL_DEPENDENCIES.items():
        down_state = states.get(downstream, {})
        down_run = down_state.get("run_at", "")
        down_status = down_state.get("status", "")
        if not down_run or down_status not in ("success", "partial"):
            continue
        try:
            down_time = datetime.fromisoformat(down_run)
        except (ValueError, TypeError):
            continue
        for up_name in upstreams:
            up_state = states.get(up_name, {})
            up_run = up_state.get("run_at", "")
            up_status = up_state.get("status", "")
            if up_status not in ("success", "partial"):
                issues.append({
                    "severity": "P1",
                    "flywheel": _CRON_TO_FLYWHEEL.get(downstream, downstream),
                    "desc": f"依赖链路异常: {downstream} 依赖 {up_name} 未成功",
                    "detail": f"{up_name} status={up_status}",
                })
                continue
            try:
                up_time = datetime.fromisoformat(up_run) if up_run else None
            except (ValueError, TypeError):
                up_time = None
            if up_time and down_time and up_time > down_time:
                issues.append({
                    "severity": "P1",
                    "flywheel": _CRON_TO_FLYWHEEL.get(downstream, downstream),
                    "desc": f"依赖时序异常: {downstream} 运行早于上游 {up_name}",
                    "detail": f"{downstream}: {down_run[:16]}, {up_name}: {up_run[:16]}",
                })
    return issues


def detect_zombie_state_files(cron_state_dir: Path) -> list[str]:
    """Find state files not belonging to active flywheel jobs."""
    zombies = []
    if not cron_state_dir.is_dir():
        return zombies
    for f in sorted(cron_state_dir.glob("*.json")):
        if f.stem in ACTIVE_CRON_JOBS:
            continue
        if f.stem in EXCLUDED_STATE_FILES:
            continue
        zombies.append(f.stem)
    return zombies


def detect_report_type(cron_state_dir: Path, now_utc: datetime) -> str:
    """Detect if this is a scheduled run or boot catch-up."""
    boot_state = _load_json(cron_state_dir / "cron-boot-detect.json")
    if boot_state:
        boot_run = boot_state.get("run_at", "")
        boot_status = boot_state.get("status", "")
        if boot_status == "partial" and boot_run:
            try:
                boot_dt = datetime.fromisoformat(boot_run)
                if boot_dt.tzinfo is None:
                    boot_dt = boot_dt.replace(tzinfo=timezone(timedelta(hours=8)))
                hours_ago = (now_utc - boot_dt.astimezone(timezone.utc)).total_seconds() / 3600
                if 0 <= hours_ago <= TH["boot_catchup_window_hours"]:
                    return "boot-catch-up"
            except (ValueError, TypeError):
                pass
    return "scheduled"
