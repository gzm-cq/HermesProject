from __future__ import annotations

from datetime import datetime, timezone

from ..config import TH
from ..parsers import _load_json, _save_json
from ..utils import _percentile


def analyze_router(trace: dict[str, list[dict]],
                   data_flywheel: Path) -> tuple[list[dict], dict, dict]:
    masks = trace["router_mask"]
    eval_bypasses = trace.get("eval_query_bypass", [])
    successes = trace["recall_success"]
    empties = trace["recall_empty_results"]
    timeouts = trace["recall_timeout"]
    errors = trace["recall_error"]
    kt_fallbacks = trace["hindsight_fail_kt_fallback"]
    sag_recalls = trace["recall_sag"]

    total_masks = len(masks)
    if total_masks == 0:
        return [], {"status": "no_data"}, {}

    # 区分 eval 查询和真实用户消息的 mask
    # router_mask 事件紧跟 eval_query_bypass 则认为是 eval 查询
    eval_mask_flags: set[int] = set()
    if eval_bypasses:
        for eb in eval_bypasses:
            eb_ts = eb.get("timestamp", "")
            for i, m in enumerate(masks):
                m_ts = m.get("timestamp", "")
                if eb_ts <= m_ts:
                    try:
                        delta = (datetime.fromisoformat(m_ts) - datetime.fromisoformat(eb_ts)).total_seconds()
                        if 0 <= delta <= TH["eval_window_sec"]:
                            eval_mask_flags.add(i)
                    except (ValueError, TypeError):
                        continue

    real_masks = [m for i, m in enumerate(masks) if i not in eval_mask_flags]
    eval_masks = [m for i, m in enumerate(masks) if i in eval_mask_flags]
    real_total = len(real_masks)
    eval_total = len(eval_masks)

    full_off = sum(
        1 for m in real_masks
        if not m.get("mask", {}).get("h")
        and not m.get("mask", {}).get("kt")
        and not m.get("mask", {}).get("s")
        and not m.get("mask", {}).get("sag")
    )
    full_on = sum(
        1 for m in real_masks
        if m.get("mask", {}).get("h")
        and m.get("mask", {}).get("kt")
        and m.get("mask", {}).get("s")
        and m.get("mask", {}).get("sag")
    )

    total_recall = len(successes) + len(empties) + len(timeouts) + len(errors)
    success_rate = (len(successes) / total_recall * 100) if total_recall else 0
    empty_rate = (len(empties) / total_recall * 100) if total_recall else 0
    error_rate = (len(errors) / total_recall * 100) if total_recall else 0
    kt_fallback_rate = (len(kt_fallbacks) / total_recall * 100) if total_recall else 0

    latencies = [s.get("latency_ms", 0) for s in successes if s.get("latency_ms")]
    avg_lat = sum(latencies) / len(latencies) if latencies else 0
    max_lat = max(latencies) if latencies else 0
    p50_lat = _percentile(latencies, 0.50)
    p95_lat = _percentile(latencies, 0.95)
    p99_lat = _percentile(latencies, 0.99)

    scores = []
    for s in successes:
        ss = s.get("score_stats", {})
        if ss.get("avg") is not None:
            scores.append(ss["avg"])
    avg_score = sum(scores) / len(scores) if scores else 0

    h_on = sum(1 for m in real_masks if m.get("mask", {}).get("h"))
    kt_on = sum(1 for m in real_masks if m.get("mask", {}).get("kt"))
    s_on = sum(1 for m in real_masks if m.get("mask", {}).get("s"))
    sag_on = sum(1 for m in real_masks if m.get("mask", {}).get("sag"))

    # SAG 专项统计（recall_sag 含 error 场景，需区分）
    sag_recall_attempts = len(sag_recalls)
    sag_error_count = sum(1 for r in sag_recalls if r.get("error"))
    sag_non_empty = sum(1 for r in sag_recalls if r.get("count", 0) > 0 and not r.get("error"))
    sag_total_kept = sum(int(s.get("sag_kept", 0)) for s in successes)
    # 延迟统计包含所有召回尝试（含 error），反映 SAG 服务实际响应时间
    sag_latencies = [r.get("latency_ms", 0) for r in sag_recalls if r.get("latency_ms") is not None]
    sag_avg_lat = sum(sag_latencies) / len(sag_latencies) if sag_latencies else 0
    sag_p50 = _percentile(sag_latencies, 0.50)
    sag_p95 = _percentile(sag_latencies, 0.95)

    issues = []
    full_off_pct = full_off / real_total * 100
    if full_off_pct > TH["router_full_off_pct"]:
        issues.append({
            "severity": "P0",
            "flywheel": "Router",
            "desc": f"Router全关率 {full_off_pct:.1f}% (阈值 {TH['router_full_off_pct']}%)",
            "detail": f"{full_off}/{real_total} 次路由全关，直接跳过召回",
        })
    if empty_rate > TH["recall_empty_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Router",
            "desc": f"空结果率 {empty_rate:.1f}% (阈值 {TH['recall_empty_pct']}%)",
            "detail": f"{len(empties)} 空结果 / {total_recall} 总召回",
        })
    if avg_score and avg_score < TH["kn_avg_score_low"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Router",
            "desc": f"平均得分偏低 {avg_score:.4f} (阈值 {TH['kn_avg_score_low']})",
            "detail": "召回结果相关性不足",
        })
    # 召回错误率（recall_error 事件）异常升高
    if error_rate > TH["error_rate_high_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Router",
            "desc": f"召回错误率 {error_rate:.1f}% (阈值 {TH['error_rate_high_pct']}%)",
            "detail": f"{len(errors)} 次召回异常 / {total_recall} 总召回；可能为 Hindsight/上游 API 异常",
        })
    # Hindsight 失败降级 KT-only 频率过高
    if kt_fallback_rate > TH["recall_empty_pct"]:
        issues.append({
            "severity": "P2",
            "flywheel": "Router",
            "desc": f"Hindsight 失败降级 KT-only 率 {kt_fallback_rate:.1f}%",
            "detail": f"{len(kt_fallbacks)} 次 fallback / {total_recall} 总召回；Hindsight 稳定性待提升",
        })

    metrics = {
        "total_masks": total_masks,
        "real_total": real_total,
        "eval_total": eval_total,
        "full_off": full_off,
        "full_off_pct": round(full_off_pct, 1),
        "full_on": full_on,
        "full_on_pct": round(full_on / real_total * 100, 1),
        "h_on": h_on,
        "kt_on": kt_on,
        "s_on": s_on,
        "sag_on": sag_on,
        "sag_on_pct": round(sag_on / real_total * 100, 1) if real_total else 0,
        "success_count": len(successes),
        "empty_count": len(empties),
        "timeout_count": len(timeouts),
        "error_count": len(errors),
        "kt_fallback_count": len(kt_fallbacks),
        "success_rate": round(success_rate, 1),
        "empty_rate": round(empty_rate, 1),
        "error_rate": round(error_rate, 1),
        "kt_fallback_rate": round(kt_fallback_rate, 1),
        "avg_latency_ms": round(avg_lat),
        "max_latency_ms": max_lat,
        "p50_latency_ms": round(p50_lat),
        "p95_latency_ms": round(p95_lat),
        "p99_latency_ms": round(p99_lat),
        "avg_score": round(avg_score, 4),
        "multi_hop_count": len(trace["multi_hop_expand"]),
        "sag_recall_count": sag_recall_attempts,
        "sag_error_count": sag_error_count,
        "sag_non_empty_count": sag_non_empty,
        "sag_total_kept": sag_total_kept,
        "sag_avg_latency_ms": round(sag_avg_lat),
        "sag_p50_latency_ms": round(sag_p50),
        "sag_p95_latency_ms": round(sag_p95),
    }

    # === Trend: compare with router_prev.json ===
    trend = {}
    prev_router = _load_json(data_flywheel / "router_prev.json")
    if prev_router and isinstance(prev_router, dict):
        prev_full_off = prev_router.get("full_off_pct")
        prev_empty = prev_router.get("empty_pct")
        prev_latency = prev_router.get("avg_latency_ms")
        prev_sag_on = prev_router.get("sag_on_pct")
        prev_sag_kept = prev_router.get("sag_total_kept")
        if prev_full_off is not None:
            delta = full_off_pct - prev_full_off
            trend["Router 全关率"] = f"{prev_full_off:.1f}% → {full_off_pct:.1f}% ({delta:+.1f}%)"
        if prev_empty is not None:
            delta = empty_rate - prev_empty
            trend["Router 空结果率"] = f"{prev_empty:.1f}% → {empty_rate:.1f}% ({delta:+.1f}%)"
        if prev_latency is not None:
            delta = avg_lat - prev_latency
            trend["Router 平均延迟"] = f"{prev_latency:.0f}ms → {avg_lat:.0f}ms ({delta:+.0f}ms)"
        if prev_sag_on is not None:
            delta = metrics["sag_on_pct"] - prev_sag_on
            trend["SAG 开启率"] = f"{prev_sag_on:.1f}% → {metrics['sag_on_pct']:.1f}% ({delta:+.1f}%)"
        if prev_sag_kept is not None and prev_sag_kept > 0:
            delta = sag_total_kept - prev_sag_kept
            trend["SAG 召回量"] = f"{prev_sag_kept} → {sag_total_kept} ({delta:+d})"

    # Save current snapshot for next comparison
    _save_json(data_flywheel / "router_prev.json", {
        "full_off_pct": round(full_off_pct, 1),
        "empty_pct": round(empty_rate, 1),
        "avg_latency_ms": round(avg_lat),
        "sag_on_pct": metrics["sag_on_pct"],
        "sag_total_kept": sag_total_kept,
        "sag_avg_latency_ms": round(sag_avg_lat),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return issues, metrics, trend
