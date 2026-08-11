from __future__ import annotations

from ..utils import _percentile

# trace.log token_usage 事件的四路分源字段（外加 total_tokens）
_SOURCE_FIELDS = (
    ("hs", "hs_tokens"),
    ("sag", "sag_tokens"),
    ("kt", "kt_tokens"),
    ("skill", "skill_tokens"),
)


def _stats(lst: list[float]) -> dict:
    if not lst:
        return {"avg": 0, "max": 0, "p50": 0, "p90": 0}
    return {
        "avg": round(sum(lst) / len(lst)),
        "max": round(max(lst)),
        "p50": round(_percentile(lst, 0.50)),
        "p90": round(_percentile(lst, 0.90)),
    }


def analyze_token_usage(trace: dict) -> tuple[list[dict], dict, dict]:
    """统计 trace.log token_usage 事件的实际 token 消耗（纯观测，不做预算判定）。

    产品决策：不做 token 预算控制（router 化再多也要花），因此这里只记录实际消耗，
    不再有 total_budget / 耗尽率 / P1 告警。issues 恒为空列表。
    """
    events = trace.get("token_usage", [])
    if not events:
        return [], {"status": "no_data"}, {}

    series: dict[str, list[float]] = {key: [] for key, _ in _SOURCE_FIELDS}
    series["total"] = []

    for e in events:
        per_source = {}
        for key, field in _SOURCE_FIELDS:
            try:
                v = float(e.get(field, 0) or 0)
            except (TypeError, ValueError):
                v = 0.0
            per_source[key] = v
            series[key].append(v)
        # total_tokens 缺失时按四路求和兜底（旧版插件不写该字段）
        raw_total = e.get("total_tokens")
        try:
            total = float(raw_total) if raw_total is not None else sum(per_source.values())
        except (TypeError, ValueError):
            total = sum(per_source.values())
        series["total"].append(total)

    results = {
        "event_count": len(events),
        "total_stats": _stats(series["total"]),
    }
    for key, _ in _SOURCE_FIELDS:
        results[f"{key}_stats"] = _stats(series[key])

    # 各路占比：用「各路总量 / 全部路总量」而非「逐条占比再平均」，
    # 避免小请求（总量几十 token）与大请求（数千 token）等权重导致失真。
    grand_total = sum(sum(series[key]) for key, _ in _SOURCE_FIELDS)
    share = {}
    for key, _ in _SOURCE_FIELDS:
        share[key] = round(sum(series[key]) / grand_total * 100, 1) if grand_total else 0.0
    results["source_share_pct"] = share
    results["grand_total_tokens"] = round(grand_total)

    return [], results, {}
