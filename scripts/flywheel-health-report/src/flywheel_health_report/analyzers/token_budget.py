from __future__ import annotations

from ..config import TH, REC_TH
from ..utils import _percentile


def analyze_token_budget(trace: dict) -> tuple[list[dict], dict, dict]:
    """Analyze token budget usage from trace.log token_budget events."""
    events = trace.get("token_budget", [])
    if not events:
        return [], {"status": "no_data"}, {}

    total_budget = events[0].get("total_budget", 4000)
    n = len(events)

    hs_used_list = []
    kt_used_list = []
    skill_used_list = []
    total_used_list = []
    exhaust_count = 0

    for e in events:
        hs_after = e.get("hs_tokens_after", 0) or 0
        kt_after = e.get("kt_tokens_after", 0) or 0
        skill_after = e.get("skill_tokens_after", 0) or 0
        total_after = hs_after + kt_after + skill_after
        total_used = total_budget - total_after if total_budget else 0
        # 分源消耗用 after 值（实际保留的 token），非 before-after（裁剪量）
        hs_used_list.append(hs_after)
        kt_used_list.append(kt_after)
        skill_used_list.append(skill_after)
        total_used_list.append(total_used)
        # 排除全关场景（total_after=0 时没有召回，不存在预算耗尽）
        if total_after <= 0:
            continue
        if total_budget and total_used / total_budget > REC_TH["token_exhaust_ratio"]:
            exhaust_count += 1

    def _stats(lst):
        if not lst:
            return {"avg": 0, "max": 0, "p50": 0, "p90": 0}
        return {
            "avg": round(sum(lst) / len(lst)),
            "max": max(lst),
            "p50": round(_percentile(lst, 0.50)),
            "p90": round(_percentile(lst, 0.90)),
        }

    hs_stats = _stats(hs_used_list)
    kt_stats = _stats(kt_used_list)
    skill_stats = _stats(skill_used_list)
    total_stats = _stats(total_used_list)
    exhaust_pct = round(exhaust_count / n * 100, 1)

    issues = []
    if exhaust_pct > TH["token_budget_exhaust_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Router",
            "desc": f"Token 预算耗尽率 {exhaust_pct}%（阈值 {TH['token_budget_exhaust_pct']}%）",
            "detail": f"{exhaust_count}/{n} 次调用接近耗尽，可能导致召回截断",
        })

    results = {
        "total_budget": total_budget,
        "event_count": n,
        "exhaust_count": exhaust_count,
        "exhaust_pct": exhaust_pct,
        "hs_stats": hs_stats,
        "kt_stats": kt_stats,
        "skill_stats": skill_stats,
        "total_stats": total_stats,
    }
    trend = {}
    return issues, results, trend
