"""confidence 衰减计算 — 纠错回路核心。

根据检索日志调整知识点 confidence，驱动纠错回路。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ========== 初始参数（以下均为经验值，上线后调参校准） ==========

# 被点击 → 升权
CLICK_BOOST: float = 0.05
# 最大 confidence
MAX_CONFIDENCE: float = 1.0
# 召回未点击 → 日衰减 1%
RECALL_NO_CLICK_DECAY: float = 0.99
# 未召回 → 日衰减 0.3%
NO_RECALL_DECAY: float = 0.997
# 用户否定 → 砍半
NEGATION_PENALTY: float = 0.5

# 阈值
THRESHOLD_REVIEW: float = 0.3
THRESHOLD_DEMOTE: float = 0.5
THRESHOLD_PROMOTE: float = 0.95
THRESHOLD_REMOVE: float = 0.1

# 累积衰减：连续 N 次未被点击 → 额外衰减天数
CONSECUTIVE_MISS_PENALTY_DAYS: float = 7.0


def update_confidence(
    current_confidence: float,
    *,
    was_recalled: bool = False,
    was_clicked: bool = False,
    user_negated: bool = False,
    days_since_last_event: float = 0.0,
    consecutive_misses: int = 0,
) -> tuple[float, str]:
    """根据检索事件更新 confidence。

    Args:
        current_confidence: 当前 confidence 值
        was_recalled: 本检索中是否被召回
        was_clicked: 本检索中是否被用户点击
        user_negated: 用户是否明确否定
        days_since_last_event: 距上次事件的天数
        consecutive_misses: 连续未被点击的次数

    Returns:
        (new_confidence, action)
        action: "promote" | "demote" | "review" | "remove" | "normal"
    """
    conf = current_confidence

    if user_negated:
        conf *= NEGATION_PENALTY
    elif was_clicked:
        conf = min(MAX_CONFIDENCE, conf + CLICK_BOOST)
    elif was_recalled:
        conf *= (RECALL_NO_CLICK_DECAY ** days_since_last_event)
    else:
        conf *= (NO_RECALL_DECAY ** days_since_last_event)

    # 累积衰减
    if consecutive_misses >= 3 and not was_clicked:
        conf *= (RECALL_NO_CLICK_DECAY ** CONSECUTIVE_MISS_PENALTY_DAYS)

    # 阈值判定
    if conf < THRESHOLD_REMOVE:
        return max(conf, 0.0), "remove"
    elif conf < THRESHOLD_REVIEW:
        return conf, "review"
    elif conf < THRESHOLD_DEMOTE:
        return conf, "demote"
    elif conf > THRESHOLD_PROMOTE:
        return min(conf, 1.0), "promote"

    return conf, "normal"


def batch_update_from_logs(
    use_logs: list[dict[str, Any]],
    current_confidences: dict[str, float],
) -> dict[str, tuple[float, str]]:
    """从使用日志批量更新多个知识的 confidence。

    Args:
        use_logs: 使用日志列表，每条含 {knowledge_id, recalled, clicked, user_feedback, timestamp}
        current_confidences: {knowledge_id: current_confidence}

    Returns:
        {knowledge_id: (new_confidence, action)}
    """
    from collections import defaultdict

    # 按 knowledge_id 分组
    logs_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for log in use_logs:
        kid = str(log.get("knowledge_id", ""))
        if kid:
            logs_by_id[kid].append(log)

    results: dict[str, tuple[float, str]] = {}
    for kid, logs in logs_by_id.items():
        conf = current_confidences.get(kid, 1.0)
        consecutive_misses = 0

        for log in logs:
            recalled = bool(log.get("recalled", False))
            clicked = bool(log.get("clicked", False))
            negated = bool(log.get("user_feedback") in ("不是这个", "没找到"))

            if recalled and not clicked:
                consecutive_misses += 1
            else:
                consecutive_misses = 0

            conf, action = update_confidence(
                conf,
                was_recalled=recalled,
                was_clicked=clicked,
                user_negated=negated,
                days_since_last_event=1.0,
                consecutive_misses=consecutive_misses,
            )

        results[kid] = (conf, action)

    return results
