from __future__ import annotations

from datetime import datetime, timezone

from ..config import TH
from ..parsers import _load_json
from ..utils import _is_test_query


def analyze_kn_baseline(baseline_dir: Path) -> tuple[list[dict], dict, dict]:
    latest_data = _load_json(baseline_dir / "baseline_latest.json")
    if not latest_data or not isinstance(latest_data, dict):
        return [], {"status": "no_data"}, {}

    dim_score_stats: dict[str, list[float]] = {}
    dim_source_stats: dict[str, dict[str, list[float]]] = {}
    total_queries = 0
    total_filtered = 0
    total_eval_true = 0
    total_eval_false = 0
    total_hs_kept = 0
    total_kt_kept = 0
    total_sag_kept = 0
    total_latency = 0.0
    latency_count = 0

    for query, m in latest_data.items():
        if not isinstance(m, dict):
            continue
        if _is_test_query(query):
            total_filtered += 1
            continue
        total_queries += 1
        dim = m.get("dimension", "unknown")
        score = m.get("avg_score", 0)
        dim_score_stats.setdefault(dim, []).append(score)
        total_eval_true += m.get("eval_counted_true", 0) or 0
        total_eval_false += m.get("eval_counted_false", 0) or 0

        hs_k = m.get("avg_hs_kept", 0) or 0
        kt_k = m.get("avg_kt_kept", 0) or 0
        sag_k = m.get("avg_sag_kept", 0) or 0
        lat = m.get("avg_latency_ms", 0) or 0
        total_hs_kept += hs_k
        total_kt_kept += kt_k
        total_sag_kept += sag_k
        if lat > 0:
            total_latency += lat
            latency_count += 1

        if dim not in dim_source_stats:
            dim_source_stats[dim] = {"hs_kept": [], "kt_kept": [], "sag_kept": [], "latency": []}
        dim_source_stats[dim]["hs_kept"].append(hs_k)
        dim_source_stats[dim]["kt_kept"].append(kt_k)
        dim_source_stats[dim]["sag_kept"].append(sag_k)
        if lat > 0:
            dim_source_stats[dim]["latency"].append(lat)

    dim_summary = {}
    for dim, scores in dim_score_stats.items():
        src = dim_source_stats.get(dim, {})
        def _avg(lst):
            return round(sum(lst) / len(lst), 2) if lst else 0
        dim_summary[dim] = {
            "count": len(scores),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0,
            "avg_hs_kept": _avg(src.get("hs_kept", [])),
            "avg_kt_kept": _avg(src.get("kt_kept", [])),
            "avg_sag_kept": _avg(src.get("sag_kept", [])),
            "avg_latency_ms": _avg(src.get("latency", [])),
        }

    overall_eval_rate = (
        total_eval_true / (total_eval_true + total_eval_false) * 100
        if (total_eval_true + total_eval_false) > 0
        else 0
    )

    overall_source = {
        "avg_hs_kept": round(total_hs_kept / total_queries, 2) if total_queries else 0,
        "avg_kt_kept": round(total_kt_kept / total_queries, 2) if total_queries else 0,
        "avg_sag_kept": round(total_sag_kept / total_queries, 2) if total_queries else 0,
        "avg_latency_ms": round(total_latency / latency_count, 0) if latency_count else 0,
    }

    issues = []
    for dim, s in dim_summary.items():
        if dim == "unknown":
            continue
        if s["count"] < 3:
            continue
        if s["avg_score"] < TH["kn_avg_score_low"]:
            issues.append({
                "severity": "P1",
                "flywheel": "Router",
                "desc": f"KN基线 dimension={dim} 均分 {s['avg_score']:.4f} < {TH['kn_avg_score_low']}",
                "detail": f"{s['count']} 个查询，HS={s['avg_hs_kept']} KT={s['avg_kt_kept']} SAG={s['avg_sag_kept']}",
            })

    unknown_count = dim_summary.get("unknown", {}).get("count", 0)
    if total_queries > 0 and unknown_count / total_queries > TH["unknown_dim_pct"] / 100:
        issues.append({
            "severity": "P1",
            "flywheel": "Router",
            "desc": f"KN基线中 dimension=unknown 占比过高 ({unknown_count}/{total_queries})",
            "detail": f"阈值 {TH['unknown_dim_pct']}%，维度信息未正确标记，影响基线分类统计质量",
        })

    results = {
        "total_queries": total_queries,
        "total_filtered": total_filtered,
        "unknown_dim_pct": round(unknown_count / total_queries * 100, 1) if total_queries else 0,
        "dim_summary": dim_summary,
        "overall_source": overall_source,
        "eval_rate": round(overall_eval_rate, 1),
        "eval_true": total_eval_true,
        "eval_false": total_eval_false,
    }

    # Trend: compare with baseline_prev.json for dimension distribution
    prev_data = _load_json(baseline_dir / "baseline_prev.json")
    trend = {}
    if prev_data and isinstance(prev_data, dict):
        prev_unknown = 0
        prev_total = 0
        for q, m in prev_data.items():
            if not isinstance(m, dict):
                continue
            if _is_test_query(q):
                continue
            prev_total += 1
            if m.get("dimension", "unknown") == "unknown":
                prev_unknown += 1
        if prev_total:
            prev_unknown_pct = prev_unknown / prev_total * 100
            cur_unknown_pct = unknown_count / total_queries * 100 if total_queries else 0
            delta = cur_unknown_pct - prev_unknown_pct
            trend["unknown_dim"] = f"{prev_unknown_pct:.1f}% → {cur_unknown_pct:.1f}% ({delta:+.1f}%)"

    return issues, results, trend


# === 类别三：数据可信度 ===

def analyze_data_credibility(kt_result: dict, router_metrics: dict,
                             kn_result: dict,
                             now: datetime) -> tuple[list[str], list[str]]:
    """Returns (warnings, notes) — warnings affect confidence, notes are informative."""
    warnings = []
    notes = []

    # Sample size
    n_masks = router_metrics.get("real_total", 0)
    if n_masks > 0 and n_masks < TH["min_sample_size"]:
        warnings.append(
            f"Router trace.log 样本量 {n_masks} < {TH['min_sample_size']}，"
            "全关率/空结果率统计结果仅供参考"
        )

    # Baseline freshness
    kt_collected = kt_result.get("collected_at", "")
    if kt_collected:
        try:
            kt_time = datetime.fromisoformat(kt_collected)
            if kt_time.tzinfo is None:
                kt_time = kt_time.replace(tzinfo=timezone.utc)
            age_hours = (now - kt_time).total_seconds() / 3600
            if age_hours > TH["baseline_stale_hours"]:
                notes.append(
                    f"知识树基线数据采集于 {kt_collected[:16]}（已过期 {age_hours:.0f}h，"
                    f"阈值 {TH['baseline_stale_hours']}h）"
                )
        except (ValueError, TypeError):
            pass

    # KN baseline quality — check unknown dimension ratio
    kn_unknown_pct = kn_result.get("unknown_dim_pct", 0)
    if kn_unknown_pct > TH["unknown_dim_pct"]:
        warnings.append(
            f"KN 基线中 dimension=unknown 占比 {kn_unknown_pct:.0f}%"
            f"（阈值 {TH['unknown_dim_pct']}%），基线质量不可靠"
        )

    return warnings, notes
