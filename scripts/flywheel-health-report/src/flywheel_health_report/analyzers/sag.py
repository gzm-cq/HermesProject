from __future__ import annotations

from ..config import TH


def analyze_sag_contribution(trace: dict) -> tuple[list[dict], dict, dict]:
    """Analyze SAG recall contribution from recall_sag + sag_merge events.

    使用 RecallLogger 框架产生的 recall_sag 事件（字段：source/count/latency_ms/score_stats/error），
    而非 hooks.py _do_sag_recall 内部直接记录的 sag_recall 事件（后者为冗余日志）。

    注意：recall_sag 事件在 SAG 异常时也会记录（count=0, error=...），需与成功召回区分：
    - recall_error_count: SAG 召回异常次数（单独统计，不计入 recall_zero）
    - recall_zero: 成功召回但返回 0 section 的次数
    """
    recall_events = trace.get("recall_sag", [])
    merge_events = trace.get("sag_merge", [])

    if not recall_events and not merge_events:
        return [], {"status": "no_data"}, {}

    # 区分成功召回与异常召回：error 场景 count=0 但不应计入 recall_zero
    recall_success_events = [e for e in recall_events if not e.get("error")]
    recall_error_count = len(recall_events) - len(recall_success_events)
    recall_counts = [e.get("count", 0) or 0 for e in recall_success_events]
    merge_counts = [e.get("count", 0) or 0 for e in merge_events]

    recall_zero = sum(1 for c in recall_counts if c == 0)
    merge_zero = sum(1 for c in merge_counts if c == 0)

    def _stats(lst):
        if not lst:
            return {"avg": 0, "max": 0, "total": 0, "non_zero": 0}
        return {
            "avg": round(sum(lst) / len(lst), 1),
            "max": max(lst),
            "total": sum(lst),
            "non_zero": sum(1 for c in lst if c > 0),
        }

    recall_stats = _stats(recall_counts)
    merge_stats = _stats(merge_counts)
    merge_zero_pct = round(merge_zero / len(merge_counts) * 100, 1) if merge_counts else 0

    issues = []
    # recall_zero 判断只基于成功召回事件（排除 error 场景）
    if recall_success_events and recall_zero == len(recall_success_events):
        issues.append({
            "severity": "P1",
            "flywheel": "SAG",
            "desc": f"SAG 全部召回为 0 section（{len(recall_success_events)} 次成功召回）",
            "detail": "可能 SAG 索引为空或搜索条件过严",
        })
    elif merge_events and merge_zero_pct > TH["sag_merge_zero_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "SAG",
            "desc": f"SAG 合并零结果率 {merge_zero_pct}%（阈值 {TH['sag_merge_zero_pct']}%）",
            "detail": f"{merge_zero}/{len(merge_counts)} 次合并为 0，SAG 召回质量可能偏低",
        })
    if recall_error_count > 0:
        issues.append({
            "severity": "P1",
            "flywheel": "SAG",
            "desc": f"SAG 召回异常 {recall_error_count} 次",
            "detail": "SAG 服务可能不稳定，已触发熔断器或网络异常",
        })

    results = {
        "recall_count": len(recall_events),
        "recall_success_count": len(recall_success_events),
        "recall_error_count": recall_error_count,
        "merge_count": len(merge_events),
        "recall_zero": recall_zero,
        "merge_zero": merge_zero,
        "merge_zero_pct": merge_zero_pct,
        "recall_stats": recall_stats,
        "merge_stats": merge_stats,
    }
    trend = {}
    return issues, results, trend
