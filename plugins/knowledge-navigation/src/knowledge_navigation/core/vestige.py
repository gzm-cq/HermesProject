"""Vestige 遗忘机制（P0-3，自实现，不依赖 AGPL 项目）。

设计目标（见 docs/融合计划/20260822-数据飞轮增强执行方案.md §2.3）：
Hindsight 24K+ 记忆只增不减，会挤掉高价值注入。Vestige 通过「访问衰减」
对长期未被召回的记忆自动降权（而非删除），降低其注入优先级。

与 scripts/memory-cleanup 的关系：
- memory-cleanup 是 LLM 驱动的主动整理（retain/remove/merge），由 cron 跑；
- Vestige 是被动衰减，在 recall 阶段按访问频次/时间动态降权，二者互补不重复。

实现约束：
- 纯插件侧，不触碰 Hermes 核心 DB schema（符合「不改核心」口径）；
- 访问状态维护在插件本地 JSON（HERMES_HOME 下），零侵入；
- 默认启用，可通过 KN_VESTIGE_ENABLED=0 关闭；衰减系数/半衰期由 ENV 可调。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── 可调参数（ENV 覆盖）──
DEFAULT_STATE_PATH = os.getenv(
    "KN_VESTIGE_STATE",
    os.path.join(os.path.expanduser("~"), ".hermes", "knowledge-navigation", "vestige_state.json"),
)
_DEFAULT_DECAY_BASE = float(os.getenv("KN_VESTIGE_DECAY_BASE", "0.9"))  # weight = base ** days
_DEFAULT_HALFLIFE_DAYS = float(os.getenv("KN_VESTIGE_HALFLIFE_DAYS", "30"))  # 半衰期（仅日志参考）
_DEFAULT_LOW_PRIORITY_THRESHOLD = float(os.getenv("KN_VESTIGE_LOW_THRESHOLD", "0.2"))  # 低于此值标记 low_priority

_lock = threading.Lock()
_state: dict[str, dict[str, Any]] | None = None  # memory_id -> {access_count, last_access}


def _load_state() -> dict[str, dict[str, Any]]:
    global _state
    if _state is not None:
        return _state
    try:
        if os.path.exists(DEFAULT_STATE_PATH):
            with open(DEFAULT_STATE_PATH, "r", encoding="utf-8") as f:
                _state = json.load(f)
        else:
            _state = {}
    except Exception as e:
        logger.warning("Vestige: 状态读取失败，重置: %s", e)
        _state = {}
    return _state


def _save_state() -> None:
    if _state is None:
        return
    try:
        os.makedirs(os.path.dirname(DEFAULT_STATE_PATH), exist_ok=True)
        tmp = DEFAULT_STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False)
        os.replace(tmp, DEFAULT_STATE_PATH)
    except Exception as e:
        logger.warning("Vestige: 状态写入失败（不影响召回）: %s", e)


def record_access(memory_id: str) -> None:
    """记录一次记忆被召回命中（在 _do_hindsight_recall 命中候选时调用）。"""
    if not memory_id:
        return
    with _lock:
        st = _load_state()
        entry = st.get(memory_id)
        now = time.time()
        if entry is None:
            entry = {"access_count": 0, "last_access": 0.0}
            st[memory_id] = entry
        entry["access_count"] = entry.get("access_count", 0) + 1
        entry["last_access"] = now
        _save_state()


def access_weight(memory_id: str, now: float | None = None) -> float:
    """计算记忆的访问衰减权重。

    新记忆/高频访问 → 接近 1.0；长期未访问 → 按 decay_base ** days 衰减。
    """
    if not memory_id:
        return 1.0
    with _lock:
        st = _load_state()
        entry = st.get(memory_id)
    if entry is None:
        # 从未访问过：视为新记忆，给中性权重 1.0（不因未记录而惩罚首次出现）
        return 1.0
    last = entry.get("last_access") or 0.0
    if last <= 0:
        return 1.0
    now = now or time.time()
    days = max(0.0, (now - last) / 86400.0)
    base = _DEFAULT_DECAY_BASE
    return base ** days


def is_low_priority(memory_id: str, now: float | None = None) -> bool:
    """低于衰减阈值的记忆标记为 low_priority（recall 阶段可过滤/降权）。"""
    return access_weight(memory_id, now) < _DEFAULT_LOW_PRIORITY_THRESHOLD


def apply_decay(candidates: list[dict[str, Any]], id_key: str = "id") -> list[dict[str, Any]]:
    """对 Hindsight 候选应用访问衰减：在 rerank_score 上乘衰减权重。

    不删除候选，仅降权，保证「遗忘」是软性的、可恢复的。
    """
    now = time.time()
    enabled = (os.getenv("KN_VESTIGE_ENABLED") or "1").strip() not in ("0", "false", "no", "off")
    if not enabled:
        return candidates
    out = []
    for c in candidates:
        cc = dict(c)
        mid = cc.get(id_key, "")
        w = access_weight(mid, now)
        # 衰减作用于 rerank_score（注入优先级），base_score（原始相关度）保留
        rs = float(cc.get("rerank_score", cc.get("base_score", 0.0)))
        cc["rerank_score"] = rs * w
        cc["_vestige_weight"] = w
        cc["_low_priority"] = w < _DEFAULT_LOW_PRIORITY_THRESHOLD
        out.append(cc)
    return out
