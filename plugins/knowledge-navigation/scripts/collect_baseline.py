#!/usr/bin/env python3
"""
collect_baseline.py — 知识导航评估基线采集 + Bootstrap/t-test + delta 检测

用法：
    # 收集基线（默认从 trace.log）
    python3 scripts/collect_baseline.py [log_file] [--json]

    # 周期基线 delta 检测（cron 使用）
    python3 scripts/collect_baseline.py --delta [--trigger]

    # 对比两次基线（如优化前后）
    python3 scripts/collect_baseline.py --compare before.json after.json

    # LLM relevance judge — 评估所有 recall 的质量（不限 eval 匹配）
    # 需要设置 LLM_API_URL / LLM_API_KEY / LLM_MODEL 环境变量
    python3 scripts/collect_baseline.py --judge [log_file]

输出：
    按维度汇总的指标，含 Bootstrap 95% CI + recall@k（有 expected_ids 时）。
"""
import json
import math
import os
import random
import ssl
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    from knowledge_navigation.config import is_test_trace_record
except Exception:  # pragma: no cover - script fallback when package import path is unavailable
    def is_test_trace_record(data: dict) -> bool:
        query = str(data.get("query_trunc", "") or "")
        error = str(data.get("error", "") or "")
        if query in {
            "test recall system",
            "help me search test query",
            "test eval query about memory",
            "unrelated topic here",
        }:
            return True
        if "exact match query about memory" in query:
            return True
        if query.startswith("LiteLLM 配置出问题了"):
            return True
        return error in {"RuntimeError: API down", "RuntimeError: down"}


LOG_PATHS = [
    "~/.hermes/plugins/knowledge-navigation/trace.log",
    "~/.hermes/logs/agent.log",
    "~/.hermes/logs/trace.log",
    "~/.hermes/logs/gateway.log",
]

BASELINE_DIR = Path("~/.hermes/plugins/knowledge-navigation/baselines").expanduser()
BASELINE_FILE = BASELINE_DIR / "baseline_latest.json"
BASELINE_PREV_FILE = BASELINE_DIR / "baseline_prev.json"
DELTA_THRESHOLD = float(os.environ.get("BASELINE_DELTA_THRESHOLD", "0.10"))
JUDGE_PARALLEL = int(os.environ.get("JUDGE_PARALLEL", "5"))

DIMENSIONS = [
    "semantic", "entity", "causal", "temporal", "conflict", "tool", "debug", "api",  # 原有 8 维度
    "complex", "numeric", "workflow",  # P2 增强：复合概念、数字精确、通用流程
]

# 飞书告警限频（同一进程至少间隔 N 秒）
_FEISHU_LAST_NOTIFY: float = 0.0
_FEISHU_NOTIFY_INTERVAL: float = 300.0


def _try_scipy() -> bool:
    """尝试导入 scipy.stats，用于精确 t 分布计算。"""
    try:
        global _t_distribution_p
        import scipy.stats as _stats
        def _scipy_t_p(t: float, df: float) -> float:
            return 2.0 * _stats.t.sf(abs(t), df)
        _t_distribution_p = _scipy_t_p
        return True
    except ImportError:
        pass
    return False


# ========== 正态近似（scipy 不可用时的降级）==========

def _normal_cdf(x: float) -> float:
    """标准正态 CDF（Abramowitz & Stegun 26.2.17 近似）。"""
    if x < 0:
        return 1.0 - _normal_cdf(-x)
    b0 = 0.2316419
    b1 = 0.319381530
    b2 = -0.356563782
    b3 = 1.781477937
    b4 = -1.821255978
    b5 = 1.330274429
    t = 1.0 / (1.0 + b0 * x)
    phi = 0.3989422804014327 * math.exp(-x * x / 2.0)
    return 1.0 - phi * (b1 * t + b2 * t**2 + b3 * t**3 + b4 * t**4 + b5 * t**5)


def _normal_t_p(t: float, df: float) -> float:
    """用正态近似计算双侧 t 检验 p 值。大 df 时精确，小 df 时保守。"""
    if df <= 0:
        return 1.0
    return 2.0 * (1.0 - _normal_cdf(abs(t)))


