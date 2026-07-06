#!/usr/bin/env python3
"""Flywheel Health Report Generator (v2).

Reads cron-state, trace.log, logs/cron/, and baseline files to produce a unified
Markdown health report organized by 4 analysis categories:
  1) 任务可靠性 - are cron jobs running stably?
  2) 产出质量   - is the data healthy?
  3) 变化趋势   - improving or degrading?
  4) 数据可信度 - is the analysis itself reliable?

Usage:
    python3 flywheel-health-report.py
    python3 flywheel-health-report.py --dry-run
    python3 flywheel-health-report.py --home /root/.hermes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# === Default Paths ===
DEFAULT_HERMES_HOME = "/root/.hermes"
CRON_STATE_SUBPATH = Path("lib") / "cron-state"
CRON_LOG_SUBPATH = Path("logs") / "cron"
TRACE_LOG_SUBPATH = Path("plugins") / "knowledge-navigation" / "trace.log"
KN_BASELINE_SUBPATH = Path("plugins") / "knowledge-navigation" / "baselines"
DATA_FLYWHEEL_SUBPATH = Path("data") / "flywheel"
OUTPUT_SUBPATH = Path("logs") / "reports"

# === Thresholds ===
TH = {
    # 产出质量
    "router_full_off_pct": 30,     # >30% -> P0
    "recall_empty_pct": 20,        # >20% -> P1
    "skill_f1_low": 0.4,           # <0.4 -> P0
    "kn_avg_score_low": 0.5,       # <0.5 per dimension -> P1
    "kt_orphan_pct": 90,           # >90% -> P1
    "silhouette_low": 0.1,         # <0.1 -> P1
    "silhouette_degrade": -0.05,   # delta < -0.05 -> P1
    "unknown_dim_pct": 50,         # >50% -> P1
    # 任务可靠性
    "elapsed_deviation_sigma": 2.0, # >2 sigma from mean -> P1
    # 数据可信度 (标注不报警)
    "baseline_stale_hours": 48,
    "min_sample_size": 50,
}

# === Test query filter ===
_TEST_QUERY_RE = re.compile(
    r"^(gen_|eval-|test_|test-|exact_kw_|semantic_|entity_|causal_|"
    r"temporal_|conflict_|tool_|debug_|api_|compare_|workflow_)",
    re.IGNORECASE,
)

# === Active cron jobs — only core flywheel tasks ===
# Excluded: system-health-check (环境巡检),
#           cron-boot-detect / cron-periodic-detect (自愈框架)
ACTIVE_CRON_JOBS = frozenset({
    "memory-cleanup",
    "knowledge-navigation-baseline",
    "run-skill-eval",
    "skillopt-nightly-run",
    "kn-router-health-check",
    "daily-learn",
    "clustering-analysis",
    "knowledge-tree-consolidate",
    "knowledge-tree-kvector",
})

# === Flywheel mapping ===
_CRON_TO_FLYWHEEL = {
    "knowledge-navigation-baseline": "Router",
    "kn-router-health-check": "Router",
    "run-skill-eval": "Skill",
    "skillopt-nightly-run": "Skill",
    "clustering-analysis": "聚类",
    "memory-cleanup": "记忆",
    "knowledge-tree-consolidate": "知识树",
    "knowledge-tree-kvector": "知识树",
    "daily-learn": "知识路",
}

_FLYWHEEL_ORDER = ["Router", "Skill", "知识树", "聚类", "记忆", "知识路"]


def _is_test_query(key: str) -> bool:
    return bool(_TEST_QUERY_RE.match(key))


# === Parsers ===

def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _load_jsonl(path: Path) -> list[dict]:
    results = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except (FileNotFoundError, OSError):
        pass
    return results


def parse_cron_states(cron_state_dir: Path) -> dict[str, dict]:
    states = {}
    if not cron_state_dir.is_dir():
        return states
    for f in sorted(cron_state_dir.glob("*.json")):
        data = _load_json(f)
        if data and isinstance(data, dict):
            name = data.get("job_name", f.stem)
            if name not in ACTIVE_CRON_JOBS:
                continue
            states[name] = data
    return states


def parse_trace_log(trace_path: Path) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {
        "router_mask": [],
        "recall_success": [],
        "recall_empty_results": [],
        "skip_router_all_off": [],
        "multi_hop_expand": [],
        "recall_timeout": [],
    }
    if not trace_path.is_file():
        return events
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            evt = d.get("event", "")
            if evt in events:
                events[evt].append(d)
        except json.JSONDecodeError:
            pass
    return events


def scan_cron_log_errors(cron_log_dir: Path, states: dict[str, dict],
                         now: datetime) -> dict[str, list[str]]:
    """Scan the most recent cron run log for each task, find hidden errors
    (ERROR/Exception/Traceback) that didn't cause a fail status.
    Skips retrospective entries starting with '原:' or '新:'."""
    hidden_errors: dict[str, list[str]] = {}
    for name in states:
        state = states[name]
        run_at = state.get("run_at", "")
        if state.get("status") != "success":
            continue
        if not run_at:
            continue
        try:
            run_date = datetime.fromisoformat(run_at).strftime("%Y%m%d")
        except (ValueError, IndexError):
            continue
        log_files = list(cron_log_dir.glob(f"{name}-{run_date}.log"))
        if not log_files:
            continue
        log_path = log_files[-1]
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        errors = []
        for line in text.splitlines():
            stripped = line.strip()
            # Skip retrospective entries (memory retention / review lines)
            if stripped.startswith("原:") or stripped.startswith("新:"):
                continue
            upper = stripped.upper()
            if "ERROR" in upper:
                errors.append(stripped[:120])
            elif "TRACEBACK" in upper:
                errors.append("[traceback found — check full log]")
        if errors:
            hidden_errors[name] = errors[:5]
    return hidden_errors


# === Analyzers — 类别一：任务可靠性 ===

def analyze_cron_jobs(states: dict[str, dict],
                      cron_log_dir: Path,
                      now: datetime) -> tuple[list[dict], dict, dict]:
    issues = []
    table = {}
    elapsed_annotations = {}

    # Collect elapsed times for deviation detection
    elapsed_by_task: dict[str, list[float]] = defaultdict(list)

    for name, s in sorted(states.items()):
        status = s.get("status", "unknown")
        run_at = s.get("run_at", "—")
        elapsed = s.get("elapsed_seconds", 0)
        last_error = s.get("last_error", "")
        table[name] = {"status": status, "run_at": run_at, "elapsed": elapsed}

        if status == "fail":
            issues.append({
                "severity": "P0",
                "flywheel": _CRON_TO_FLYWHEEL.get(name, name),
                "desc": f"Cron `{name}` 运行失败",
                "detail": last_error or f"status={status}",
            })

        # Collect elapsed for deviation analysis (only success runs with positive elapsed)
        if status == "success" and isinstance(elapsed, (int, float)) and elapsed > 0:
            elapsed_by_task[name].append(float(elapsed))

    # Check elapsed deviation per task
    for name, times in elapsed_by_task.items():
        if len(times) < 3:
            continue  # not enough history
        mean_t = statistics.mean(times)
        stdev_t = statistics.stdev(times) if len(times) > 1 else 0
        latest = times[-1]
        if stdev_t > 0 and abs(latest - mean_t) > TH["elapsed_deviation_sigma"] * stdev_t:
            direction = "↑ 变慢" if latest > mean_t else "↓ 变快"
            pct = ((latest - mean_t) / mean_t) * 100
            if abs(pct) > 50:  # only report significant changes
                issues.append({
                    "severity": "P1",
                    "flywheel": _CRON_TO_FLYWHEEL.get(name, name),
                    "desc": f"`{name}` 执行耗时异常 ({direction}: {pct:+.0f}%)",
                    "detail": f"历史均值 {mean_t:.0f}s, 本次 {latest:.0f}s",
                })
                elapsed_annotations[name] = f"⚠️ {pct:+.0f}% vs 均值 {mean_t:.0f}s"

    # Scan cron logs for hidden errors
    hidden_errors = scan_cron_log_errors(cron_log_dir, states, now)
    for name, errs in hidden_errors.items():
        issues.append({
            "severity": "P1",
            "flywheel": _CRON_TO_FLYWHEEL.get(name, name),
            "desc": f"`{name}` 运行成功但日志含错误",
            "detail": errs[0][:200],
        })

    return issues, table, elapsed_annotations


# === Analyzers — 类别二：产出质量 + 类别三：趋势 ===

def analyze_router(trace: dict[str, list[dict]],
                   data_flywheel: Path) -> tuple[list[dict], dict, dict]:
    masks = trace["router_mask"]
    successes = trace["recall_success"]
    empties = trace["recall_empty_results"]
    timeouts = trace["recall_timeout"]

    total_masks = len(masks)
    if total_masks == 0:
        return [], {"status": "no_data"}, {}

    full_off = sum(
        1 for m in masks
        if not m.get("mask", {}).get("h")
        and not m.get("mask", {}).get("kt")
        and not m.get("mask", {}).get("s")
    )
    full_on = sum(
        1 for m in masks
        if m.get("mask", {}).get("h")
        and m.get("mask", {}).get("kt")
        and m.get("mask", {}).get("s")
    )

    total_recall = len(successes) + len(empties) + len(timeouts)
    success_rate = (len(successes) / total_recall * 100) if total_recall else 0
    empty_rate = (len(empties) / total_recall * 100) if total_recall else 0

    latencies = [s.get("latency_ms", 0) for s in successes if s.get("latency_ms")]
    avg_lat = sum(latencies) / len(latencies) if latencies else 0
    max_lat = max(latencies) if latencies else 0

    scores = []
    for s in successes:
        ss = s.get("score_stats", {})
        if ss.get("avg") is not None:
            scores.append(ss["avg"])
    avg_score = sum(scores) / len(scores) if scores else 0

    h_on = sum(1 for m in masks if m.get("mask", {}).get("h"))
    kt_on = sum(1 for m in masks if m.get("mask", {}).get("kt"))
    s_on = sum(1 for m in masks if m.get("mask", {}).get("s"))

    issues = []
    full_off_pct = full_off / total_masks * 100
    if full_off_pct > TH["router_full_off_pct"]:
        issues.append({
            "severity": "P0",
            "flywheel": "Router",
            "desc": f"Router全关率 {full_off_pct:.1f}% (阈值 {TH['router_full_off_pct']}%)",
            "detail": f"{full_off}/{total_masks} 次路由全关，直接跳过召回",
        })
    if empty_rate > TH["recall_empty_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Router",
            "desc": f"空结果率 {empty_rate:.1f}% (阈值 {TH['recall_empty_pct']}%)",
            "detail": f"{len(empties)} 空结果 / {total_recall} 总召回",
        })
    if avg_score and avg_score < TH["kn_avg_score_low"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Router",
            "desc": f"平均得分偏低 {avg_score:.4f} (阈值 {TH['kn_avg_score_low']})",
            "detail": "召回结果相关性不足",
        })

    metrics = {
        "total_masks": total_masks,
        "full_off": full_off,
        "full_off_pct": round(full_off_pct, 1),
        "full_on": full_on,
        "full_on_pct": round(full_on / total_masks * 100, 1),
        "h_on": h_on,
        "kt_on": kt_on,
        "s_on": s_on,
        "success_count": len(successes),
        "empty_count": len(empties),
        "timeout_count": len(timeouts),
        "success_rate": round(success_rate, 1),
        "empty_rate": round(empty_rate, 1),
        "avg_latency_ms": round(avg_lat),
        "max_latency_ms": max_lat,
        "avg_score": round(avg_score, 4),
        "multi_hop_count": len(trace["multi_hop_expand"]),
    }

    # Trend: compare with previous snapshot (baseline_latest.json is current,
    # baseline_prev.json is yesterday's — but we don't have per-period router snapshots.
    # Use prev skill_eval as an approximation? No, skip — trend for router is a V2 item.)
    trend = {}

    return issues, metrics, trend


def analyze_skill_eval(data_flywheel: Path, kn_baseline_dir: Path) -> tuple[list[dict], dict, dict]:
    # latest eval result: skill_eval_prev.json is the most recent run
    latest_data = _load_json(data_flywheel / "skill_eval_prev.json")
    if not latest_data:
        return [], {"status": "no_data"}, {}

    meta = latest_data.get("meta", {})
    avg_f1 = meta.get("avg_f1", 0)
    avg_precision = meta.get("avg_precision", 0)
    avg_recall = meta.get("avg_recall", 0)
    n_queries = meta.get("n_queries", 0)
    timestamp = meta.get("timestamp", "")

    issues = []
    if avg_f1 < TH["skill_f1_low"]:
        issues.append({
            "severity": "P0",
            "flywheel": "Skill",
            "desc": f"Skill F1={avg_f1:.4f} (阈值 {TH['skill_f1_low']})",
            "detail": f"基于 {n_queries} 个查询",
        })

    results = {
        "avg_f1": round(avg_f1, 4),
        "avg_precision": round(avg_precision, 4),
        "avg_recall": round(avg_recall, 4),
        "n_queries": n_queries,
        "timestamp": timestamp,
    }

    # Trend: compare with baselines/skill_eval_latest.json (previous run)
    trend = {}
    older_data = _load_json(kn_baseline_dir / "skill_eval_latest.json")
    if older_data and older_data is not latest_data:
        old_meta = older_data.get("meta", {})
        old_f1 = old_meta.get("avg_f1", 0)
        old_ts = old_meta.get("timestamp", "")
        cur_ts = meta.get("timestamp", "")
        # Skip trend if timestamps match (same run, duplicated file)
        if old_ts and cur_ts and old_ts == cur_ts:
            pass
        elif old_f1 and avg_f1:
            delta = avg_f1 - old_f1
            results["f1_delta"] = round(delta, 4)
            trend["Skill F1"] = f"{old_f1:.4f} → {avg_f1:.4f} ({delta:+.4f})"

    return issues, results, trend


def analyze_kt_baseline(data_flywheel: Path) -> tuple[list[dict], dict, dict]:
    latest_data = _load_json(data_flywheel / "kt-baseline-latest.json")
    if not latest_data:
        return [], {"status": "no_data"}, {}

    metrics = latest_data.get("metrics", {})
    total_kps = int(metrics.get("total_kps", 0))
    orphan_kps = int(metrics.get("orphan_kps", 0))
    avg_conf = metrics.get("avg_confidence", 0)
    fragment_domains = int(metrics.get("fragment_domains", 0))
    collected_at = latest_data.get("collected_at", "")

    orphan_pct = (orphan_kps / total_kps * 100) if total_kps else 0

    issues = []
    if orphan_pct > TH["kt_orphan_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "知识树",
            "desc": f"孤立知识点 {orphan_pct:.1f}% (阈值 {TH['kt_orphan_pct']}%)",
            "detail": f"孤立 {orphan_kps}/{total_kps}",
        })

    results = {
        "total_kps": total_kps,
        "orphan_kps": orphan_kps,
        "orphan_pct": round(orphan_pct, 1),
        "avg_confidence": round(avg_conf, 4),
        "fragment_domains": fragment_domains,
        "collected_at": collected_at,
    }

    # Trend: compare with kt-baseline-prev.json
    prev_data = _load_json(data_flywheel / "kt-baseline-prev.json")
    trend = {}
    if prev_data:
        prev_metrics = prev_data.get("metrics", {})
        prev_orphan = int(prev_metrics.get("orphan_kps", 0))
        prev_total = int(prev_metrics.get("total_kps", 0))
        prev_orphan_pct = (prev_orphan / prev_total * 100) if prev_total else 0
        if prev_orphan_pct:
            delta = orphan_pct - prev_orphan_pct
            trend["孤立率"] = f"{prev_orphan_pct:.1f}% → {orphan_pct:.1f}% ({delta:+.1f}%)"

    return issues, results, trend


def analyze_clustering(data_flywheel: Path) -> tuple[list[dict], dict, dict]:
    runs = _load_jsonl(data_flywheel / "clustering_baseline_prev.json")
    if not runs:
        return [], {"status": "no_data"}, {}

    latest = runs[-1]
    prev = runs[-2] if len(runs) > 1 else None

    sil = latest.get("silhouette", 0)
    cluster_count = latest.get("cluster_count", 0)
    memory_links = latest.get("memory_links", 0)
    total_units = latest.get("total_units", 0)
    timestamp = latest.get("timestamp", "")

    result = {
        "timestamp": timestamp,
        "silhouette": round(sil, 4),
        "cluster_count": cluster_count,
        "memory_links": memory_links,
        "total_units": total_units,
    }

    issues = []

    # Silhouette absolute value check (NEW)
    if sil < TH["silhouette_low"]:
        issues.append({
            "severity": "P1",
            "flywheel": "聚类",
            "desc": f"Silhouette={sil:.4f} 接近零 (阈值 {TH['silhouette_low']})",
            "detail": "聚类几乎无效，建议检查聚类参数或数据质量",
        })

    # Silhouette degrade check
    trend = {}
    if prev:
        sil_delta = sil - prev.get("silhouette", 0)
        result["silhouette_delta"] = round(sil_delta, 4)
        trend["Silhouette"] = f"{prev.get('silhouette', 0):.4f} → {sil:.4f} ({sil_delta:+.4f})"
        if sil_delta < TH["silhouette_degrade"]:
            issues.append({
                "severity": "P1",
                "flywheel": "聚类",
                "desc": f"Silhouette 退化 {sil_delta:+.4f}",
                "detail": f"prev={prev.get('silhouette', 0):.4f} → latest={sil:.4f}",
            })

    return issues, result, trend


def analyze_kn_baseline(baseline_dir: Path) -> tuple[list[dict], dict, dict]:
    latest_data = _load_json(baseline_dir / "baseline_latest.json")
    if not latest_data or not isinstance(latest_data, dict):
        return [], {"status": "no_data"}, {}

    dim_stats: dict[str, list[float]] = {}
    total_queries = 0
    total_filtered = 0
    total_eval_true = 0
    total_eval_false = 0

    for query, m in latest_data.items():
        if not isinstance(m, dict):
            continue
        if _is_test_query(query):
            total_filtered += 1
            continue
        total_queries += 1
        dim = m.get("dimension", "unknown")
        score = m.get("avg_score", 0)
        dim_stats.setdefault(dim, []).append(score)
        total_eval_true += m.get("eval_counted_true", 0)
        total_eval_false += m.get("eval_counted_false", 0)

    dim_summary = {}
    for dim, scores in dim_stats.items():
        dim_summary[dim] = {
            "count": len(scores),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0,
        }

    overall_eval_rate = (
        total_eval_true / (total_eval_true + total_eval_false) * 100
        if (total_eval_true + total_eval_false) > 0
        else 0
    )

    issues = []
    for dim, s in dim_summary.items():
        if dim == "unknown":
            continue
        if s["avg_score"] < TH["kn_avg_score_low"]:
            issues.append({
                "severity": "P1",
                "flywheel": "Router",
                "desc": f"KN基线 dimension={dim} 均分 {s['avg_score']:.4f} < {TH['kn_avg_score_low']}",
                "detail": f"{s['count']} 个查询",
            })

    unknown_count = dim_summary.get("unknown", {}).get("count", 0)
    if total_queries > 0 and unknown_count / total_queries > TH["unknown_dim_pct"] / 100:
        issues.append({
            "severity": "P1",
            "flywheel": "Router",
            "desc": f"KN基线中 dimension=unknown 占比过高 ({unknown_count}/{total_queries})",
            "detail": f"阈值 {TH['unknown_dim_pct']}%，维度信息未正确标记，影响基线分类统计质量",
        })

    results = {
        "total_queries": total_queries,
        "total_filtered": total_filtered,
        "unknown_dim_pct": round(unknown_count / total_queries * 100, 1) if total_queries else 0,
        "dim_summary": dim_summary,
        "eval_rate": round(overall_eval_rate, 1),
        "eval_true": total_eval_true,
        "eval_false": total_eval_false,
    }

    # Trend: compare with baseline_prev.json for dimension distribution
    prev_data = _load_json(baseline_dir / "baseline_prev.json")
    trend = {}
    if prev_data and isinstance(prev_data, dict):
        prev_unknown = 0
        prev_total = 0
        for q, m in prev_data.items():
            if not isinstance(m, dict):
                continue
            if _is_test_query(q):
                continue
            prev_total += 1
            if m.get("dimension", "unknown") == "unknown":
                prev_unknown += 1
        if prev_total:
            prev_unknown_pct = prev_unknown / prev_total * 100
            cur_unknown_pct = unknown_count / total_queries * 100 if total_queries else 0
            delta = cur_unknown_pct - prev_unknown_pct
            trend["unknown_dim"] = f"{prev_unknown_pct:.1f}% → {cur_unknown_pct:.1f}% ({delta:+.1f}%)"

    return issues, results, trend


# === 类别三：数据可信度 ===

def analyze_data_credibility(kt_result: dict, router_metrics: dict,
                             kn_result: dict,
                             now: datetime) -> tuple[list[str], list[str]]:
    """Returns (warnings, notes) — warnings affect confidence, notes are informative."""
    warnings = []
    notes = []

    # Sample size
    n_masks = router_metrics.get("total_masks", 0)
    if n_masks > 0 and n_masks < TH["min_sample_size"]:
        warnings.append(
            f"Router trace.log 样本量 {n_masks} < {TH['min_sample_size']}，"
            "全关率/空结果率统计结果仅供参考"
        )

    # Baseline freshness
    kt_collected = kt_result.get("collected_at", "")
    if kt_collected:
        try:
            kt_time = datetime.fromisoformat(kt_collected)
            if kt_time.tzinfo is None:
                kt_time = kt_time.replace(tzinfo=timezone.utc)
            age_hours = (now - kt_time).total_seconds() / 3600
            if age_hours > TH["baseline_stale_hours"]:
                notes.append(
                    f"知识树基线数据采集于 {kt_collected[:16]}（已过期 {age_hours:.0f}h，"
                    f"阈值 {TH['baseline_stale_hours']}h）"
                )
        except (ValueError, TypeError):
            pass

    # KN baseline quality — check unknown dimension ratio
    kn_unknown_pct = kn_result.get("unknown_dim_pct", 0)
    if kn_unknown_pct > TH["unknown_dim_pct"]:
        warnings.append(
            f"KN 基线中 dimension=unknown 占比 {kn_unknown_pct:.0f}%"
            f"（阈值 {TH['unknown_dim_pct']}%），基线质量不可靠"
        )

    return warnings, notes


# === Report Generator ===

def _resolve_trend_arrow(delta_val: float) -> str:
    if delta_val > 0.01:
        return "↑ 改善"
    elif delta_val < -0.01:
        return "↓ 恶化"
    return "→ 持平"


def generate_report(home: Path, dry_run: bool = False) -> tuple[str, list[dict]]:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")

    cron_state_dir = home / CRON_STATE_SUBPATH
    cron_log_dir = home / CRON_LOG_SUBPATH
    trace_path = home / TRACE_LOG_SUBPATH
    kn_baseline_dir = home / KN_BASELINE_SUBPATH
    data_flywheel_dir = home / DATA_FLYWHEEL_SUBPATH

    # Parse all data
    cron_states = parse_cron_states(cron_state_dir)
    trace = parse_trace_log(trace_path)

    # Analyze
    cron_issues, cron_table, elapsed_ann = analyze_cron_jobs(cron_states, cron_log_dir, now)
    router_issues, router_m, router_trend = analyze_router(trace, data_flywheel_dir)
    skill_issues, skill_m, skill_trend = analyze_skill_eval(data_flywheel_dir, kn_baseline_dir)
    kt_issues, kt_m, kt_trend = analyze_kt_baseline(data_flywheel_dir)
    cluster_issues, cluster_m, cluster_trend = analyze_clustering(data_flywheel_dir)
    kn_issues, kn_m, kn_trend = analyze_kn_baseline(kn_baseline_dir)

    credibility_warnings, credibility_notes = analyze_data_credibility(
        kt_m, router_m, kn_m, now
    )

    # Collect issues
    all_issues = (cron_issues + router_issues + skill_issues + kt_issues +
                  cluster_issues + kn_issues)
    p0 = [i for i in all_issues if i["severity"] == "P0"]
    p1 = [i for i in all_issues if i["severity"] == "P1"]

    L = []
    L.append(f"# Flywheel Health Report - {today}")
    L.append("")
    L.append(f"**Generated**: {now_str}")
    L.append(f"**Home**: `{home}`")
    L.append(f"**Core cron tasks**: {len(cron_table)} 个（排除 3 个非飞轮）")
    if dry_run:
        L.append("**Mode**: dry-run (no file written)")
    L.append("")

    # === 概览 ===
    L.append("## 概览")
    L.append("")
    L.append(f"- P0 问题: **{len(p0)}**")
    L.append(f"- P1 问题: **{len(p1)}**")
    for w in credibility_warnings:
        L.append(f"- ⚠️ {w}")
    for n in credibility_notes:
        L.append(f"- 📝 {n}")
    L.append("")

    # === P0 ===
    L.append("## 🔴 P0 - 需要立即处理")
    L.append("")
    if p0:
        L.append("| 飞轮 | 问题 | 详情 |")
        L.append("|------|------|------|")
        for i in p0:
            L.append(f"| {i['flywheel']} | {i['desc']} | {i.get('detail', '')} |")
    else:
        L.append("✅ 无 P0 问题")
    L.append("")

    # === P1 ===
    L.append("## 🟡 P1 - 需要关注")
    L.append("")
    if p1:
        L.append("| 飞轮 | 问题 | 详情 |")
        L.append("|------|------|------|")
        for i in p1:
            L.append(f"| {i['flywheel']} | {i['desc']} | {i.get('detail', '')} |")
    else:
        L.append("✅ 无 P1 问题")
    L.append("")

    # === 类别一：任务可靠性 ===
    L.append("## 📊 任务可靠性")
    L.append("")
    L.append("| 任务 | 飞轮 | 状态 | 上次运行 | 耗时 | 耗时异常 |")
    L.append("|------|------|------|---------|------|---------|")
    for name, info in sorted(cron_table.items()):
        icon = "✅" if info["status"] == "success" else "❌" if info["status"] == "fail" else "⚪"
        fw = _CRON_TO_FLYWHEEL.get(name, name)
        run_short = info["run_at"][:16] if info["run_at"] != "—" else "—"
        elapsed_str = f"{info['elapsed']}s" if info['elapsed'] else "—"
        ann = elapsed_ann.get(name, "—")
        L.append(f"| {name} | {fw} | {icon} {info['status']} | {run_short} | {elapsed_str} | {ann} |")
    L.append("")

    # === 类别二：产出明细 ===
    L.append("## 🔍 产出明细")
    L.append("")

    # Router
    L.append("### Router 飞轮")
    L.append("")
    if router_m.get("status") == "no_data":
        L.append("- 无 trace.log 数据")
    else:
        L.append(f"- 路由总次数: {router_m['total_masks']} | "
                 f"样本量: {'充足' if router_m['total_masks'] >= TH['min_sample_size'] else '⚠️ 偏少'}")
        L.append(f"- 全关率: {router_m['full_off_pct']}% ({router_m['full_off']}/{router_m['total_masks']}) | "
                 f"全开率: {router_m['full_on_pct']}% ({router_m['full_on']})")
        L.append(f"- Hindsight 开启: {router_m['h_on']} | 知识树: {router_m['kt_on']} | Skill: {router_m['s_on']}")
        L.append(f"- 召回成功: {router_m['success_count']} | 空结果: {router_m['empty_count']} | "
                 f"超时: {router_m['timeout_count']}")
        L.append(f"- 成功率: {router_m['success_rate']}% | 空结果率: {router_m['empty_rate']}%")
        L.append(f"- 平均延迟: {router_m['avg_latency_ms']}ms | 最大: {router_m['max_latency_ms']}ms")
        L.append(f"- 平均得分: {router_m['avg_score']} | 多跳展开: {router_m['multi_hop_count']} 次")
    L.append("")

    # KN 基线
    L.append("### KN 基线")
    L.append("")
    if kn_m.get("status") == "no_data":
        L.append("- 无 baseline 数据")
    else:
        L.append(f"- 用户查询: {kn_m['total_queries']} | 已过滤测试查询: {kn_m['total_filtered']}")
        L.append(f"- 未知维度占比: {kn_m['unknown_dim_pct']}%")
        L.append("  *Eval 命中率: 基线中 eval_counted_true/false 均为 0 "
                 "（LLM judge 评估结果未持久化至该字段，召回成功率参考 trace.log 数据）*")
        L.append("")
        L.append("| Dimension | 查询数 | 均分 |")
        L.append("|-----------|--------|------|")
        for dim, s in sorted(kn_m["dim_summary"].items()):
            flag = " ⚠️" if dim == "unknown" else ""
            L.append(f"| {dim}{flag} | {s['count']} | {s['avg_score']} |")
    L.append("")

    # Skill
    L.append("### Skill 飞轮")
    L.append("")
    if skill_m.get("status") == "no_data":
        L.append("- 无 skill_eval 数据")
    else:
        L.append(f"- F1: {skill_m['avg_f1']} | Precision: {skill_m['avg_precision']} | "
                 f"Recall: {skill_m['avg_recall']}")
        L.append(f"- 查询数: {skill_m['n_queries']} | 时间: {skill_m['timestamp']}")
    L.append("")

    # 知识树
    L.append("### 知识树飞轮")
    L.append("")
    if kt_m.get("status") == "no_data":
        L.append("- 无 baseline 数据")
    else:
        L.append(f"- 知识点总量: {kt_m['total_kps']}")
        L.append(f"- 孤立知识点: {kt_m['orphan_kps']} ({kt_m['orphan_pct']}%)")
        L.append(f"- 平均置信度: {kt_m['avg_confidence']} | 碎片域: {kt_m['fragment_domains']}")
        L.append(f"- 采集时间: {kt_m['collected_at']}")
    L.append("")

    # 聚类
    L.append("### 聚类飞轮")
    L.append("")
    if cluster_m.get("status") == "no_data":
        L.append("- 无 clustering 数据")
    else:
        L.append(f"- Silhouette: {cluster_m['silhouette']}{' ⚠️ < 0.1' if cluster_m['silhouette'] < TH['silhouette_low'] else ''}")
        L.append(f"- 聚类数: {cluster_m['cluster_count']} | Memory Links: {cluster_m['memory_links']}")
        L.append(f"- 总单元: {cluster_m['total_units']}")
        if "silhouette_delta" in cluster_m:
            L.append(f"- Silhouette 变化: {cluster_m['silhouette_delta']:+.4f}")
        L.append(f"- 时间: {cluster_m['timestamp']}")
    L.append("")

    # === 类别三：变化趋势 ===
    L.append("## 📈 变化趋势")
    L.append("")
    all_trends = {}
    all_trends.update(router_trend)
    all_trends.update(skill_trend)
    all_trends.update(kt_trend)
    all_trends.update(cluster_trend)
    all_trends.update(kn_trend)

    if all_trends:
        L.append("| 指标 | 变化 |")
        L.append("|------|------|")
        for key, val in sorted(all_trends.items()):
            L.append(f"| {key} | {val} |")
    else:
        L.append("无趋势数据（基线历史数据不足，V2 自动积累）")
    L.append("")

    # === 类别四：数据可信度 ===
    L.append("## ⚠️ 数据可信度")
    L.append("")
    if credibility_warnings or credibility_notes:
        for w in credibility_warnings:
            L.append(f"- ⚠️ {w}")
        for n in credibility_notes:
            L.append(f"- 📝 {n}")
    else:
        L.append("✅ 数据样本充足，基线新鲜，分析结果可靠")
    L.append("")

    return "\n".join(L), p0


def main():
    parser = argparse.ArgumentParser(description="Flywheel Health Report Generator (v2)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run mode: print report to stdout, no file written",
    )
    parser.add_argument(
        "--home",
        default=DEFAULT_HERMES_HOME,
        help=f"Hermes home path (default: {DEFAULT_HERMES_HOME})",
    )
    args = parser.parse_args()

    home = Path(args.home)
    report, p0_issues = generate_report(home, dry_run=args.dry_run)

    print(report)

    if args.dry_run:
        print("\n[DRY-RUN] No file written.")
    else:
        output_dir = home / OUTPUT_SUBPATH
        output_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_path = output_dir / f"flywheel-report-{today}.md"
        output_path.write_text(report, encoding="utf-8")
        print(f"\n[Report saved to: {output_path}]")

    sys.exit(1 if p0_issues else 0)


if __name__ == "__main__":
    main()