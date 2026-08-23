#!/usr/bin/env python3
"""Vestige 遗忘机制运维脚本（P0-3 配套）。

读取 Vestige 访问衰减状态（knowledge-navigation 插件维护的 vestige_state.json），
报告长期未访问、已被降权的记忆（low_priority），供人工审计。

注意：Vestige 的「遗忘」是软性的——在 recall 阶段按 access_weight 降权，
不删除记忆本身。本脚本仅做**报告**与**状态重置**，不修改 Hindsight 数据。

用法：
    python weed.py                 # 报告当前衰减状态
    python weed.py --reset <id>   # 重置某记忆的访问计数（重新激活）
    python weed.py --stats        # 汇总统计
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

DEFAULT_STATE_PATH = os.getenv(
    "KN_VESTIGE_STATE",
    os.path.join(os.path.expanduser("~"), ".hermes", "knowledge-navigation", "vestige_state.json"),
)
DECAY_BASE = float(os.getenv("KN_VESTIGE_DECAY_BASE", "0.9"))
LOW_THRESHOLD = float(os.getenv("KN_VESTIGE_LOW_THRESHOLD", "0.2"))


def _load() -> dict:
    if not os.path.exists(DEFAULT_STATE_PATH):
        return {}
    with open(DEFAULT_STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _weight(entry: dict, now: float) -> float:
    last = entry.get("last_access") or 0.0
    if last <= 0:
        return 1.0
    days = max(0.0, (now - last) / 86400.0)
    return DECAY_BASE ** days


def report() -> int:
    st = _load()
    now = time.time()
    if not st:
        print("[vestige] 无状态文件，尚未记录任何记忆访问。")
        return 0
    low = []
    for mid, entry in st.items():
        w = _weight(entry, now)
        if w < LOW_THRESHOLD:
            low.append((mid, w, entry.get("access_count", 0), entry.get("last_access", 0)))
    low.sort(key=lambda x: x[1])
    print(f"[vestige] 总记录: {len(st)} | low_priority(权重<{LOW_THRESHOLD}): {len(low)}")
    for mid, w, cnt, la in low[:20]:
        days = int((now - la) / 86400.0) if la else -1
        print(f"  {w:.3f}  access={cnt}  idle={days}d  {mid}")
    return 0


def stats() -> int:
    st = _load()
    now = time.time()
    if not st:
        print("[vestige] 无状态。")
        return 0
    weights = [_weight(e, now) for e in st.values()]
    avg = sum(weights) / len(weights)
    mn = min(weights)
    mx = max(weights)
    print(f"[vestige] 记录数={len(st)} 权重 avg={avg:.3f} min={mn:.3f} max={mx:.3f}")
    print(f"[vestige] low_priority 占比={sum(1 for w in weights if w < LOW_THRESHOLD) / len(weights):.1%}")
    return 0


def reset(mid: str) -> int:
    st = _load()
    if mid not in st:
        print(f"[vestige] 未找到记录: {mid}")
        return 1
    st[mid] = {"access_count": 0, "last_access": 0.0}
    os.makedirs(os.path.dirname(DEFAULT_STATE_PATH), exist_ok=True)
    with open(DEFAULT_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    print(f"[vestige] 已重置 {mid}（重新激活）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Vestige 遗忘机制运维脚本")
    ap.add_argument("--reset", metavar="ID", help="重置某记忆的访问状态（重新激活）")
    ap.add_argument("--stats", action="store_true", help="汇总统计")
    args = ap.parse_args()
    if args.reset:
        return reset(args.reset)
    if args.stats:
        return stats()
    return report()


if __name__ == "__main__":
    sys.exit(main())
