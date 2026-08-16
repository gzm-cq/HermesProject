from __future__ import annotations

from ..config import TH
from ..parsers import _load_json


def analyze_kt_baseline(data_flywheel: Path) -> tuple[list[dict], dict, dict]:
    latest_data = _load_json(data_flywheel / "kt-baseline-latest.json")
    if not latest_data:
        return [], {"status": "no_data"}, {}

    metrics = latest_data.get("metrics", {})
    total_kps = int(metrics.get("total_kps", 0))
    orphan_kps = int(metrics.get("orphan_kps", 0))
    avg_conf = metrics.get("avg_confidence", 0)
    fragment_domains = int(metrics.get("fragment_domains", 0))
    total_subjects = int(metrics.get("total_subjects", 0))
    low_conf_kp_rate = float(metrics.get("low_conf_kp_rate", 0) or 0)
    pending_conflict_rate = float(metrics.get("pending_conflict_rate", 0) or 0)
    collected_at = latest_data.get("collected_at", "")

    orphan_pct = (orphan_kps / total_kps * 100) if total_kps else 0
    # 过度拆解率：碎片域占全部 domain 比例（域划得过细 → 碎片域多 → 该率↑）
    over_split_rate = (fragment_domains / total_subjects) if total_subjects else 0.0

    issues = []
    if orphan_pct > TH["kt_orphan_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "知识树",
            "desc": f"孤立知识点 {orphan_pct:.1f}% (阈值 {TH['kt_orphan_pct']}%)",
            "detail": f"孤立 {orphan_kps}/{total_kps}",
        })

    results = {
        "total_kps": total_kps,
        "orphan_kps": orphan_kps,
        "orphan_pct": round(orphan_pct, 1),
        "avg_confidence": round(avg_conf, 4),
        "fragment_domains": fragment_domains,
        "total_subjects": total_subjects,
        # 闭环反馈键（供 auto-tuner 消费，写入 daily summary）
        "kt_candidate_noise_rate": round(low_conf_kp_rate, 4),
        "kt_over_split_rate": round(over_split_rate, 4),
        "kt_low_conf_kp_rate": round(low_conf_kp_rate, 4),
        "kt_pending_conflict_rate": round(pending_conflict_rate, 4),
        "collected_at": collected_at,
    }

    # Trend: compare with kt-baseline-prev.json
    prev_data = _load_json(data_flywheel / "kt-baseline-prev.json")
    trend = {}
    if prev_data:
        prev_metrics = prev_data.get("metrics", {})
        prev_orphan = int(prev_metrics.get("orphan_kps", 0))
        prev_total = int(prev_metrics.get("total_kps", 0))
        prev_orphan_pct = (prev_orphan / prev_total * 100) if prev_total else 0
        if prev_orphan_pct:
            delta = orphan_pct - prev_orphan_pct
            trend["孤立率"] = f"{prev_orphan_pct:.1f}% → {orphan_pct:.1f}% ({delta:+.1f}%)"

    return issues, results, trend
