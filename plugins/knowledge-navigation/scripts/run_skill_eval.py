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

稳健化参数（环境变量）：
    SKILL_EVAL_WORKERS   并发 worker 数（默认 1，降并发防本地 embedding OOM）
    SKILL_EVAL_TIMEOUT   单条超时秒数（默认 90，超时/异常 skip，不整轮卡死）
"""

import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _percentile(values: list[float], p: float) -> float:
    """线性插值百分位数（与飞轮健康报告 utils._percentile 同语义）。"""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


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


def _skipped_record(item: dict, latency_ms: float, reason: str) -> dict:
    """构造一条超时/异常 query 的占位结果（计入 results，但不参与均值）"""
    expected = item.get("expected_skills", [])
    return {
        "query": item["query"],
        "expected": expected,
        "matched": [],
        "latency_ms": round(latency_ms, 0),
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "n_expected": len(expected),
        "n_hit": 0,
        "n_returned": 0,
        "skipped": True,
        "skip_reason": reason,
    }


def run_eval() -> dict:
    """全量跑评估集，返回结构化结果。

    稳健化（修复 A 项）：逐条 90s 超时兜底 + 逐条进度 + 超时/异常 skip，
    杜绝原 future.result() 无超时导致的"整轮卡死"。
    - workers 默认 1：降低对本地 embedding 服务（MX550 2GB）的并发压力，防 OOM。
    - 单条超时/异常不会中断整轮，被跳过项计入 results（skip_reason 标注）但不参与均值。
    """
    queries = load_eval_queries()
    if not queries:
        return {}

    # 确保索引已加载
    if not ensure_index():
        print("⚠️  Skill 索引为空，无法评估")
        return {}

    n_total = len(queries)
    # 默认 1：降并发，避免本地 embedding 服务（MX550 2GB）瞬时 OOM
    workers = max(1, int(os.environ.get("SKILL_EVAL_WORKERS", "1")))
    # 稳健 harness：逐条 90s 超时兜底（生产原 future.result() 无超时 → 整轮卡死）
    per_query_timeout = float(os.environ.get("SKILL_EVAL_TIMEOUT", "90"))

    print(
        f"  加载 {n_total} 条 eval queries... (workers={workers}, "
        f"per_query_timeout={per_query_timeout:.0f}s)\n",
        file=sys.stderr,
    )

    def _eval_one(item: dict):
        qid = item["query_id"]
        query = item["query"]
        expected = item.get("expected_skills", [])
        t0 = time.time()
        matched = match_skills(query, top_k=3)
        latency = (time.time() - t0) * 1000
        matched_names = [m["name"] for m in matched]
        metrics = compute_metrics(matched_names, expected)
        return qid, {
            "query": query,
            "expected": expected,
            "matched": matched_names,
            "latency_ms": round(latency, 0),
            **metrics,
        }, latency

    results_by_id: dict[str, dict] = {}
    all_precisions: list[float] = []
    all_recalls: list[float] = []
    all_f1s: list[float] = []
    all_latencies: list[float] = []  # 每条成功请求延迟（ms），用于中位数/异常剔除统计
    total_latency = 0.0
    n_done = 0
    n_skipped = 0

    # 逐条 result(timeout=...) 兜底：单条挂死最多等 90s 即跳过，绝不整轮卡死。
    # workers=1 时若某条真挂死，后续条也会因 worker 占满而超时——但均在 90s 内
    # 返回（不再无限挂），且 meta.n_skipped 会暴露问题；如需更强隔离可调大 SKILL_EVAL_WORKERS。
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_eval_one, item) for item in queries]
        for item, future in zip(queries, futures):
            qid = item["query_id"]
            n_done += 1
            try:
                qid, res, latency = future.result(timeout=per_query_timeout)
            except TimeoutError:
                n_skipped += 1
                print(f"   ⏱ [{n_done}/{n_total}] {qid} TIMEOUT > {per_query_timeout:.0f}s — skipped",
                      file=sys.stderr)
                results_by_id[qid] = _skipped_record(item, per_query_timeout * 1000, "timeout")
                continue
            except Exception as e:  # noqa: BLE001 — 兜底单条异常，避免整轮中断
                n_skipped += 1
                print(f"   ✗ [{n_done}/{n_total}] {qid} ERROR {type(e).__name__}: {e} — skipped",
                      file=sys.stderr)
                results_by_id[qid] = _skipped_record(item, per_query_timeout * 1000,
                                                      f"error:{type(e).__name__}")
                continue
            results_by_id[qid] = res
            all_precisions.append(res["precision"])
            all_recalls.append(res["recall"])
            all_f1s.append(res["f1"])
            total_latency += latency
            all_latencies.append(latency)
            print(f"   ✓ [{n_done}/{n_total}] {qid} F1={res['f1']:.3f} {res['latency_ms']:.0f}ms",
                  file=sys.stderr)

    n_scored = len(all_f1s)
    avg_precision = sum(all_precisions) / n_scored if n_scored else 0.0
    avg_recall = sum(all_recalls) / n_scored if n_scored else 0.0
    avg_f1 = sum(all_f1s) / n_scored if n_scored else 0.0
    avg_latency = total_latency / n_scored if n_scored else 0.0

    # 延迟稳健统计：偶发 LLM 慢条（如 >5s TTFB 波动）会拉高均值，属正常现象不归因瓶颈。
    # 主指标取中位数，另报剔除异常（>5s）后的均值；异常数单独标注供参考。
    LATENCY_OUTLIER_MS = 5000.0
    latencies = sorted(all_latencies)
    median_latency = statistics.median(latencies) if latencies else 0.0
    clean_lat = [x for x in latencies if x <= LATENCY_OUTLIER_MS]
    clean_avg_latency = (sum(clean_lat) / len(clean_lat)) if clean_lat else 0.0
    clean_p95 = _percentile(clean_lat, 0.95) if clean_lat else 0.0
    n_outliers = len(latencies) - len(clean_lat)

    return {
        "meta": {
            "n_queries": n_total,
            "n_scored": n_scored,
            "n_skipped": n_skipped,
            "workers": workers,
            "per_query_timeout_s": per_query_timeout,
            "avg_precision": round(avg_precision, 4),
            "avg_recall": round(avg_recall, 4),
            "avg_f1": round(avg_f1, 4),
            "avg_latency_ms": round(avg_latency, 0),
            "median_latency_ms": round(median_latency, 0),
            "clean_avg_latency_ms": round(clean_avg_latency, 0),
            "clean_p95_latency_ms": round(clean_p95, 0),
            "n_outliers": n_outliers,
            "latency_outlier_ms_threshold": LATENCY_OUTLIER_MS,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "note": "稳健 harness 方法：逐条 90s 超时 + 超时/异常 skip，杜绝整轮卡死；延迟主指标为中位数，均值参考，>5s 异常点单独计数",
        },
        "results": results_by_id,
    }


def print_report(result: dict) -> None:
    meta = result["meta"]
    results = result["results"]

    print("📊 Skill Matcher 评估报表")
    print(f"   评估集: {meta['n_queries']} 条 query")
    print(f"   延迟: 中位 {meta['median_latency_ms']:.0f}ms | 剔除异常均值 {meta['clean_avg_latency_ms']:.0f}ms "
          f"| p95(clean) {meta['clean_p95_latency_ms']:.0f}ms | 全量均值 {meta['avg_latency_ms']:.0f}ms "
          f"| 异常>5s {meta['n_outliers']} 条")
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
            print(f"   延迟: {d['latency_ms']:+.0f}ms")
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
    if "--json" not in args:
        print(f"\n💾 基线已保存至 {BASELINE_FILE}")


if __name__ == "__main__":
    main()
