#!/usr/bin/env python3
"""WeaknessMiner — 失败模式挖掘（P1-1，自实现，不拷贝上游）。

依据 docs/融合计划/20260822-数据飞轮增强执行方案.md §3.1：
从 skillopt-sleep 的 staging 报告（skill 优化失败轨迹）聚类失败 pattern，
接入 skillopt-sleep/mine.py 的 mine()（提供 llm_miner 回调）。

数据源：
    /root/.hermes/skillopt-runner/.skillopt-sleep/staging/*/report.json
    每条 report 含 gate_action / rejected_edits[]（target/op/reason）等字段，
    是真实的「skill 优化被拒」失败轨迹，比 knowledge-navigation/trace.log
    （仅 adapter 超时日志，无轨迹模式）更适合聚类。

设计：
- extract_patterns()：从 staging reports 聚类 rejected_edits 的 (target, op) 模式；
- llm_miner_callback()：可选的 LLM 增强挖掘（默认离线启发式，零外部依赖）；
- run()：组装 SessionDigest 喂给 skillopt_sleep.mine()，产出 TaskRecord 列表。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any

DEFAULT_STAGING_DIR = "/root/.hermes/skillopt-runner/.skillopt-sleep/staging"


def _load_reports(staging_dir: str) -> list[dict[str, Any]]:
    reports = []
    if not os.path.isdir(staging_dir):
        return reports
    for name in sorted(os.listdir(staging_dir)):
        rp = os.path.join(staging_dir, name, "report.json")
        if not os.path.isfile(rp):
            continue
        try:
            with open(rp, "r", encoding="utf-8") as f:
                reports.append(json.load(f))
        except Exception:
            continue
    return reports


def extract_patterns(staging_dir: str = DEFAULT_STAGING_DIR) -> list[dict[str, Any]]:
    """从 staging reports 聚类失败模式。

    聚类键：(rejected_edit.target, rejected_edit.op)，统计频次与典型 reason。
    返回按频次降序的 pattern 列表。
    """
    reports = _load_reports(staging_dir)
    counter: Counter = Counter()
    reasons: dict[tuple[str, str], list[str]] = {}
    for r in reports:
        for rej in r.get("rejected_edits", []) or []:
            key = (str(rej.get("target", "?")), str(rej.get("op", "?")))
            counter[key] += 1
            reasons.setdefault(key, []).append(str(rej.get("reason", ""))[:120])
    patterns = []
    for (target, op), cnt in counter.most_common():
        patterns.append({
            "target": target,
            "op": op,
            "count": cnt,
            "sample_reasons": reasons[(target, op)][:3],
        })
    return patterns


def llm_miner_callback(digests: list[Any]) -> list[Any]:
    """llm_miner 回调（离线启发式版）。

    不依赖外部 LLM，直接基于 staging 聚类结果生成 TaskRecord。
    若安装了 skillopt_sleep 的 TaskRecord 类型则构造之，否则返回 dict 兼容结构。
    """
    patterns = extract_patterns()
    tasks: list[Any] = []
    try:
        from skillopt_sleep.mine import TaskRecord  # type: ignore
        for i, p in enumerate(patterns):
            tasks.append(TaskRecord(
                task_id=f"weakness_{i}",
                intent=f"修复 skill 优化失败模式: {p['target']}/{p['op']} (出现 {p['count']} 次)",
                split="train",
            ))
    except Exception:
        # 退化：返回纯 dict，供上层自行处理
        for i, p in enumerate(patterns):
            tasks.append({
                "task_id": f"weakness_{i}",
                "intent": f"修复 skill 优化失败模式: {p['target']}/{p['op']} (出现 {p['count']} 次)",
                "split": "train",
            })
    return tasks


def run(staging_dir: str = DEFAULT_STAGING_DIR, use_llm_miner: bool = False) -> dict[str, Any]:
    """执行 WeaknessMiner，返回聚类报告 + 接入 mine() 的结果。"""
    patterns = extract_patterns(staging_dir)
    result: dict[str, Any] = {"patterns": patterns, "n_patterns": len(patterns)}
    # 接入 skillopt_sleep.mine()（若有）
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skillopt-sleep"))
        from skillopt_sleep.mine import mine  # type: ignore
        from skillopt_sleep.types import SessionDigest  # type: ignore

        # 把聚类出的失败 pattern 转成 SessionDigest，喂给 heuristic_mine
        # （每个 pattern → 一个 digest，feedback_signals 记录失败原因，
        #  使 heuristic_mine 能识别 retry chain / failure pattern）
        digests: list[Any] = []
        for i, p in enumerate(patterns):
            digests.append(SessionDigest(
                session_id=f"weakness-pattern-{i}",
                project="skillopt-sleep",
                user_prompts=[f"优化 skill {p['target']}"],
                assistant_finals=[f"rejected edit op={p['op']}: {p['sample_reasons'][0] if p.get('sample_reasons') else ''}"],
                tools_used=["skill_manage"],
                feedback_signals=["still broken"] * min(p["count"], 3),
                n_user_turns=1,
                n_assistant_turns=1,
            ))
        tasks = mine(
            digests,
            llm_miner=llm_miner_callback if use_llm_miner else None,
            max_tasks=max(len(patterns), len(digests), 1),
        )
        result["mined_tasks"] = [getattr(t, "__dict__", t) for t in tasks]
        result["n_tasks"] = len(tasks)
    except Exception as e:
        result["mine_error"] = f"{type(e).__name__}: {e}"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="WeaknessMiner 失败模式挖掘")
    ap.add_argument("--staging-dir", default=DEFAULT_STAGING_DIR)
    ap.add_argument("--use-llm-miner", action="store_true", help="启用 LLM 增强挖掘")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()
    res = run(args.staging_dir, args.use_llm_miner)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"[weakness-miner] 聚类失败模式: {res['n_patterns']} 类")
        for p in res["patterns"][:10]:
            print(f"  {p['target']}/{p['op']} ×{p['count']}  e.g. {p['sample_reasons'][0] if p['sample_reasons'] else ''}")
        if "n_tasks" in res:
            print(f"[weakness-miner] 接入 mine() 产出 TaskRecord: {res['n_tasks']}")
        if "mine_error" in res:
            print(f"[weakness-miner] mine() 未接入（非阻塞）: {res['mine_error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
