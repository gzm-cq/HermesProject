from __future__ import annotations

from datetime import datetime, timedelta

from ..config import TH
from ..parsers import _load_json


def analyze_skill_eval(data_flywheel: Path, kn_baseline_dir: Path) -> tuple[list[dict], dict, dict]:
    # NOTE: upstream naming is counterintuitive:
    #   skill_eval_prev.json  = most recent run (latest)
    #   skill_eval_latest.json = previous run (older)
    latest_data = _load_json(data_flywheel / "skill_eval_prev.json")
    if not latest_data:
        return [], {"status": "no_data"}, {}

    meta = latest_data.get("meta", {})
    avg_f1 = meta.get("avg_f1", 0)
    avg_precision = meta.get("avg_precision", 0)
    avg_recall = meta.get("avg_recall", 0)
    n_queries = meta.get("n_queries", 0)
    timestamp = meta.get("timestamp", "")

    issues = []
    if avg_f1 < TH["skill_f1_low"]:
        issues.append({
            "severity": "P0",
            "flywheel": "Skill",
            "desc": f"Skill F1={avg_f1:.4f} (阈值 {TH['skill_f1_low']})",
            "detail": f"基于 {n_queries} 个查询",
        })

    results = {
        "avg_f1": round(avg_f1, 4),
        "avg_precision": round(avg_precision, 4),
        "avg_recall": round(avg_recall, 4),
        "n_queries": n_queries,
        "timestamp": timestamp,
    }

    # Trend: compare with baselines/skill_eval_latest.json (previous run)
    trend = {}
    older_data = _load_json(kn_baseline_dir / "skill_eval_latest.json")
    if older_data and older_data is not latest_data:
        old_meta = older_data.get("meta", {})
        old_f1 = old_meta.get("avg_f1", 0)
        old_ts = old_meta.get("timestamp", "")
        cur_ts = meta.get("timestamp", "")
        # Skip trend if timestamps match (same run, duplicated file)
        if old_ts and cur_ts and old_ts == cur_ts:
            pass
        elif old_f1 and avg_f1:
            delta = avg_f1 - old_f1
            results["f1_delta"] = round(delta, 4)
            trend["Skill F1"] = f"{old_f1:.4f} → {avg_f1:.4f} ({delta:+.4f})"

    return issues, results, trend


def analyze_skill_usage(skill_usage_path: Path, now: datetime) -> tuple[list[dict], dict, dict]:
    """Analyze real skill usage from .usage.json."""
    data = _load_json(skill_usage_path)
    if not data:
        return [], {"status": "no_data"}, {}

    all_skills = list(data.values())
    skill_names = list(data.keys())
    active = [s for s in all_skills if s.get("state") == "active"]
    archived = [s for s in all_skills if s.get("state") == "archived"]
    used = [s for s in active if (s.get("use_count") or 0) > 0]
    never_used = [s for s in active if (s.get("use_count") or 0) == 0]

    total_use = sum(s.get("use_count", 0) or 0 for s in active)
    total_view = sum(s.get("view_count", 0) or 0 for s in active)

    cutoff = now - timedelta(days=TH["skill_unused_warn_days"])
    stale = []
    for s in active:
        lu = s.get("last_used_at")
        if lu:
            try:
                dt = datetime.fromisoformat(lu.replace("Z", "+00:00"))
                if dt < cutoff and (s.get("use_count", 0) or 0) > 0:
                    stale.append(s)
            except (ValueError, TypeError):
                pass

    top_used = sorted(
        [(name, s) for name, s in zip(skill_names, all_skills) if (s.get("use_count", 0) or 0) > 0],
        key=lambda x: x[1].get("use_count", 0) or 0,
        reverse=True,
    )[:10]
    top_list = [{"name": n, "use_count": s.get("use_count", 0) or 0,
                 "view_count": s.get("view_count", 0) or 0,
                 "last_used_at": s.get("last_used_at", "")[:10] if s.get("last_used_at") else ""}
                for n, s in top_used]

    recent_7d = []
    cutoff_7d = now - timedelta(days=7)
    for name, s in zip(skill_names, all_skills):
        lu = s.get("last_used_at")
        if lu and (s.get("use_count", 0) or 0) > 0:
            try:
                dt = datetime.fromisoformat(lu.replace("Z", "+00:00"))
                if dt >= cutoff_7d:
                    recent_7d.append({"name": name, "use_count": s.get("use_count", 0) or 0,
                                       "last_used_at": lu[:10]})
            except (ValueError, TypeError):
                pass
    recent_7d.sort(key=lambda x: x["last_used_at"], reverse=True)

    issues = []

    results = {
        "total_skills": len(all_skills),
        "active_count": len(active),
        "archived_count": len(archived),
        "used_count": len(used),
        "never_used_count": len(never_used),
        "stale_count": len(stale),
        "total_uses": total_use,
        "total_views": total_view,
        "top_used": top_list,
        "recent_7d": recent_7d[:10],
    }

    trend = {}
    return issues, results, trend
