#!/usr/bin/env python3
"""清理 skillopt state 脏数据 + 初始化 EMA 衰减字段。

问题：
  - skill_neg_feedback 是「只增不减」的累积计数（197 技能 / 37424 次），
    排行榜实际退化为「历史词频排序」，且无 SKILL.md 的僵尸技能
    （负反馈只在优化成功时清零）永久占据榜首名额。

策略：
  1. 僵尸识别：找不到 SKILL.md / 不在 usage / usage 中非 active 或 pinned
     → 从负反馈表、提及表、重试池中彻底移除（不再占名额）
  2. 初始化 skill_neg_ema（衰减后负反馈），初值 = 当前累积值，
     保留历史相对强度；此后每轮按半衰期衰减，新痛点有上升通道。

用法：
  APPLY=1 python3 clean_skillopt_state.py   # 实际写入（默认 dry-run）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/root/.hermes"))
SKILLOPT_HOME = Path(os.environ.get("SKILLOPT_HOME", str(HERMES_HOME / "skillopt-runner")))
STATE_FILE = SKILLOPT_HOME / "state.json"
USAGE_FILE = HERMES_HOME / "skills" / ".usage.json"
SKILLS_DIR = HERMES_HOME / "skills"
APPLY = os.environ.get("APPLY", "0") == "1"


def build_skill_index() -> dict[str, Path]:
    """扫描 skills 目录，构建 name -> SKILL.md 路径映射。

    key 同时支持简单名（knowledge-navigation）与二级名
    （software-development/knowledge-navigation），与 get_skill_path
    的 rglob(f'{name}/SKILL.md') 语义保持一致。
    """
    index: dict[str, Path] = {}
    if not SKILLS_DIR.exists():
        return index
    for md in SKILLS_DIR.rglob("SKILL.md"):
        parts = md.parent.relative_to(SKILLS_DIR).parts
        index["/".join(parts)] = md
        if len(parts) >= 1:
            index.setdefault(parts[-1], md)
        if len(parts) >= 2:
            index.setdefault("/".join(parts[-2:]), md)
    return index


def main() -> int:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    usage: dict = {}
    if USAGE_FILE.exists():
        usage = json.loads(USAGE_FILE.read_text(encoding="utf-8"))

    index = build_skill_index()
    print(f"扫描到 {len(index)} 个 SKILL.md 索引条目")
    print(f"usage 中 {len(usage)} 个技能")

    neg: dict[str, int] = state.get("skill_neg_feedback", {})
    total: dict[str, int] = state.get("skill_total_mentions", {})
    failed: dict = state.get("failed_tasks", {})

    no_file: list[str] = []
    not_in_usage: list[str] = []
    inactive: list[str] = []
    keep: list[str] = []

    for name in sorted(neg.keys()):
        if name not in index:
            no_file.append(name)
            continue
        rec = usage.get(name)
        if rec is None:
            not_in_usage.append(name)
            continue
        if rec.get("pinned", False) or rec.get("state", "active") != "active":
            inactive.append(name)
            continue
        keep.append(name)

    zombies = no_file + not_in_usage + inactive
    zneg = sum(neg.get(n, 0) for n in zombies)

    print("\n" + "=" * 72)
    print("  僵尸技能分类（负反馈永不清零 → 永久占位）")
    print("=" * 72)

    def dump(title: str, names: list[str], limit: int = 12) -> None:
        rows = sorted(names, key=lambda n: -neg.get(n, 0))
        print(f"\n【{title}】{len(rows)} 个，合计负反馈 "
              f"{sum(neg.get(n, 0) for n in rows)} 次")
        for n in rows[:limit]:
            print(f"    {neg.get(n, 0):>6d}  {n}")
        if len(rows) > limit:
            print(f"    ... 其余 {len(rows) - limit} 个")

    dump("A. 找不到 SKILL.md（无法优化，负反馈永不清零）", no_file)
    dump("B. 不在 usage 中（生产已不存在）", not_in_usage)
    dump("C. usage 中 pinned / 非 active", inactive)

    print(f"\n保留 {len(keep)} 个可优化技能，"
          f"负反馈合计 {sum(neg.get(n, 0) for n in keep)} 次")
    print(f"僵尸 {len(zombies)} 个，占累积负反馈 {zneg} 次 "
          f"({zneg / max(1, sum(neg.values())):.1%})")

    print("\n清理后 TOP15 预估（按当前累积值，供人工核对）:")
    for i, n in enumerate(sorted(keep, key=lambda x: -neg.get(x, 0))[:15], 1):
        print(f"  {i:>2d}. {neg.get(n, 0):>6d}  {n}")

    if not APPLY:
        print("\n[dry-run] 未写入。设置 APPLY=1 执行清理。")
        return 0

    # ── 写入 ─────────────────────────────────────────────────────────
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H-%M-%S")
    shutil.copy2(STATE_FILE, STATE_FILE.with_suffix(f".preclean-{ts}.json"))

    new_neg = {n: v for n, v in neg.items() if n in keep}
    new_total = {n: v for n, v in total.items() if n in keep}
    new_failed = {n: v for n, v in failed.items() if n in keep}

    state["skill_neg_feedback"] = new_neg
    state["skill_total_mentions"] = new_total
    state["failed_tasks"] = new_failed
    # EMA 衰减字段：初值 = 清理后的累积值（保留历史相对强度），
    # 此后每轮 ema = ema * 0.5**(days/14) + new_neg
    state["skill_neg_ema"] = dict(new_neg)
    state["last_decay_iso"] = datetime.now(timezone.utc).isoformat()
    state["zombie_removed"] = {
        "cleaned_at": datetime.now(timezone.utc).isoformat(),
        "no_skill_md": no_file,
        "not_in_usage": not_in_usage,
        "inactive": inactive,
        "removed_neg_total": zneg,
    }

    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ 已写入 {STATE_FILE}")
    print(f"   负反馈技能数: {len(neg)} → {len(new_neg)}")
    print(f"   累积负反馈:   {sum(neg.values())} → {sum(new_neg.values())}")
    print(f"   重试池技能数: {len(failed)} → {len(new_failed)}")
    print(f"   已初始化 skill_neg_ema（半衰期 14 天）")
    print(f"   清理前快照: {STATE_FILE.with_suffix(f'.preclean-{ts}.json').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