# 默认用正态近似，_try_scipy() 成功时替换为 scipy 版本
_t_distribution_p = _normal_t_p


# ========== Bootstrap ==========

def bootstrap_ci(
    values: list[float],
    n_resamples: int = 1000,
    ci: float = 0.95,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Bootstrap 重采样计算置信区间。

    Returns:
        (mean, ci_lower, ci_upper)
    """
    if not values:
        return 0.0, 0.0, 0.0
    if len(values) < 2:
        return values[0], values[0], values[0]

    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(n_resamples):
        sample = [values[rng.randint(0, n - 1)] for _ in range(n)]
        means.append(sum(sample) / n)

    means.sort()
    tail = int(n_resamples * (1 - ci) / 2)
    lower = means[tail]
    upper = means[n_resamples - 1 - tail]
    return sum(values) / n, lower, upper


# ========== t-test (Welch's) ==========

def welch_ttest(a: list[float], b: list[float]) -> dict[str, Any]:
    """Welch's t-test — 两组独立样本，方差不要求相等。

    Returns:
        {t_stat, p_value, mean_a, mean_b, diff, significant}
    """
    result: dict[str, Any] = {
        "mean_a": 0.0,
        "mean_b": 0.0,
        "diff": 0.0,
        "t_stat": 0.0,
        "p_value": 1.0,
        "significant": False,
    }
    if not a or not b:
        return result

    n_a, n_b = len(a), len(b)
    mean_a = sum(a) / n_a
    mean_b = sum(b) / n_b
    var_a = sum((x - mean_a) ** 2 for x in a) / (n_a - 1) if n_a > 1 else 0.0
    var_b = sum((x - mean_b) ** 2 for x in b) / (n_b - 1) if n_b > 1 else 0.0

    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se == 0:
        return {**result, "mean_a": mean_a, "mean_b": mean_b, "diff": mean_b - mean_a}

    t_stat = (mean_b - mean_a) / se

    # Welch-Satterthwaite 自由度
    df_num = (var_a / n_a + var_b / n_b) ** 2
    df_den = (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
    df = df_num / df_den if df_den > 0 else 1.0

    p_value = _t_distribution_p(t_stat, df)

    return {
        "mean_a": round(mean_a, 4),
        "mean_b": round(mean_b, 4),
        "diff": round(mean_b - mean_a, 4),
        "t_stat": round(t_stat, 4),
        "p_value": round(p_value, 6),
        "significant": p_value < 0.05,
    }


# ========== 基线采集 ==========

def find_log_file() -> str:
    """按优先级查找可读的日志文件（trace.log > agent.log > gateway.log）。"""
    for path in LOG_PATHS:
        expanded = Path(path).expanduser()
        if expanded.exists():
            return str(expanded)
    return ""


def collect_baseline(log_file: str = "") -> dict:
    """从 trace.log 中提取评估基线数据（支持精确 + 模糊 eval 匹配）。

    改动说明（2026-06-29）：
    - 原逻辑只采集 eval_counted=True 的记录，导致生产数据几乎采不到
    - 现在采集所有 recall_success 事件，精确/模糊匹配都纳入
    - 分组 key：eval_query_id > eval_candidate_id > query_trunc（兜底）

    Args:
        log_file: 日志文件路径，为空时自动查找

    Returns:
        {query_id: {total_requests, avg_*, ci_*, raw_records}}
    """
    if not log_file:
        log_file = find_log_file()

    path = Path(log_file).expanduser()
    if not path.exists():
        print(f"❌ 日志文件不存在: {path}", file=sys.stderr)
        return {}

    records_by_qid = defaultdict(list)

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue

            event = data.get("event", "")

            # ── 分支 1: recall_success 事件 ──
            if event == "recall_success":
                if is_test_trace_record(data):
                    continue

                # 分组 key 优先级：eval_query_id > eval_candidate_id > query_trunc
                qid = data.get("eval_query_id")
                if not qid:
                    qid = data.get("eval_candidate_id")
                if not qid:
                    qid = data.get("query_trunc", "")[:50] or "unknown"

                records_by_qid[qid].append({
                    "timestamp": data.get("timestamp", ""),
                    "total_results": data.get("total_results", 0),
                    "kept_results": data.get("kept_results", 0),
                    "avg_score": data.get("score_stats", {}).get("avg", 0.0),
                    "excluded_marked": data.get("excluded_marked", 0),
                    "latency_ms": data.get("latency_ms", 0),
                    "total_chars": data.get("total_chars", 0),
                    "injected_count": data.get("injected_count", 0),
                    "recalled_ids": data.get("recalled_ids", []),
                    "eval_expected_ids": data.get("eval_expected_ids", []),
                    "eval_recall_hit": data.get("eval_recall_hit", 0),
                    "eval_recall_k": data.get("eval_recall_k", 0),
                    "eval_counted": data.get("eval_counted", False),
                    "eval_match_method": data.get("eval_match_method", ""),
                    "query_trunc": data.get("query_trunc", ""),
                })

            # ── 分支 2: eval_match 事件（匹配质量数据）──
            elif event == "eval_match":
                qid = data.get("matched_query_id") or data.get("query_id", "")
                if not qid:
                    continue

                accepted = data.get("accepted", False)
                records_by_qid[qid].append({
                    "timestamp": data.get("timestamp", ""),
                    "total_results": 1,  # 1 次匹配
                    "kept_results": 1 if accepted else 0,
                    "avg_score": data.get("score", 0.0),
                    "excluded_marked": 0,
                    "latency_ms": 0,
                    "total_chars": 0,
                    "injected_count": 0,
                    "recalled_ids": [],
                    "eval_expected_ids": [],
                    "eval_recall_hit": 0,
                    "eval_recall_k": 0,
                    "eval_counted": data.get("counted", False),
                    "eval_match_method": data.get("match_type", ""),
                    "query_trunc": data.get("user_message_trunc", ""),
                })

    # 汇总 + Bootstrap CI
    baseline: dict[str, Any] = {}
    for qid, records in records_by_qid.items():
        n = len(records)
        kept_vals = [r["kept_results"] for r in records]
        score_vals = [r["avg_score"] for r in records]
        latency_vals = [r["latency_ms"] for r in records]

        kept_mean, kept_lo, kept_hi = bootstrap_ci(kept_vals)
        score_mean, score_lo, score_hi = bootstrap_ci(score_vals)
        latency_mean, latency_lo, latency_hi = bootstrap_ci(latency_vals)

        # recall@k 汇总（仅精确匹配有）
        recall_hits = [r["eval_recall_hit"] for r in records if r["eval_recall_k"] > 0]
        recall_ks = [r["eval_recall_k"] for r in records if r["eval_recall_k"] > 0]

        # 精确 vs 模糊匹配统计
        counted_true = sum(1 for r in records if r["eval_counted"])
        counted_false = n - counted_true

        # 推断维度（从 qid 格式：semantic_xxx, entity_xxx 等）
        dim = qid.split("_")[0] if "_" in qid else "unknown"

        baseline[qid] = {
            "total_requests": n,
            "dimension": dim,
            "eval_counted_true": counted_true,
            "eval_counted_false": counted_false,
            "avg_kept_results": round(kept_mean, 2),
            "kept_ci_95": [round(kept_lo, 2), round(kept_hi, 2)],
            "avg_score": round(score_mean, 4),
            "score_ci_95": [round(score_lo, 4), round(score_hi, 4)],
            "avg_latency_ms": round(latency_mean, 0),
            "latency_ci_95": [round(latency_lo, 0), round(latency_hi, 0)],
            "avg_total_results": round(sum(r["total_results"] for r in records) / n, 1),
            "avg_excluded_marked": round(sum(r["excluded_marked"] for r in records) / n, 1),
            "avg_injected_count": round(sum(r["injected_count"] for r in records) / n, 1),
            # 不保存 raw_records — 基线 JSON 文件膨胀
        }
        if recall_hits:
            recall_rates = [h / k for h, k in zip(recall_hits, recall_ks)]
            recall_mean, recall_lo, recall_hi = bootstrap_ci(recall_rates)
            baseline[qid]["avg_recall_at_k"] = round(recall_mean, 4)
            baseline[qid]["recall_ci_95"] = [round(recall_lo, 4), round(recall_hi, 4)]

    return baseline


def compute_dimension_stats(baseline: dict) -> dict[str, Any]:
    """按维度汇总，含 Bootstrap CI + 精确/模糊匹配分布。

    Returns:
        {dimension: {query_count, metrics_with_ci, eval_counted_true, eval_counted_false}}
    """
    by_dim: dict[str, list[dict]] = defaultdict(list)
    for qid, data in baseline.items():
        # 优先使用 baseline 中已存的 dimension（来自 JSON 或 qid 推断）
        dim = data.get("dimension") or qid.split("_")[0]
        by_dim[dim].append(data)

    stats: dict[str, Any] = {}
    for dim in DIMENSIONS:
        items = by_dim.get(dim, [])
        if not items:
            continue

        kept_vals = [d["avg_kept_results"] for d in items]
        score_vals = [d["avg_score"] for d in items]
        latency_vals = [d["avg_latency_ms"] for d in items]

        kept_m, kept_lo, kept_hi = bootstrap_ci(kept_vals)
        score_m, score_lo, score_hi = bootstrap_ci(score_vals)
        latency_m, latency_lo, latency_hi = bootstrap_ci(latency_vals)

        # 精确 vs 模糊匹配分布
        counted_true = sum(d.get("eval_counted_true", 0) for d in items)
        counted_false = sum(d.get("eval_counted_false", 0) for d in items)

        stats[dim] = {
            "query_count": len(items),
            "kept": {"mean": round(kept_m, 2), "ci_95": [round(kept_lo, 2), round(kept_hi, 2)]},
            "score": {"mean": round(score_m, 4), "ci_95": [round(score_lo, 4), round(score_hi, 4)]},
            "latency_ms": {"mean": round(latency_m, 0), "ci_95": [round(latency_lo, 0), round(latency_hi, 0)]},
            "eval_counted_true": counted_true,
            "eval_counted_false": counted_false,
        }
    return stats


# ========== 基线对比 ==========

def compare_baselines(file_a: str, file_b: str) -> dict[str, Any]:
    """对比两个基线，对每个维度做 t-test。

    Args:
        file_a: 旧基线（before）JSON 文件
        file_b: 新基线（after）JSON 文件

    Returns:
        {dimension: {kept: {t-test result}, score: {t-test result}, latency: {t-test result}}}
    """
    with open(file_a, "r") as f:
        base_a_raw = json.load(f)
    with open(file_b, "r") as f:
        base_b_raw = json.load(f)

    dim_a = compute_dimension_stats(base_a_raw)
    dim_b = compute_dimension_stats(base_b_raw)

    comparison: dict[str, Any] = {}
    for dim in DIMENSIONS:
        if dim not in dim_b:
            continue
        # 对每个维度提取查询级的原始数据用于 t-test
        records_a = [base_a_raw[qid] for qid in base_a_raw if qid.startswith(dim)]
        records_b = [base_b_raw[qid] for qid in base_b_raw if qid.startswith(dim)]

        if not records_a or not records_b:
            continue

        metrics = ["avg_kept_results", "avg_score", "avg_latency_ms"]
        metric_labels = {"avg_kept_results": "kept", "avg_score": "score", "avg_latency_ms": "latency_ms"}
        dim_result: dict[str, Any] = {"query_count_a": len(records_a), "query_count_b": len(records_b)}

        for metric in metrics:
            va = [r[metric] for r in records_a]
            vb = [r[metric] for r in records_b]
            dim_result[metric_labels[metric]] = welch_ttest(va, vb)

        comparison[dim] = dim_result

    return comparison


# ========== Delta 检测（cron 使用） ==========

def load_previous_baseline() -> dict:
    if BASELINE_PREV_FILE.exists():
        try:
            return json.loads(BASELINE_PREV_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_baseline(current: dict) -> None:
    """原子保存基线：先轮转 prev，再写 .tmp 再 rename，避免中断丢失。"""
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    # 将当前 latest 轮转为 prev（供下次 --delta 对比）
    if BASELINE_FILE.exists():
        BASELINE_FILE.rename(BASELINE_PREV_FILE)
    tmp = BASELINE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(BASELINE_FILE)


def _check_regression(cur_val: float, prev_val: float, metric: str) -> dict | None:
    """检测单指标退化。返回退化信息或 None。"""
    if prev_val == 0:
        return None
    delta = (cur_val - prev_val) / prev_val
    if delta < -DELTA_THRESHOLD:
        return {
            "metric": metric,
            "previous": round(prev_val, 4),
            "current": round(cur_val, 4),
            "delta_pct": round(delta * 100, 1),
        }
    return None


def detect_delta(current: dict, previous: dict) -> dict:
    """检测三个指标的退化：kept_results、score、latency_ms。"""
    if not previous:
        return {}
    regressions: dict[str, dict] = {}
    for qid, cur in current.items():
        prev = previous.get(qid)
        if not prev:
            continue

        cur_k = cur.get("avg_kept_results", 0)
        prev_k = prev.get("avg_kept_results", cur_k)
        r = _check_regression(cur_k, prev_k, "avg_kept_results")
        if r:
            regressions[f"{qid}.kept"] = r

        cur_s = cur.get("avg_score", 0)
        prev_s = prev.get("avg_score", cur_s)
        r = _check_regression(cur_s, prev_s, "avg_score")
        if r:
            regressions[f"{qid}.score"] = r

        cur_l = cur.get("avg_latency_ms", 0)
        prev_l = prev.get("avg_latency_ms", cur_l)
        if prev_l > 0 and cur_l > prev_l * 1.5:
            regressions[f"{qid}.latency"] = {
                "metric": "avg_latency_ms",
                "previous": round(prev_l, 0),
                "current": round(cur_l, 0),
                "delta_pct": round((cur_l - prev_l) / prev_l * 100, 1),
            }

    return regressions


def notify_feishu_regression(regressions: dict) -> None:
    """飞书告警（带限频），退化幅度最大的排前面。"""
    global _FEISHU_LAST_NOTIFY
    if not regressions:
        return
    now = time.time()
    if now - _FEISHU_LAST_NOTIFY < _FEISHU_NOTIFY_INTERVAL:
        print(f"   ⚠️ 飞书告警跳过：距上次告警不足 {_FEISHU_NOTIFY_INTERVAL:.0f}s")
        return

    channel = os.environ.get("FEISHU_HOME_CHANNEL", "")
    if not channel:
        print("   ⚠️ 飞书告警跳过：未配置 FEISHU_HOME_CHANNEL")
        return

    # 按退化幅度（|delta_pct|）倒序排列，取 top 20
    sorted_items = sorted(regressions.items(), key=lambda x: abs(x[1]["delta_pct"]), reverse=True)
    lines = [
        f"{qid}: {r['metric']} {r['previous']}→{r['current']} ({r['delta_pct']}%)"
        for qid, r in sorted_items[:20]
    ]
    text = "🔴 知识导航基线退化告警\n" + "\n".join(lines)

    import subprocess
    result = subprocess.run(
        ["lark-cli", "im", "+messages-send", "--chat-id", channel, "--text", text, "--as", "bot"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode == 0:
        print(f"   ✅ 飞书告警已发送 ({len(lines)}/{len(regressions)} 条)")
        _FEISHU_LAST_NOTIFY = now
    else:
        print(f"   ⚠️ 飞书告警发送失败: {result.stderr[:200]}")


# ========== 报表输出 ==========

def print_report(baseline: dict, stats: dict[str, Any], log_file: str = "") -> None:
    """输出可读基线报表。"""
    total = len(baseline)
    covered = sum(1 for d in DIMENSIONS if d in stats)
    source = log_file if log_file else find_log_file()

    # 统计精确 vs 模糊匹配总数
    total_exact = sum(d.get("eval_counted_true", 0) for d in baseline.values())
    total_fuzzy = sum(d.get("eval_counted_false", 0) for d in baseline.values())

    print(f"📊 评估基线报表 (Bootstrap 95% CI)")
    print(f"   来源: {source}")
    print(f"   覆盖查询: {total} 条 | {covered}/{len(DIMENSIONS)} 维度")
    print(f"   精确匹配: {total_exact} 次 | 模糊匹配: {total_fuzzy} 次")
    print()

    for dim in DIMENSIONS:
        dim_info = stats.get(dim)
        if not dim_info:
            continue
        qc = dim_info["query_count"]
        kept = dim_info["kept"]
        score = dim_info["score"]
        latency = dim_info["latency_ms"]
        dim_exact = dim_info.get("eval_counted_true", 0)
        dim_fuzzy = dim_info.get("eval_counted_false", 0)
        print(f"  [{dim}] {qc} 条查询 (精确 {dim_exact} / 模糊 {dim_fuzzy})")
        print(f"    kept:  {kept['mean']:.2f}  [{kept['ci_95'][0]:.2f}, {kept['ci_95'][1]:.2f}]")
        print(f"    score: {score['mean']:.3f}  [{score['ci_95'][0]:.3f}, {score['ci_95'][1]:.3f}]")
        print(f"    delay: {latency['mean']:.0f}ms [{latency['ci_95'][0]:.0f}, {latency['ci_95'][1]:.0f}]ms")
        print()

    if stats:
        print(f"  总查询数: {total}")
        avg_kept = sum(d['kept']['mean'] for d in stats.values()) / len(stats)
        print(f"  均值 kept: {avg_kept:.2f}")
    else:
        print("  总查询数: 0（无数据）")


def print_comparison_report(comparison: dict[str, Any]) -> None:
    """输出基线对比报表。"""
    print("📊 基线对比 (Welch t-test, α=0.05)")
    print()

    for dim, result in comparison.items():
        qa = result["query_count_a"]
        qb = result["query_count_b"]
        print(f"  [{dim}] {qa} vs {qb} 查询")
        for metric in ["kept", "score", "latency_ms"]:
            m = result.get(metric)
            if not m:
                continue
            sig = "🟢" if m["significant"] else "⚪"
            print(f"    {metric:12s} {sig}  {m['mean_a']:.3f} → {m['mean_b']:.3f}  "
                  f"diff={m['diff']:+.4f}  p={m['p_value']:.6f}  t={m['t_stat']:.3f}")
        print()


# ========== LLM Relevance Judge ==========

def collect_all_recalls(log_file: str) -> list[dict]:
    """读取 trace.log 中所有 recall_success 记录（不限 eval 匹配）。"""
    if not log_file:
        log_file = find_log_file()
    path = Path(log_file).expanduser()
    if not path.exists():
        return []

    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if '"event": "recall_success"' not in line:
                continue
            try:
                data = json.loads(line)
                if is_test_trace_record(data):
                    continue
                records.append({
                    "timestamp": data.get("timestamp", ""),
                    "query_trunc": data.get("query_trunc", ""),
                    "kept_results": data.get("kept_results", 0),
                    "injected_count": data.get("injected_count", 0),
                    "avg_score": data.get("score_stats", {}).get("avg", 0.0),
                    "latency_ms": data.get("latency_ms", 0),
                    "eval_query_id": data.get("eval_query_id", ""),
                })
            except (json.JSONDecodeError, TypeError):
                continue
    return records


def _judge_one(rec: dict, config: dict | None) -> tuple[float, bool] | tuple[None, Exception]:
    """单条 recall 的 LLM relevance 评分。返回 (score, True) 或 (None, error)。"""
    query = rec.get("query_trunc") or "(空查询)"
    kept = rec.get("kept_results", 0)
    score = rec.get("avg_score", 0)
    prompt = f"""你是一个 RAG 检索质量评估员。

用户发问：{query}

检索结果：
- 召回条数：{kept}
- 平均 rerank 分数：{score:.4f}

请只输出一个 0-1 之间的数字，表示此次检索结果与用户发问的相关程度：
0 = 完全无关
0.3 = 部分相关
0.7 = 比较相关
1.0 = 完全相关
只输出数字，不要其他文字。"""

    headers = {"Content-Type": "application/json"}
    if config.get("key"):
        headers["Authorization"] = f"Bearer {config['key']}"
    body = json.dumps({
        "model": config.get("model", "s-deepseek-v4-flash"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 250,
        "extra_body": {"thinking": {"type": "disabled"}},
    }).encode("utf-8")
    ctx = ssl.create_default_context()
    if os.environ.get("JUDGE_INSECURE", "").lower() in ("1", "true", "yes"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(config["url"], data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            resp_text = resp.read().decode("utf-8")
            resp_data = json.loads(resp_text)
            content = resp_data["choices"][0]["message"]["content"].strip()
            if not content:
                return None, RuntimeError(f"empty content from LLM")
            llm_score = float(content)
            llm_score = max(0.0, min(1.0, llm_score))
            return llm_score, True
    except Exception as e:
        return None, e


def run_judge(log_file: str, config: dict | None = None) -> dict[str, Any]:
    """用 LLM 评估所有 recall 的 relevance（支持并发调用）。

    Args:
        log_file: trace.log 路径
        config: LLM 配置 {url, key, model}

    Returns:
        {total, scored, relevant_rate, avg_score, by_hour: [...]}
    """
    records = collect_all_recalls(log_file)
    if not records:
        print("⚠️  未找到 recall_success 记录。")
        return {}

    print(f"📡 评估 {len(records)} 次 recall 的 relevance（并行 {JUDGE_PARALLEL} 路）...", file=sys.stderr)

    relevant_count = 0
    judged = 0
    scores: list[float] = []

    # 采样：最多评 200 条
    sample = records[:min(200, len(records))]

    if config and JUDGE_PARALLEL > 1:
        # 并发模式
        with ThreadPoolExecutor(max_workers=JUDGE_PARALLEL) as pool:
            futures = {pool.submit(_judge_one, rec, config): i for i, rec in enumerate(sample)}
            for future in as_completed(futures):
                idx = futures[future]
                result = future.result()
                if result is None or result[0] is None:
                    continue
                llm_score, ok = result
                if ok:
                    scores.append(llm_score)
                    if llm_score >= 0.5:
                        relevant_count += 1
                    judged += 1
                    if judged % 20 == 0:
                        print(f"   已评 {judged}/{len(sample)} 条...", file=sys.stderr)
    else:
        # 串行模式（降级）
        for i, rec in enumerate(sample):
            result = _judge_one(rec, config)
            if result is None or result[0] is None:
                continue
            llm_score, ok = result
            if ok:
                scores.append(llm_score)
                if llm_score >= 0.5:
                    relevant_count += 1
                judged += 1
                if (i + 1) % 20 == 0:
                    print(f"   已评 {i+1}/{len(sample)} 条...", file=sys.stderr)

    return {
        "total_records": len(records),
        "judged": judged,
        "relevant_rate": round(relevant_count / judged, 4) if judged else 0.0,
        "avg_relevance": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "relevance_ci_95": list(bootstrap_ci(scores)[1:3]) if len(scores) > 1 else [0, 0],
    }


def print_judge_report(result: dict[str, Any]) -> None:
    """输出 relevance judge 报表。"""
    if not result:
        return
    print(f"📊 LLM Relevance Judge 报表")
    print(f"   总 recall 记录: {result['total_records']}")
    print(f"   已评分: {result['judged']} 条")
    print()
    print(f"   相关率 (score>=0.5): {result['relevant_rate']*100:.1f}%")
    print(f"   平均 relevance:  {result['avg_relevance']:.4f}")
    if result.get("relevance_ci_95"):
        print(f"   Bootstrap 95% CI: [{result['relevance_ci_95'][0]:.4f}, {result['relevance_ci_95'][1]:.4f}]")
    print()
    print(f"   （采样 200 条，LLM 评估。低分说明 recall 需要优化）")


# ========== CLI 入口 ==========

def _parse_args() -> dict[str, Any]:
    """简易参数解析，兼容原有 sys.argv 扫描风格。"""
    args: dict[str, Any] = {
        "compare": None,
        "judge": False,
        "json": False,
        "delta": False,
        "trigger": False,
        "log_file": "",
    }
    skip_next = False
    for i, arg in enumerate(sys.argv[1:]):
        if skip_next:
            skip_next = False
            continue
        if arg == "--compare":
            args["compare"] = (sys.argv[i + 2] if len(sys.argv) > i + 2 else "",
                               sys.argv[i + 3] if len(sys.argv) > i + 3 else "")
            skip_next = True
        elif arg == "--judge":
            args["judge"] = True
            # 可选 log_file 跟在 --judge 后
            if len(sys.argv) > i + 2 and not sys.argv[i + 2].startswith("--"):
                args["log_file"] = sys.argv[i + 2]
        elif arg == "--json":
            args["json"] = True
        elif arg == "--delta":
            args["delta"] = True
        elif arg == "--trigger":
            args["trigger"] = True
        elif not arg.startswith("--") and not args["log_file"]:
            args["log_file"] = arg
    return args


def main() -> None:
    args = _parse_args()

    if args["compare"]:
        file_a, file_b = args["compare"]
        if not file_a or not file_b:
            print("用法: collect_baseline.py --compare before.json after.json")
            sys.exit(1)
        comparison = compare_baselines(file_a, file_b)
        if args["json"]:
            print(json.dumps(comparison, indent=2, ensure_ascii=False))
        else:
            print_comparison_report(comparison)
        return

    if args["judge"]:
        llm_config = None
        if os.getenv("LLM_API_URL"):
            llm_config = {
                "url": os.getenv("LLM_API_URL", "http://127.0.0.1:4142/v1/chat/completions"),
                "key": os.getenv("LLM_API_KEY", ""),
                "model": os.getenv("LLM_MODEL", "s-deepseek-v4-flash"),
            }
        result = run_judge(args["log_file"], config=llm_config)
        if args["json"]:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print_judge_report(result)
        return

    log_file = args["log_file"]
    baseline = collect_baseline(log_file)

    if not baseline:
        print("⚠️  未找到评估基线数据。")
        print("   确保 pre_llm_call 插件已启用 eval_query_id 日志。")
        sys.exit(0)

    stats = compute_dimension_stats(baseline)

    if args["json"]:
        output: Any = {  # pyright: ignore[reportUninitializedVariable]
            "by_query": baseline,
            "by_dimension": stats,
        }
    else:
        print_report(baseline, stats, log_file)

    if args["delta"]:
        previous = load_previous_baseline()
        regressions = detect_delta(baseline, previous)
        if regressions:
            if args["json"]:
                output["delta"] = {
                    "regressions": regressions,
                    "threshold_pct": DELTA_THRESHOLD * 100,
                }
            else:
                print(f"\n🔴 检测到 {len(regressions)} 条退化（阈值 >{DELTA_THRESHOLD*100:.0f}%）：")
                for qid, r in sorted(regressions.items(), key=lambda x: abs(x[1]["delta_pct"]), reverse=True):
                    print(f"  {qid.split('.')[-1]}: {r['current']}（{r['metric']}，从 {r['previous']} 下降 {r['delta_pct']}%）")
            if args["trigger"]:
                notify_feishu_regression(regressions)
        else:
            if args["json"]:
                output["delta"] = {"regressions": {}, "threshold_pct": DELTA_THRESHOLD * 100, "status": "no_regression"}
            else:
                print(f"\n🟢 无退化查询（阈值 {DELTA_THRESHOLD*100:.0f}%）")
        save_baseline(baseline)
        if args["json"]:
            output["baseline_saved"] = str(BASELINE_FILE)
        else:
            print(f"\n💾 基线已保存至 {BASELINE_FILE}")

    if args["json"]:
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return
    else:
        # 写 JSON 到文件（用于后续 --compare）
        output_path = Path("baseline_output.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(baseline, f, indent=2, ensure_ascii=False)
        if not args["json"]:
            print(f"  (原始数据已写入 {output_path})")

    # 没有 --delta 时也保存基线（供 cron delta 检测使用）
    save_baseline(baseline)


if __name__ == "__main__":
    # 启动时尝试用 scipy 获取精确 p 值
    _try_scipy()
    main()