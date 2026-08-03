"""utils.py — 跨分析器共享工具函数。

从 flywheel-health-report.py 搬入：
- _percentile (L418)
- _resolve_trend_arrow (L1593)
- _is_test_query (L185)
"""

from __future__ import annotations

from .config import _TEST_QUERY_RE


def _percentile(values: list[float], p: float) -> float:
    """计算百分位数（线性插值）。"""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _resolve_trend_arrow(delta_val: float) -> str:
    if delta_val > 0.01:
        return "↑ 改善"
    elif delta_val < -0.01:
        return "↓ 恶化"
    return "→ 持平"


def _is_test_query(key: str) -> bool:
    return bool(_TEST_QUERY_RE.match(key))
