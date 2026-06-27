"""review_queue 操作 — 审查队列 CLI 和数据操作"""

from __future__ import annotations

import logging
from typing import Any
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


# ========== ReviewItem 类型 ==========

REVIEW_TYPES = frozenset({
    "consistency_warning",
    "incomplete_split",
    "contradiction",
    "orphan",
    "obsolete",
    "move_suggestion",
})

TIMEOUT_DAYS: dict[str, int] = {
    "consistency_warning": 7,
    "incomplete_split": 7,
    "contradiction": 0,          # 永久 pending
    "orphan": 30,
    "obsolete": 7,
    "move_suggestion": 30,
}

DEFAULT_ACTIONS: dict[str, str] = {
    "consistency_warning": "auto_accept_low_confidence",
    "incomplete_split": "auto_discard",
    "contradiction": "keep_both",
    "orphan": "auto_attach_unknown",
    "obsolete": "auto_delete",
    "move_suggestion": "auto_move",
}


def _check_timeout(review: dict[str, Any]) -> bool:
    """检查是否超时。"""
    timeout_at = review.get("timeout_at")
    if not timeout_at:
        return False
    now = datetime.now(timezone.utc)
    # timeout_at 可能是 datetime 或 ISO 字符串
    if isinstance(timeout_at, str):
        timeout_at = datetime.fromisoformat(timeout_at)
    # 统一时区：如果解析结果是 naive datetime，假定为 UTC
    if timeout_at.tzinfo is None:
        timeout_at = timeout_at.replace(tzinfo=timezone.utc)
    return now >= timeout_at


def list_reviews(
    db_adapter: Any,
    review_type: str | None = None,
    status: str = "pending_review",
) -> list[dict[str, Any]]:
    """列出待审查项。"""
    try:
        return db_adapter.list_review_queue(review_type=review_type, status=status)
    except Exception as e:
        logger.warning("查询审查队列失败: %s", e)
        return []


def accept_review(review_id: int, db_adapter: Any) -> bool:
    """接受审查项，执行对应动作。"""
    try:
        review = db_adapter.get_review_item(review_id)
        if not review:
            logger.warning("审查项不存在: %d", review_id)
            return False

        rtype = review.get("type", "")
        action = DEFAULT_ACTIONS.get(rtype, "noop")

        if action == "auto_delete" and review.get("target_knowledge_id"):
            db_adapter.delete_node(review["target_knowledge_id"])

        elif action == "auto_move" and review.get("target_knowledge_id"):
            # move_suggestion 的移动逻辑由调用方实现
            pass

        db_adapter.update_review_status(review_id, "accepted")
        return True

    except Exception as e:
        logger.warning("接受审查项失败: %s", e)
        return False


def reject_review(review_id: int, db_adapter: Any) -> bool:
    """拒绝审查项。"""
    try:
        db_adapter.update_review_status(review_id, "rejected")
        return True
    except Exception as e:
        logger.warning("拒绝审查项失败: %s", e)
        return False


def process_timeouts(db_adapter: Any) -> int:
    """处理所有超时未处理的审查项。"""
    try:
        pending = db_adapter.list_review_queue(status="pending_review")
    except Exception as e:
        logger.warning("查询待处理审查项失败: %s", e)
        return 0

    processed = 0
    for review in pending:
        if _check_timeout(review):
            rtype = review.get("type", "")
            action = DEFAULT_ACTIONS.get(rtype, "noop")
            if action == "auto_discard" or action == "auto_delete":
                target_id = review.get("target_knowledge_id")
                if target_id:
                    try:
                        db_adapter.delete_node(target_id)
                    except Exception:
                        pass
            db_adapter.update_review_status(review["id"], "timeout")
            processed += 1

    return processed


def insert_review_item(
    review_type: str,
    text: str,
    original_text: str,
    original_claims_count: int,
    reason: str,
    *,
    db_adapter: Any = None,
    target_knowledge_id: int | None = None,
    similarity: float | None = None,
    condition_same: bool | None = None,
) -> dict[str, Any] | None:
    """插入审查队列条目。

    Args:
        review_type: 类型
        text: 相关文本
        original_text: 源文本
        original_claims_count: 源 claims_count
        reason: 触发原因
        db_adapter: PG 适配器（可选）
        target_knowledge_id: 关联知识 ID
        similarity: 余弦相似度（矛盾检测用）
        condition_same: 条件是否相同（矛盾检测用）

    Returns:
        插入的条目 dict（无 db_adapter 时返回 None）
    """
    if review_type not in REVIEW_TYPES:
        logger.warning("未知审查类型: %s", review_type)
        return None

    timeout_days = TIMEOUT_DAYS.get(review_type, 7)
    timeout_at = None
    if timeout_days > 0:
        timeout_at = (datetime.now(timezone.utc) + timedelta(days=timeout_days)).isoformat()

    item = {
        "type": review_type,
        "text": text,
        "original_text": original_text,
        "original_claims_count": original_claims_count,
        "reason": reason,
        "target_knowledge_id": target_knowledge_id,
        "similarity": similarity,
        "condition_same": condition_same,
        "status": "pending_review",
        "timeout_at": timeout_at,
    }

    if db_adapter:
        try:
            db_adapter.insert_review(
                new_text=text,
                existing_node_id=target_knowledge_id or 0,
                existing_text=original_text,
                conflict_type=review_type,
                similarity=similarity or 0.0,
            )
        except Exception as e:
            logger.warning("写入审查队列失败: %s", e)
            return None

    return item
