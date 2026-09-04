"""记忆生命周期管理 — 冷记忆淘汰 + 高频回升。

Feature Flag 控制，默认关闭。访问数据暂时用启发式估算，后续接上真实 use_log。
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from memory_cleanup.config import CONFIG

logger = logging.getLogger(__name__)

_DEFAULT_CREATED_DAYS_RECENT = 15
_DEFAULT_CREATED_DAYS_NORMAL = 30


def _parse_datetime(text: str) -> datetime | None:
    """从文本中尝试解析日期时间。

    支持格式：
    - YYYY-MM-DD HH:MM:SS
    - YYYY-MM-DD
    - YYYY/MM/DD
    - MM月DD日（需结合年份推断，默认当年）
    """
    patterns = [
        (r"\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}:\d{2}", "%Y-%m-%d %H:%M:%S"),
        (r"\d{4}-\d{1,2}-\d{1,2}", "%Y-%m-%d"),
        (r"\d{4}/\d{1,2}/\d{1,2}", "%Y/%m/%d"),
    ]
    for pattern, fmt in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return datetime.strptime(match.group(), fmt)
            except ValueError:
                continue
    return None


def _estimate_last_access(entry: dict[str, Any], now: datetime) -> datetime:
    """估算条目最后访问时间。

    优先级：
    1. entry["last_accessed"]（如果有且是 datetime 或可解析字符串）
    2. entry["created_at"] * 0.7 + now * 0.3（保守估计）
    3. 从条目内容中提取日期作为创建时间，再按 0.7/0.3 估算
    4. 兜底：now - 15天（中等热度假设）

    注意：如果内容中的日期是"历史记录"（如"2020年的项目"），
    会被误判为创建时间。但因为使用了 0.7/0.3 权重，
    最终估算会偏向 now，减少误判影响。
    """
    last_accessed = entry.get("last_accessed")
    if last_accessed:
        if isinstance(last_accessed, datetime):
            return last_accessed
        if isinstance(last_accessed, str):
            parsed = _parse_datetime(last_accessed)
            if parsed:
                return parsed

    content = entry.get("content", "")
    content_lower = content.lower() if isinstance(content, str) else ""

    has_recent_signal = any(kw.lower() in content_lower for kw in CONFIG.lifecycle_recent_keywords)
    has_historical_signal = any(kw in content for kw in CONFIG.lifecycle_historical_keywords)

    created_at = entry.get("created_at")
    if created_at:
        if isinstance(created_at, datetime):
            created_dt = created_at
        elif isinstance(created_at, str):
            parsed = _parse_datetime(created_at)
            if parsed:
                created_dt = parsed
            else:
                created_dt = now - timedelta(days=_DEFAULT_CREATED_DAYS_RECENT if has_recent_signal else _DEFAULT_CREATED_DAYS_NORMAL)
        else:
            created_dt = now - timedelta(days=_DEFAULT_CREATED_DAYS_RECENT if has_recent_signal else _DEFAULT_CREATED_DAYS_NORMAL)
    else:
        if isinstance(content, str):
            parsed = _parse_datetime(content)
            if parsed:
                if has_historical_signal and not has_recent_signal:
                    created_dt = now - timedelta(days=_DEFAULT_CREATED_DAYS_NORMAL)
                else:
                    created_dt = parsed
            else:
                created_dt = now - timedelta(days=_DEFAULT_CREATED_DAYS_RECENT if has_recent_signal else _DEFAULT_CREATED_DAYS_NORMAL)
        else:
            created_dt = now - timedelta(days=_DEFAULT_CREATED_DAYS_NORMAL)

    delta = now - created_dt
    weight = 0.3 if has_recent_signal else 0.2
    estimated = created_dt + delta * weight
    return min(estimated, now)


def _estimate_access_count(entry: dict[str, Any]) -> int:
    """估算条目访问次数。

    优先级：
    1. entry["access_count"]（如果有）
    2. 启发式：根据内容中的频率关键词估算
    3. 兜底：3 次
    """
    access_count = entry.get("access_count")
    if isinstance(access_count, int) and access_count >= 0:
        return access_count

    content = entry.get("content", "")
    if not isinstance(content, str):
        return 3

    freq_score = 0
    content_lower = content.lower()
    for kw in CONFIG.lifecycle_frequency_keywords:
        if kw.lower() in content_lower:
            freq_score += 2

    if freq_score > 0:
        return min(15, 5 + freq_score)

    return 3


def _is_protected(entry: dict[str, Any]) -> bool:
    """判断条目是否含受保护信号（用户偏好/行为规则），受保护条目永不淘汰。

    2026-09-04 修复：启发式日期估算会把内容中的日期当创建时间，导致含
    旧日期的用户偏好（如 "2026-06-10 session confirmed"）被误判为冷记忆。
    """
    content = entry.get("content", "")
    if not isinstance(content, str):
        return False
    content_lower = content.lower()
    for kw in CONFIG.lifecycle_protected_keywords:
        if kw in content or kw.lower() in content_lower:
            return True
    return False


def compute_capacity_ratio(entries: list[str], char_limit: int) -> float:
    """计算记忆容量占用比例（字符数 / 上限）。

    Args:
        entries: 记忆条目列表
        char_limit: 字符上限（memory_char_limit / user_char_limit）

    Returns:
        占用比例（0.0 ~ 1.0+），char_limit <= 0 时返回 0.0
    """
    if char_limit <= 0:
        return 0.0
    total = sum(len(e) for e in entries)
    return total / char_limit


def detect_cold_memories(
    entries: list[dict[str, Any]],
    cold_days: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """检测冷记忆（长期未访问的条目）。

    Args:
        entries: 条目列表，每个条目是 dict，至少含 "content" 字段
        cold_days: 多少天未访问视为冷记忆
        now: 当前时间（测试用），None 时用 datetime.now()

    Returns:
        冷记忆条目列表（浅拷贝，新增 "estimated_last_access" 和 "days_since_access" 字段）
    """
    if not entries:
        return []

    if now is None:
        now = datetime.now()

    cold_threshold = now - timedelta(days=cold_days)
    cold_entries: list[dict[str, Any]] = []

    for entry in entries:
        if _is_protected(entry):
            logger.info("冷记忆检测: 条目含受保护信号，跳过淘汰")
            continue
        last_access = _estimate_last_access(entry, now)
        days_since = (now - last_access).days

        if last_access <= cold_threshold:
            cold_entry = dict(entry)
            cold_entry["estimated_last_access"] = last_access
            cold_entry["days_since_access"] = days_since
            cold_entries.append(cold_entry)

    logger.info(
        "冷记忆检测: %d/%d 条为冷记忆（阈值 %d 天）",
        len(cold_entries), len(entries), cold_days,
    )
    return cold_entries


def detect_hot_memories(
    hindsight_entries: list[dict[str, Any]],
    access_count_threshold: int,
) -> list[dict[str, Any]]:
    """检测高频记忆（Hindsight 中访问次数达到阈值的条目）。

    Args:
        hindsight_entries: Hindsight 条目列表
        access_count_threshold: 访问次数阈值

    Returns:
        高频记忆条目列表（浅拷贝，新增 "estimated_access_count" 字段）
    """
    if not hindsight_entries:
        return []

    hot_entries: list[dict[str, Any]] = []

    for entry in hindsight_entries:
        access_count = _estimate_access_count(entry)

        if access_count >= access_count_threshold:
            hot_entry = dict(entry)
            hot_entry["estimated_access_count"] = access_count
            hot_entries.append(hot_entry)

    logger.info(
        "高频记忆检测: %d/%d 条为高频（阈值 %d 次）",
        len(hot_entries), len(hindsight_entries), access_count_threshold,
    )
    return hot_entries
