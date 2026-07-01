#!/usr/bin/env python3
"""run_skill_eval.py — Skill Matcher 评估基线采集。

功能：
1. 加载 skill_eval_queries.json（含 query 和 expected_skills）
2. 调用 match_skills(query) 生产匹配结果
3. 计算 Precision@3, Recall@3, F1@3
4. 输出报表（human-readable + --json）+ 保存到 baselines/

用法：
    python3 scripts/run_skill_eval.py
    python3 scripts/run_skill_eval.py --json
    python3 scripts/run_skill_eval.py --compare before.json after.json
"""

import json
import os
import sys
import time
from pathlib import Path

# ── 路径 ──
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
CONFIG_DIR = PROJECT_DIR / "config"
BASELINE_DIR = PROJECT_DIR / "baselines"
EVAL_FILE = CONFIG_DIR / "skill_eval_queries.json"
BASELINE_FILE = BASELINE_DIR / "skill_eval_latest.json"

# ── 导入生产匹配器 ──
sys.path.insert(0, str(PROJECT_DIR / "src"))
from knowledge_navigation.core.skill_matcher import match_skills, ensure_index


def load_eval_queries() -> list[dict]:
    if not EVAL_FILE.exists():
        print(f"❌ 评估集文件不存在: {EVAL_FILE}")
        return []
    with open(EVAL_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_metrics(results: list[str], expected: list[str]) -> dict:
    """计算单条 query 的 Precision@3, Recall@3, F1@3"""
    if not expected:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "n_expected": 0, "n_hit": 0}

    results_set = set(results)
    expected_set = set(expected)
    n_hit = len(results_set & expected_set)
    k = len(results) if results else 3  # 实际返回数，最低 3
    precision = n_hit / k if k > 0 else 0.0
    recall = n_hit / len(expected_set) if expected_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "n_expected": len(expected_set),
        "n_hit": n_hit,
        "n_returned": len(results),
    }


def run_eval() -> dict:
    """全量跑评估集，返回结构化结果"""
    queries = load_eval_queries()
    if not queries:
        return {}

    # 确保索引已加载
    if not ensure_index():
        print("⚠️  Skill 索引为空，无法评估")
        return {}

    print(f"  加载 {len(queries)} 条 eval queries...\n")

    results_by_id: dict[str, dict] = {}
    all_precisions: list[float] = []
    all_recalls: list[float] = []
    all_f1s: list[float] = []
    total_latency = 0.0
    n_total = len(queries)

    for item in queries:
        qid = item["query_id"]
        query = item["query"]
        expected = item.get("expected_skills", [])

        t0 = time.time()
        matched = match_skills(query, top_k=3)
        latency = (time.time() - t0) * 1000
        total_latency += latency

        matched_names = [m["name"] for m in matched]
        metrics = compute_metrics(matched_names, expected)

        results_by_id[qid] = {
            "query": query,
            "expected": expected,
            "matched": matched_names,
            "latency_ms": round(latency, 0),
            **metrics,
        }
        all_precisions.append(metrics["precision"])
        all_recalls.append(metrics["recall"])
        all_f1s.append(metrics["f1"])

    avg_precision = sum(all_precisions) / n_total
    avg_recall = sum(all_recalls) / n_total
    avg_f1 = sum(all_f1s) / n_total
    avg_latency = total_latency / n_total

    return {
        "meta": {
            "n_queries": n_total,
            "avg_precision": round(avg_precision, 4),
            "avg_recall": round(avg_recall, 4),
            "avg_f1": round(avg_f1, 4),
            "avg_latency_ms": round(avg_latency, 0),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        },
        "results": results_by_id,
    }


def print_report(result: dict) -> None:
    meta = result["meta"]
    results = result["results"]

    print("📊 Skill Matcher 评估报表")
    print(f"   评估集: {meta['n_queries']} 条 query")
    print(f"   平均延迟: {meta['avg_latency_ms']:.0f}ms")
    print()
    print(f"   平均 Precision@3:  {meta['avg_precision']:.3f}")
    print(f"   平均 Recall@3:     {meta['avg_recall']:.3f}")
    print(f"   平均 F1@3:         {meta['avg_f1']:.4f}")
    print()

    # 按 F1 排序，最差的排前面
    sorted_qs = sorted(results.items(), key=lambda x: x[1]["f1"])
    print("   F1 最低的 10 条:")
    for qid, info in sorted_qs[:10]:
        flag = "⚠️" if info["f1"] < 0.5 else "  "
        print(f"   {flag} {qid:20s} F1={info['f1']:.3f} P={info['precision']:.2f} "
              f"R={info['recall']:.2f}  expected={info['expected']} → got={info['matched']}")
    print()
    print("   F1 最高的 10 条:")
    for qid, info in sorted_qs[-10:]:
        flag = "✅" if info["f1"] >= 0.8 else "  "
        print(f"   {flag} {qid:20s} F1={info['f1']:.3f} P={info['precision']:.2f} "
              f"R={info['recall']:.2f}  expected={info['expected']}")


def save_baseline(data: dict) -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = BASELINE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(BASELINE_FILE)


def compare_baselines(file_a: str, file_b: str) -> dict:
    with open(file_a, "r") as f:
        baseline_a = json.load(f)
    with open(file_b, "r") as f:
        baseline_b = json.load(f)

    meta_a = baseline_a["meta"]
    meta_b = baseline_b["meta"]

    comparison = {
        "meta": {
            f"before_{k}": v for k, v in meta_a.items()
        },
        "meta_after": meta_b,
        "diff": {
            "precision": round(meta_b["avg_precision"] - meta_a["avg_precision"], 4),
            "recall": round(meta_b["avg_recall"] - meta_a["avg_recall"], 4),
            "f1": round(meta_b["avg_f1"] - meta_a["avg_f1"], 4),
            "latency_ms": round(meta_b["avg_latency_ms"] - meta_a["avg_latency_ms"], 0),
        },
    }
    return comparison


def main() -> None:
    args = set(sys.argv[1:])
    compare_flag = "--compare" in args

    if compare_flag:
        # 找 --compare 后的两个参数
        idx = sys.argv.index("--compare")
        file_a = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else ""
        file_b = sys.argv[idx + 2] if len(sys.argv) > idx + 2 else ""
        if not file_a or not file_b:
            print("用法: run_skill_eval.py --compare before.json after.json")
            sys.exit(1)
        result = compare_baselines(file_a, file_b)
        if "--json" in args:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            d = result["diff"]
            print("📊 Skill Matcher 基线对比")
            print(f"   精度: {d['precision']:+.4f}")
            print(f"   召回: {d['recall']:+.4f}")
            print(f"   F1:   {d['f1']:+.4f}")
            print(f"   延迟: {d['latency_ms']:+d}ms")
        return

    result = run_eval()
    if not result:
        print("⚠️  评估结果为空")
        sys.exit(0)

    if "--json" in args:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_report(result)

    save_baseline(result)
    print(f"\n💾 基线已保存至 {BASELINE_FILE}")


if __name__ == "__main__":
    main()
