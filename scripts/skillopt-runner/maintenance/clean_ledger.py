#!/usr/bin/env python3
"""清理 ledger.jsonl 中被测试运行污染的 skillopt_patch 事件。

65 条 skillopt_patch 里有 51 条（78.5%）打在 `test-skill`(34) / `audit-skill`(17)
—— 这两个 skill 在生产上根本不存在，是跑单元测试时写入真实账本的。
后果：`recent_skill_patch_trend()` 据此判定 test-skill「反复打补丁仍不根治」，
触发 F-1 反向门控，把 patch 卡住（单元测试因此失败）。

只移除 event=skillopt_patch 且 skill ∈ {test-skill, audit-skill} 的行，
其余事件（kn_judge / dream_promote / self_evolving / kt_build 及真实 patch）原样保留。

用法： APPLY=1 python3 clean_ledger.py   （默认 dry-run）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/root/.hermes"))
LEDGER = HERMES_HOME / "data" / "flywheel" / "ledger.jsonl"
JUNK_SKILLS = {"test-skill", "audit-skill"}
APPLY = os.environ.get("APPLY", "0") == "1"

if not LEDGER.exists():
    print("ledger 不存在:", LEDGER)
    sys.exit(0)

kept: list[str] = []
removed: list[dict] = []
bad: list[str] = []

with LEDGER.open(encoding="utf-8") as f:
    for raw in f:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad.append(line)
            kept.append(line)      # 解析不了的行一律保留，不猜
            continue
        if (obj.get("event") == "skillopt_patch"
                and obj.get("skill") in JUNK_SKILLS):
            removed.append(obj)
        else:
            kept.append(line)

print(f"ledger: {LEDGER}")
print(f"总行数 {len(kept) + len(removed)} | 待移除 {len(removed)} | "
      f"保留 {len(kept)} | 不可解析(保留) {len(bad)}")
print()
if removed:
    print("待移除样例（前 5 条）:")
    for r in removed[:5]:
        print("   ", json.dumps(r, ensure_ascii=False)[:160])
    print()
    print("移除后 skillopt_patch 剩余分布:")
    from collections import Counter
    c = Counter()
    for line in kept:
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") == "skillopt_patch":
            c[o.get("skill")] += 1
    for k, v in c.most_common():
        print(f"   {v:>4d}  {k}")

if not APPLY:
    print("\n[dry-run] 未写入。设置 APPLY=1 执行清理。")
    sys.exit(0)

ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H-%M-%S")
bak = LEDGER.with_suffix(f".jsonl.pre-ledger-clean-{ts}")
shutil.copy2(LEDGER, bak)
LEDGER.write_text("\n".join(kept) + "\n", encoding="utf-8")
print(f"\n✅ 已写入 {LEDGER}")
print(f"   备份: {bak}")
print(f"   移除 {len(removed)} 条测试污染记录，保留 {len(kept)} 条")
