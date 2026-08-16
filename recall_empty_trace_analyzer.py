"""KT recall_empty 归因分析器（闭环假设 C：查询 mix 漂移）。

用法（WSL 内）:
  wsl python3 /mnt/d/HermesProject/recall_empty_trace_analyzer.py

行为:
  1. 解析 knowledge-navigation/trace.log 中所有 recall_empty_results / recall_empty 事件
  2. 以网关重启时刻（KT_RESTART_ISO，默认 2026-08-16T02:20:00+00:00，即本地 10:20）为界，
     区分「重启前（旧代码，无 source/intent/query 字段）」与「重启后（新代码，含字段）」
  3. 对新代码事件按 source（活跃路由）、intent（eval/general）、(source,intent) 聚合
  4. 抽样展示 query，定位空结果归属的路由/意图
  5. 输出明确结论：是否存在带字段的重启后事件 → 能否闭环 C

字段说明（hooks/router.py 注入）:
  source : 活跃路由 "+" 串（hindsight/kt/skill/sag），全关为 "none"
  intent : "eval"（命中评估查询）或 "general"
  query  : 原始用户消息（截断前）
"""
from __future__ import annotations

import json
import os
import re
import collections
from datetime import datetime, timezone

TRACE = os.environ.get(
    "TRACE_LOG",
    "/root/.hermes/plugins/knowledge-navigation/trace.log",
)
# 网关重启（新代码生效）时刻；事件晚于此即视为「重启后、带新字段」
RESTART_ISO = os.environ.get("KT_RESTART_ISO", "2026-08-16T02:20:00+00:00")
EMPTY_EVENTS = ("recall_empty_results", "recall_empty")


def _parse(path: str):
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                m = re.search(r"\{.*\}", line)
                if not m:
                    continue
                try:
                    rec = json.loads(m.group(0))
                except Exception:
                    continue
            if isinstance(rec, dict) and rec.get("event") in EMPTY_EVENTS:
                out.append(rec)
    return out


def _iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def main() -> None:
    restart = _iso(RESTART_ISO)
    events = _parse(TRACE)
    print(f"trace: {TRACE}")
    print(f"restart cutoff (new-code): {RESTART_ISO}")
    print(f"total empty events: {len(events)}")

    before, after = [], []
    for e in events:
        t = _iso(e.get("timestamp", ""))
        if t is None or restart is None or t >= restart:
            after.append(e)
        else:
            before.append(e)

    print(f"  重启前 (旧代码, 无字段): {len(before)}")
    print(f"  重启后 (新代码, 带字段): {len(after)}")

    # 字段存在性（判断新代码是否真的在产出现场数据）
    has = {"source": 0, "intent": 0, "query": 0}
    for e in after:
        for k in has:
            if k in e:
                has[k] += 1
    print(f"\n重启后事件字段覆盖率: {has}")

    if not after:
        print("\n⚠️ 重启后暂无带字段的 recall_empty 事件 —— 网关自重启后无触发空结果的流量。")
        print("   假设 C 暂无法用真实数据闭环；下次出现空结果（或发一条无召回的查询）后重跑本脚本即可。")
        return

    src = collections.Counter()
    intent = collections.Counter()
    combo = collections.Counter()
    samples = []
    for e in after:
        s = e.get("source", "<MISSING>")
        i = e.get("intent", "<MISSING>")
        q = e.get("query", "<MISSING>")
        src[s] += 1
        intent[i] += 1
        combo[(s, i)] += 1
        if len(samples) < 15:
            samples.append((e.get("event"), s, i, (q[:60] if isinstance(q, str) else q)))

    print("\n-- 按 source（活跃路由）--")
    for k, v in src.most_common():
        print(f"  {k:20s} {v}")
    print("\n-- 按 intent（eval/general）--")
    for k, v in intent.most_common():
        print(f"  {k:20s} {v}")
    print("\n-- 按 (source, intent) --")
    for k, v in combo.most_common():
        print(f"  {k}  {v}")
    print("\n-- query 抽样 (event, source, intent, query) --")
    for s in samples:
        print("  ", s)

    # 简易查询 mix 漂移判定
    gen = intent.get("general", 0)
    ev = intent.get("eval", 0)
    tot = gen + ev
    if tot:
        print(f"\n查询 mix: general={gen} ({gen/tot*100:.0f}%) eval={ev} ({ev/tot*100:.0f}%)")
        print("→ 若 eval 占比异常高且伴随空结果，支持「查询 mix 漂移」假设；否则空结果由知识结构侧主导。")


if __name__ == "__main__":
    main()
