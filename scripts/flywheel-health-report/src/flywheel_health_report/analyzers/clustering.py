from __future__ import annotations

from ..config import TH
from ..parsers import _load_jsonl


def analyze_clustering(data_flywheel: Path) -> tuple[list[dict], dict, dict]:
    runs = _load_jsonl(data_flywheel / "clustering_baseline_prev.json")
    if not runs:
        return [], {"status": "no_data"}, {}

    latest = runs[-1]
    prev = runs[-2] if len(runs) > 1 else None

    noise_pct = latest.get("noise_units", 0)
    total = latest.get("processed_units", 0)
    noise_rate = (noise_pct / total * 100) if total else 0
    cluster_count = latest.get("cluster_count", 0)
    memory_links = latest.get("memory_links", 0)
    total_units = latest.get("total_units", 0)
    timestamp = latest.get("timestamp", "")

    result = {
        "timestamp": timestamp,
        "noise_rate": round(noise_rate, 1),
        "cluster_count": cluster_count,
        "memory_links": memory_links,
        "total_units": total_units,
    }

    issues = []

    # Noise rate check
    if noise_rate > TH["cluster_noise_rate_high"]:
        issues.append({
            "severity": "P1",
            "flywheel": "聚类",
            "desc": f"噪声率 {noise_rate:.1f}% (阈值 {TH['cluster_noise_rate_high']}%)",
            "detail": "大部分数据被归为噪声，聚类效果有限",
        })

    # Noise rate trend — 用前 3 次滚动均值作基线，避免单次离群值误判趋势
    trend = {}
    window = runs[-(TH["trend_window_size"] + 1):-1] if len(runs) > TH["trend_window_size"] else (runs[:-1] if len(runs) > 1 else [])
    if window:
        window_rates = []
        for r in window:
            p_n = r.get("noise_units", 0)
            p_t = r.get("processed_units", 0)
            if p_t > 0:
                window_rates.append(p_n / p_t * 100)
        if window_rates:
            baseline_rate = sum(window_rates) / len(window_rates)
            delta = noise_rate - baseline_rate
            result["noise_rate_delta"] = round(delta, 1)
            result["noise_rate_baseline"] = round(baseline_rate, 1)
            result["noise_rate_baseline_window"] = len(window)
            trend["噪声率"] = f"{baseline_rate:.1f}%（{len(window)}次均值）→ {noise_rate:.1f}% ({delta:+.1f}%)"
            # 离群标注：当前相对窗口均值偏离 >2pp，提示趋势可能是异常波动
            if abs(delta) > TH["noise_outlier_pp"]:
                trend["噪声率_离群"] = (
                    "⚠️ 单次偏离均值 >2pp，趋势可能为异常波动而非真恶化/改善"
                )
    else:
        result["noise_rate_delta"] = 0.0

    return issues, result, trend
