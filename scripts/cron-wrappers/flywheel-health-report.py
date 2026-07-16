#!/usr/bin/env python3
"""Flywheel Health Report Generator (v2).

Reads cron-state, trace.log, logs/cron/, and baseline files to produce a unified
Markdown health report organized by 10 analysis modules:
  1) 任务可靠性 (analyze_cron_jobs)     - are cron jobs running stably?
  2) Router 召回 (analyze_router)       - mask 决策 / 成功率 / 错误率 / KT 降级率
  3) Skill 评测 (analyze_skill_eval)    - F1/Precision/Recall
  4) Skill 真实使用 (analyze_skill_usage) - active/used/never_used/stale
  5) Token 预算 (analyze_token_budget)  - 分源消耗 hs/kt/skill + 耗尽率
  6) SAG 贡献 (analyze_sag_contribution) - recall/merge 零结果率
  7) 全局错误 (analyze_global_errors)   - ERROR/WARNING 占比 + top 模块
  8) KN 基线 (analyze_kn_baseline)      - per-dimension source contribution
  9) 聚类 (analyze_clustering)          - 噪声率
  10) 知识树 (analyze_kt_baseline)      - 孤立知识点
  11) 记忆清理 (analyze_memory_cleanup) - 字符占用率 / 清理产出 / 运行状态
  外加：完整性检查 / 依赖链 / 僵尸文件 / 数据可信度

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
from collections import Counter, defaultdict
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
SKILL_USAGE_SUBPATH = Path("skills") / ".usage.json"
ERROR_LOG_SUBPATH = Path("logs") / "errors.log"
MEMORY_DIR_SUBPATH = Path("memories")

# === Thresholds ===
TH = {
    # 产出质量
    "router_full_off_pct": 30,     # >30% -> P0
    "recall_empty_pct": 20,        # >20% -> P1
    "skill_f1_low": 0.4,           # <0.4 -> P0
    "kn_avg_score_low": 0.5,       # <0.5 per dimension -> P1
    "kt_orphan_pct": 90,           # >90% -> P1
    "cluster_noise_rate_high": 50, # >50% -> P1
    "unknown_dim_pct": 50,         # >50% -> P1
    # 任务可靠性
    "elapsed_deviation_sigma": 2.0, # >2 sigma from mean -> P1
    # 数据可信度 (标注不报警)
    "baseline_stale_hours": 48,
    "min_sample_size": 50,
    # Skill 真实使用
    "skill_unused_warn_days": 30,
    "skill_unused_warn_count": 20,
    # Token 预算
    "token_budget_exhaust_pct": 10,
    # 全局错误
    "error_rate_high_pct": 5,
    # SAG 贡献
    "sag_merge_zero_pct": 50,
    # 记忆清理
    "memory_char_usage_high_pct": 90,
    "memory_cleanup_stale_hours": 48,
}

# === Test query filter ===
_TEST_QUERY_RE = re.compile(
    r"^(gen_|eval-|test_|test-|exact_kw_|semantic_|entity_|causal_|"
    r"temporal_|conflict_|tool_|debug_|api_|compare_|workflow_|complex_|numeric_)",
    re.IGNORECASE,
)

# === Active cron jobs — only core flywheel tasks ===
# Excluded: system-health-check (环境巡检),
#           cron-boot-detect / cron-periodic-detect (自愈框架),
#           flywheel-health-report (报告自身),
#           cron-periodic-dedup (去重字典，非 state 文件)
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

# 已知的非飞轮 state 文件白名单（自愈框架/巡检/报告自身/去重字典）
# 这些文件每天会重新生成，删除无意义，在报告中显式排除以避免噪音
EXCLUDED_STATE_FILES = frozenset({
    "system-health-check",
    "cron-boot-detect",
    "cron-periodic-detect",
    "cron-periodic-dedup",
    "flywheel-health-report",
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

# === Required output files for integrity check ===
REQUIRED_OUTPUTS = {
    "Skill": Path("data") / "flywheel" / "skill_eval_prev.json",
    "知识树": Path("data") / "flywheel" / "kt-baseline-latest.json",
    "聚类": Path("data") / "flywheel" / "clustering_baseline_prev.json",
    "Router": Path("plugins") / "knowledge-navigation" / "baselines" / "baseline_latest.json",
}

# === Flywheel dependency chain (downstream -> upstream) ===
FLYWHEEL_DEPENDENCIES = {
    "skillopt-nightly-run": ["run-skill-eval"],
}


def _is_test_query(key: str) -> bool:
    return bool(_TEST_QUERY_RE.match(key))


# === Parsers ===

def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save_json(path: Path, data: dict) -> None:
    """Write JSON to file, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _rotate_jsonl(path: Path, keep: int = 30) -> None:
    """Trim JSONL file to last N records."""
    records = _load_jsonl(path)
    if len(records) <= keep:
        return
    records = records[-keep:]
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_daily_summary(data_flywheel: Path, summary: dict) -> None:
    """Append daily summary to history JSONL, dedup by date, keep last 30 days."""
    path = data_flywheel / "daily-summary-history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    # 同日替换而非追加，避免趋势表重复
    records = _load_jsonl(path)
    date = summary.get("date")
    records = [r for r in records if r.get("date") != date]
    records.append(summary)
    records = records[-30:]
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_daily_summary(data_flywheel: Path) -> list[dict]:
    """Load daily summary history."""
    path = data_flywheel / "daily-summary-history.jsonl"
    return _load_jsonl(path)


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


def parse_trace_log(trace_path: Path,
                    filter_date: str | None = None) -> dict[str, list[dict]]:
    """Parse trace.log, optionally filtered to entries matching filter_date (e.g. '2026-07-07').

    When filter_date is provided, only entries whose timestamp starts with that date
    are included. This ensures each daily report reflects only the current day's data,
    not cumulative history.
    """
    events: dict[str, list[dict]] = {
        "router_mask": [],
        "recall_success": [],
        "recall_empty": [],
        "recall_empty_results": [],
        "recall_error": [],
        "hindsight_fail_kt_fallback": [],
        "multi_hop_expand": [],
        "recall_timeout": [],
        # SAG 召回统一使用 recall_sag（来自 RecallLogger 框架，字段更完整）
        # hooks.py 中 _do_sag_recall 内部的 sag_recall 事件为冗余日志，不再消费
        "recall_sag": [],
        "token_budget": [],
        "sag_merge": [],
    }
    if not trace_path.is_file():
        return events
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if filter_date:
                ts = d.get("timestamp", "")
                if not ts.startswith(filter_date):
                    continue
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
            # Skip success indicators like "errors=0" (not actual errors)
            if "ERRORS=0" in upper:
                continue
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

def _percentile(values: list[float], p: float) -> float:
    """计算百分位数（线性插值）。"""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def analyze_router(trace: dict[str, list[dict]],
                   data_flywheel: Path) -> tuple[list[dict], dict, dict]:
    masks = trace["router_mask"]
    successes = trace["recall_success"]
    empties = trace["recall_empty_results"]
    timeouts = trace["recall_timeout"]
    errors = trace["recall_error"]
    kt_fallbacks = trace["hindsight_fail_kt_fallback"]
    sag_recalls = trace["recall_sag"]

    total_masks = len(masks)
    if total_masks == 0:
        return [], {"status": "no_data"}, {}

    full_off = sum(
        1 for m in masks
        if not m.get("mask", {}).get("h")
        and not m.get("mask", {}).get("kt")
        and not m.get("mask", {}).get("s")
        and not m.get("mask", {}).get("sag")
    )
    full_on = sum(
        1 for m in masks
        if m.get("mask", {}).get("h")
        and m.get("mask", {}).get("kt")
        and m.get("mask", {}).get("s")
        and m.get("mask", {}).get("sag")
    )

    total_recall = len(successes) + len(empties) + len(timeouts) + len(errors)
    success_rate = (len(successes) / total_recall * 100) if total_recall else 0
    empty_rate = (len(empties) / total_recall * 100) if total_recall else 0
    error_rate = (len(errors) / total_recall * 100) if total_recall else 0
    kt_fallback_rate = (len(kt_fallbacks) / total_recall * 100) if total_recall else 0

    latencies = [s.get("latency_ms", 0) for s in successes if s.get("latency_ms")]
    avg_lat = sum(latencies) / len(latencies) if latencies else 0
    max_lat = max(latencies) if latencies else 0
    p50_lat = _percentile(latencies, 0.50)
    p95_lat = _percentile(latencies, 0.95)
    p99_lat = _percentile(latencies, 0.99)

    scores = []
    for s in successes:
        ss = s.get("score_stats", {})
        if ss.get("avg") is not None:
            scores.append(ss["avg"])
    avg_score = sum(scores) / len(scores) if scores else 0

    h_on = sum(1 for m in masks if m.get("mask", {}).get("h"))
    kt_on = sum(1 for m in masks if m.get("mask", {}).get("kt"))
    s_on = sum(1 for m in masks if m.get("mask", {}).get("s"))
    sag_on = sum(1 for m in masks if m.get("mask", {}).get("sag"))

    # SAG 专项统计（recall_sag 含 error 场景，需区分）
    sag_recall_attempts = len(sag_recalls)
    sag_error_count = sum(1 for r in sag_recalls if r.get("error"))
    sag_non_empty = sum(1 for r in sag_recalls if r.get("count", 0) > 0 and not r.get("error"))
    sag_total_kept = sum(int(s.get("sag_kept", 0)) for s in successes)
    # 延迟统计包含所有召回尝试（含 error），反映 SAG 服务实际响应时间
    sag_latencies = [r.get("latency_ms", 0) for r in sag_recalls if r.get("latency_ms") is not None]
    sag_avg_lat = sum(sag_latencies) / len(sag_latencies) if sag_latencies else 0
    sag_p50 = _percentile(sag_latencies, 0.50)
    sag_p95 = _percentile(sag_latencies, 0.95)

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
    # 召回错误率（recall_error 事件）异常升高
    if error_rate > TH["error_rate_high_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Router",
            "desc": f"召回错误率 {error_rate:.1f}% (阈值 {TH['error_rate_high_pct']}%)",
            "detail": f"{len(errors)} 次召回异常 / {total_recall} 总召回；可能为 Hindsight/上游 API 异常",
        })
    # Hindsight 失败降级 KT-only 频率过高
    if kt_fallback_rate > TH["recall_empty_pct"]:
        issues.append({
            "severity": "P2",
            "flywheel": "Router",
            "desc": f"Hindsight 失败降级 KT-only 率 {kt_fallback_rate:.1f}%",
            "detail": f"{len(kt_fallbacks)} 次 fallback / {total_recall} 总召回；Hindsight 稳定性待提升",
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
        "sag_on": sag_on,
        "sag_on_pct": round(sag_on / total_masks * 100, 1) if total_masks else 0,
        "success_count": len(successes),
        "empty_count": len(empties),
        "timeout_count": len(timeouts),
        "error_count": len(errors),
        "kt_fallback_count": len(kt_fallbacks),
        "success_rate": round(success_rate, 1),
        "empty_rate": round(empty_rate, 1),
        "error_rate": round(error_rate, 1),
        "kt_fallback_rate": round(kt_fallback_rate, 1),
        "avg_latency_ms": round(avg_lat),
        "max_latency_ms": max_lat,
        "p50_latency_ms": round(p50_lat),
        "p95_latency_ms": round(p95_lat),
        "p99_latency_ms": round(p99_lat),
        "avg_score": round(avg_score, 4),
        "multi_hop_count": len(trace["multi_hop_expand"]),
        "sag_recall_count": sag_recall_attempts,
        "sag_error_count": sag_error_count,
        "sag_non_empty_count": sag_non_empty,
        "sag_total_kept": sag_total_kept,
        "sag_avg_latency_ms": round(sag_avg_lat),
        "sag_p50_latency_ms": round(sag_p50),
        "sag_p95_latency_ms": round(sag_p95),
    }

    # === Trend: compare with router_prev.json ===
    trend = {}
    prev_router = _load_json(data_flywheel / "router_prev.json")
    if prev_router and isinstance(prev_router, dict):
        prev_full_off = prev_router.get("full_off_pct")
        prev_empty = prev_router.get("empty_pct")
        prev_latency = prev_router.get("avg_latency_ms")
        prev_sag_on = prev_router.get("sag_on_pct")
        prev_sag_kept = prev_router.get("sag_total_kept")
        if prev_full_off is not None:
            delta = full_off_pct - prev_full_off
            trend["Router 全关率"] = f"{prev_full_off:.1f}% → {full_off_pct:.1f}% ({delta:+.1f}%)"
        if prev_empty is not None:
            delta = empty_rate - prev_empty
            trend["Router 空结果率"] = f"{prev_empty:.1f}% → {empty_rate:.1f}% ({delta:+.1f}%)"
        if prev_latency is not None:
            delta = avg_lat - prev_latency
            trend["Router 平均延迟"] = f"{prev_latency:.0f}ms → {avg_lat:.0f}ms ({delta:+.0f}ms)"
        if prev_sag_on is not None:
            delta = metrics["sag_on_pct"] - prev_sag_on
            trend["SAG 开启率"] = f"{prev_sag_on:.1f}% → {metrics['sag_on_pct']:.1f}% ({delta:+.1f}%)"
        if prev_sag_kept is not None and prev_sag_kept > 0:
            delta = sag_total_kept - prev_sag_kept
            trend["SAG 召回量"] = f"{prev_sag_kept} → {sag_total_kept} ({delta:+d})"

    # Save current snapshot for next comparison
    _save_json(data_flywheel / "router_prev.json", {
        "full_off_pct": round(full_off_pct, 1),
        "empty_pct": round(empty_rate, 1),
        "avg_latency_ms": round(avg_lat),
        "sag_on_pct": metrics["sag_on_pct"],
        "sag_total_kept": sag_total_kept,
        "sag_avg_latency_ms": round(sag_avg_lat),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return issues, metrics, trend


def analyze_skill_eval(data_flywheel: Path, kn_baseline_dir: Path) -> tuple[list[dict], dict, dict]:
    # NOTE: upstream naming is counterintuitive:
    #   skill_eval_prev.json  = most recent run (latest)
    #   skill_eval_latest.json = previous run (older)
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


def analyze_skill_usage(skill_usage_path: Path, now: datetime) -> tuple[list[dict], dict, dict]:
    """Analyze real skill usage from .usage.json."""
    data = _load_json(skill_usage_path)
    if not data:
        return [], {"status": "no_data"}, {}

    all_skills = list(data.values())
    skill_names = list(data.keys())
    active = [s for s in all_skills if s.get("state") == "active"]
    archived = [s for s in all_skills if s.get("state") == "archived"]
    used = [s for s in active if (s.get("use_count") or 0) > 0]
    never_used = [s for s in active if (s.get("use_count") or 0) == 0]

    total_use = sum(s.get("use_count", 0) or 0 for s in active)
    total_view = sum(s.get("view_count", 0) or 0 for s in active)

    cutoff = now - timedelta(days=TH["skill_unused_warn_days"])
    stale = []
    for s in active:
        lu = s.get("last_used_at")
        if lu:
            try:
                dt = datetime.fromisoformat(lu.replace("Z", "+00:00"))
                if dt < cutoff and (s.get("use_count", 0) or 0) > 0:
                    stale.append(s)
            except (ValueError, TypeError):
                pass

    top_used = sorted(
        [(name, s) for name, s in zip(skill_names, all_skills) if (s.get("use_count", 0) or 0) > 0],
        key=lambda x: x[1].get("use_count", 0) or 0,
        reverse=True,
    )[:10]
    top_list = [{"name": n, "use_count": s.get("use_count", 0) or 0,
                 "view_count": s.get("view_count", 0) or 0,
                 "last_used_at": s.get("last_used_at", "")[:10] if s.get("last_used_at") else ""}
                for n, s in top_used]

    recent_7d = []
    cutoff_7d = now - timedelta(days=7)
    for name, s in zip(skill_names, all_skills):
        lu = s.get("last_used_at")
        if lu and (s.get("use_count", 0) or 0) > 0:
            try:
                dt = datetime.fromisoformat(lu.replace("Z", "+00:00"))
                if dt >= cutoff_7d:
                    recent_7d.append({"name": name, "use_count": s.get("use_count", 0) or 0,
                                       "last_used_at": lu[:10]})
            except (ValueError, TypeError):
                pass
    recent_7d.sort(key=lambda x: x["last_used_at"], reverse=True)

    issues = []
    if len(never_used) > TH["skill_unused_warn_count"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Skill",
            "desc": f"{len(never_used)} 个 active skill 从未被使用（阈值 {TH['skill_unused_warn_count']}）",
            "detail": f"共 {len(active)} 个 active，使用过 {len(used)} 个",
        })
    if len(stale) > TH["skill_unused_warn_count"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Skill",
            "desc": f"{len(stale)} 个 skill 超过 {TH['skill_unused_warn_days']} 天未使用",
            "detail": "可能已过时，建议 review 归档",
        })

    results = {
        "total_skills": len(all_skills),
        "active_count": len(active),
        "archived_count": len(archived),
        "used_count": len(used),
        "never_used_count": len(never_used),
        "stale_count": len(stale),
        "total_uses": total_use,
        "total_views": total_view,
        "top_used": top_list,
        "recent_7d": recent_7d[:10],
    }

    trend = {}
    return issues, results, trend


def analyze_token_budget(trace: dict) -> tuple[list[dict], dict, dict]:
    """Analyze token budget usage from trace.log token_budget events."""
    events = trace.get("token_budget", [])
    if not events:
        return [], {"status": "no_data"}, {}

    total_budget = events[0].get("total_budget", 4000)
    n = len(events)

    hs_used_list = []
    kt_used_list = []
    skill_used_list = []
    total_used_list = []
    exhaust_count = 0

    for e in events:
        hs_before = e.get("hs_tokens_before", 0) or 0
        hs_after = e.get("hs_tokens_after", 0) or 0
        kt_before = e.get("kt_tokens_before", 0) or 0
        kt_after = e.get("kt_tokens_after", 0) or 0
        skill_before = e.get("skill_tokens_before", 0) or 0
        skill_after = e.get("skill_tokens_after", 0) or 0
        total_after = hs_after + kt_after + skill_after
        total_used = total_budget - total_after if total_budget else 0
        hs_used_list.append(hs_before - hs_after if hs_before > hs_after else 0)
        kt_used_list.append(kt_before - kt_after if kt_before > kt_after else 0)
        skill_used_list.append(skill_before - skill_after if skill_before > skill_after else 0)
        total_used_list.append(total_used)
        if total_after <= 0 or (total_budget and total_used / total_budget > 0.95):
            exhaust_count += 1

    def _stats(lst):
        if not lst:
            return {"avg": 0, "max": 0, "p50": 0, "p90": 0}
        return {
            "avg": round(sum(lst) / len(lst)),
            "max": max(lst),
            "p50": round(_percentile(lst, 0.50)),
            "p90": round(_percentile(lst, 0.90)),
        }

    hs_stats = _stats(hs_used_list)
    kt_stats = _stats(kt_used_list)
    skill_stats = _stats(skill_used_list)
    total_stats = _stats(total_used_list)
    exhaust_pct = round(exhaust_count / n * 100, 1)

    issues = []
    if exhaust_pct > TH["token_budget_exhaust_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Router",
            "desc": f"Token 预算耗尽率 {exhaust_pct}%（阈值 {TH['token_budget_exhaust_pct']}%）",
            "detail": f"{exhaust_count}/{n} 次调用接近耗尽，可能导致召回截断",
        })

    results = {
        "total_budget": total_budget,
        "event_count": n,
        "exhaust_count": exhaust_count,
        "exhaust_pct": exhaust_pct,
        "hs_stats": hs_stats,
        "kt_stats": kt_stats,
        "skill_stats": skill_stats,
        "total_stats": total_stats,
    }
    trend = {}
    return issues, results, trend


def analyze_sag_contribution(trace: dict) -> tuple[list[dict], dict, dict]:
    """Analyze SAG recall contribution from recall_sag + sag_merge events.

    使用 RecallLogger 框架产生的 recall_sag 事件（字段：source/count/latency_ms/score_stats/error），
    而非 hooks.py _do_sag_recall 内部直接记录的 sag_recall 事件（后者为冗余日志）。

    注意：recall_sag 事件在 SAG 异常时也会记录（count=0, error=...），需与成功召回区分：
    - recall_error_count: SAG 召回异常次数（单独统计，不计入 recall_zero）
    - recall_zero: 成功召回但返回 0 section 的次数
    """
    recall_events = trace.get("recall_sag", [])
    merge_events = trace.get("sag_merge", [])

    if not recall_events and not merge_events:
        return [], {"status": "no_data"}, {}

    # 区分成功召回与异常召回：error 场景 count=0 但不应计入 recall_zero
    recall_success_events = [e for e in recall_events if not e.get("error")]
    recall_error_count = len(recall_events) - len(recall_success_events)
    recall_counts = [e.get("count", 0) or 0 for e in recall_success_events]
    merge_counts = [e.get("count", 0) or 0 for e in merge_events]

    recall_zero = sum(1 for c in recall_counts if c == 0)
    merge_zero = sum(1 for c in merge_counts if c == 0)

    def _stats(lst):
        if not lst:
            return {"avg": 0, "max": 0, "total": 0, "non_zero": 0}
        return {
            "avg": round(sum(lst) / len(lst), 1),
            "max": max(lst),
            "total": sum(lst),
            "non_zero": sum(1 for c in lst if c > 0),
        }

    recall_stats = _stats(recall_counts)
    merge_stats = _stats(merge_counts)
    merge_zero_pct = round(merge_zero / len(merge_counts) * 100, 1) if merge_counts else 0

    issues = []
    # recall_zero 判断只基于成功召回事件（排除 error 场景）
    if recall_success_events and recall_zero == len(recall_success_events):
        issues.append({
            "severity": "P1",
            "flywheel": "SAG",
            "desc": f"SAG 全部召回为 0 section（{len(recall_success_events)} 次成功召回）",
            "detail": "可能 SAG 索引为空或搜索条件过严",
        })
    elif merge_events and merge_zero_pct > TH["sag_merge_zero_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "SAG",
            "desc": f"SAG 合并零结果率 {merge_zero_pct}%（阈值 {TH['sag_merge_zero_pct']}%）",
            "detail": f"{merge_zero}/{len(merge_counts)} 次合并为 0，SAG 召回质量可能偏低",
        })
    if recall_error_count > 0:
        issues.append({
            "severity": "P1",
            "flywheel": "SAG",
            "desc": f"SAG 召回异常 {recall_error_count} 次",
            "detail": "SAG 服务可能不稳定，已触发熔断器或网络异常",
        })

    results = {
        "recall_count": len(recall_events),
        "recall_success_count": len(recall_success_events),
        "recall_error_count": recall_error_count,
        "merge_count": len(merge_events),
        "recall_zero": recall_zero,
        "merge_zero": merge_zero,
        "merge_zero_pct": merge_zero_pct,
        "recall_stats": recall_stats,
        "merge_stats": merge_stats,
    }
    trend = {}
    return issues, results, trend


def analyze_global_errors(error_log_path: Path, filter_date: str) -> tuple[list[dict], dict, dict]:
    """Analyze global errors from logs/errors.log."""
    if not error_log_path.exists():
        return [], {"status": "no_data"}, {}

    levels: Counter = Counter()
    modules: Counter = Counter()
    error_keywords: Counter = Counter()
    total_lines = 0
    date_lines = 0

    line_re = re.compile(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}[,\d]* (\w+) ([\w\.]+):")

    with open(error_log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            m = line_re.match(line)
            if m:
                date = m.group(1)
                level = m.group(2)
                module = m.group(3)
                if date == filter_date:
                    date_lines += 1
                    levels[level] += 1
                    modules[module] += 1
                    low = line.lower()
                    for kw in ["error", "exception", "traceback", "failed",
                               "timeout", "denied", "connection", "not found"]:
                        if kw in low:
                            error_keywords[kw] += 1

    if date_lines == 0:
        return [], {"status": "no_data", "total_in_log": total_lines}, {}

    error_count = levels.get("ERROR", 0)
    warning_count = levels.get("WARNING", 0)
    total_issue = error_count + warning_count
    error_pct = round(error_count / total_issue * 100, 1) if total_issue else 0

    top_modules = [{"module": m, "count": c} for m, c in modules.most_common(10)]
    top_keywords = [{"keyword": k, "count": c} for k, c in error_keywords.most_common()]

    issues = []
    if error_count > 0:
        if error_pct > TH["error_rate_high_pct"]:
            issues.append({
                "severity": "P1",
                "flywheel": "系统",
                "desc": f"全局 ERROR 占比 {error_pct}%（阈值 {TH['error_rate_high_pct']}%）",
                "detail": f"当日 {error_count} 条 ERROR / {total_issue} 条问题日志",
            })
        else:
            issues.append({
                "severity": "P2",
                "flywheel": "系统",
                "desc": f"当日有 {error_count} 条 ERROR 日志",
                "detail": "建议关注 top 模块的错误趋势",
            })

    results = {
        "total_logs": total_lines,
        "date_logs": date_lines,
        "error_count": error_count,
        "warning_count": warning_count,
        "error_pct": error_pct,
        "top_modules": top_modules,
        "top_keywords": top_keywords,
    }
    trend = {}
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


def analyze_memory_cleanup(memory_dir: Path,
                           data_window: str) -> tuple[list[dict], dict, dict]:
    """Analyze memory cleanup results from cleanup-report-*.json files.

    监控维度：
    - 清理执行状态（是否每日运行）
    - MEMORY.md / USER.md 字符占用率（是否接近上限）
    - 清理产出：压缩/迁移 hindsight/删除 数量
    - Phase 2 验证通过率
    """
    if not memory_dir.is_dir():
        return [], {"status": "no_data"}, {}

    date_str = data_window.replace("-", "")
    report_files = sorted(memory_dir.glob(f"cleanup-report-{date_str}_*.json"))
    date_matched = bool(report_files)
    if not report_files:
        report_files = sorted(memory_dir.glob("cleanup-report-*.json"))
    if not report_files:
        return [], {"status": "no_data"}, {}

    latest_report = _load_json(report_files[-1])
    if not latest_report:
        return [], {"status": "no_data"}, {}

    issues = []

    ts_str = latest_report.get("timestamp", "")
    try:
        report_dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S%z")
        report_hours_ago = (datetime.now(timezone.utc) - report_dt).total_seconds() / 3600
    except (ValueError, TypeError):
        report_hours_ago = 999

    if report_hours_ago > TH["memory_cleanup_stale_hours"]:
        issues.append({
            "severity": "P1",
            "flywheel": "记忆",
            "desc": f"记忆清理已 {report_hours_ago:.0f} 小时未运行",
            "detail": f"最近一次: {ts_str or '未知'}，阈值 {TH['memory_cleanup_stale_hours']}h",
        })

    mem_src = latest_report.get("sources", {}).get("MEMORY.md", {})
    user_src = latest_report.get("sources", {}).get("USER.md", {})

    mem_after = mem_src.get("after_cleanup", {})
    user_after = user_src.get("after_cleanup", {})

    mem_keep_chars = mem_after.get("keep_chars", 0)
    user_keep_chars = user_after.get("keep_chars", 0)
    # 从报告中读取 char_limit（新报告含此字段），兜底用默认值
    MEM_LIMIT = mem_src.get("char_limit", 50000)
    USER_LIMIT = user_src.get("char_limit", 15000)
    mem_usage_pct = round(mem_keep_chars / MEM_LIMIT * 100, 1) if MEM_LIMIT else 0
    user_usage_pct = round(user_keep_chars / USER_LIMIT * 100, 1) if USER_LIMIT else 0

    if mem_usage_pct > TH["memory_char_usage_high_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "记忆",
            "desc": f"MEMORY.md 字符占用 {mem_usage_pct}%（阈值 {TH['memory_char_usage_high_pct']}%）",
            "detail": f"{mem_keep_chars:,}/{MEM_LIMIT:,} chars，接近上限需关注",
        })
    if user_usage_pct > TH["memory_char_usage_high_pct"]:
        issues.append({
            "severity": "P1",
            "flywheel": "记忆",
            "desc": f"USER.md 字符占用 {user_usage_pct}%（阈值 {TH['memory_char_usage_high_pct']}%）",
            "detail": f"{user_keep_chars:,}/{USER_LIMIT:,} chars，接近上限需关注",
        })

    total_compress = mem_src.get("phase1_compress", 0) + user_src.get("phase1_compress", 0)
    total_hindsight = mem_src.get("phase1_hindsight", 0) + user_src.get("phase1_hindsight", 0)
    total_remove = mem_src.get("phase1_remove", 0) + user_src.get("phase1_remove", 0)
    total_merge = mem_src.get("phase1_merge", 0) + user_src.get("phase1_merge", 0)

    v2_mem = mem_src.get("phase2", {})
    v2_user = user_src.get("phase2", {})
    v2_total = (v2_mem.get("correct", 0) + v2_mem.get("corrected", 0) + v2_mem.get("keep", 0) +
                v2_user.get("correct", 0) + v2_user.get("corrected", 0) + v2_user.get("keep", 0))
    v2_correct = v2_mem.get("correct", 0) + v2_user.get("correct", 0)
    v2_correct_rate = round(v2_correct / v2_total * 100, 1) if v2_total else 0

    results = {
        "mode": latest_report.get("mode", "unknown"),
        "report_age_hours": round(report_hours_ago, 1),
        "date_matched": date_matched,
        "memory_chars": mem_keep_chars,
        "memory_limit": MEM_LIMIT,
        "memory_usage_pct": mem_usage_pct,
        "user_chars": user_keep_chars,
        "user_limit": USER_LIMIT,
        "user_usage_pct": user_usage_pct,
        "total_compress": total_compress,
        "total_hindsight": total_hindsight,
        "total_remove": total_remove,
        "total_merge": total_merge,
        "v2_correct_rate": v2_correct_rate,
        "tokens_total": (latest_report.get("tokens", {}).get("prompt", 0) +
                         latest_report.get("tokens", {}).get("completion", 0)),
        "elapsed_s": latest_report.get("total_time_s", 0),
    }

    # 趋势：与 memory_prev.json 对比
    trend = {}
    prev_memory = _load_json(memory_dir / "memory_prev.json")
    if prev_memory and isinstance(prev_memory, dict):
        prev_mem_pct = prev_memory.get("memory_usage_pct")
        prev_user_pct = prev_memory.get("user_usage_pct")
        if prev_mem_pct is not None:
            delta = mem_usage_pct - prev_mem_pct
            trend["MEMORY占用率"] = f"{prev_mem_pct:.1f}% → {mem_usage_pct:.1f}% ({delta:+.1f}%)"
        if prev_user_pct is not None:
            delta = user_usage_pct - prev_user_pct
            trend["USER占用率"] = f"{prev_user_pct:.1f}% → {user_usage_pct:.1f}% ({delta:+.1f}%)"

    # 保存当前快照供下次对比
    _save_json(memory_dir / "memory_prev.json", {
        "memory_usage_pct": mem_usage_pct,
        "user_usage_pct": user_usage_pct,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return issues, results, trend


def analyze_clustering(data_flywheel: Path) -> tuple[list[dict], dict, dict]:
    runs = _load_jsonl(data_flywheel / "clustering_baseline_prev.json")
    if not runs:
        return [], {"status": "no_data"}, {}

    latest = runs[-1]
    prev = runs[-2] if len(runs) > 1 else None

    noise_pct = latest.get("noise_units", 0)
    total = latest.get("processed_units", 0)
    noise_rate = (noise_pct / total * 100) if total else 0
    cluster_count = latest.get("cluster_count", 0)
    memory_links = latest.get("memory_links", 0)
    total_units = latest.get("total_units", 0)
    timestamp = latest.get("timestamp", "")

    result = {
        "timestamp": timestamp,
        "noise_rate": round(noise_rate, 1),
        "cluster_count": cluster_count,
        "memory_links": memory_links,
        "total_units": total_units,
    }

    issues = []

    # Noise rate check
    if noise_rate > TH["cluster_noise_rate_high"]:
        issues.append({
            "severity": "P1",
            "flywheel": "聚类",
            "desc": f"噪声率 {noise_rate:.1f}% (阈值 {TH['cluster_noise_rate_high']}%)",
            "detail": "大部分数据被归为噪声，聚类效果有限",
        })

    # Noise rate trend — 用前 3 次滚动均值作基线，避免单次离群值误判趋势
    trend = {}
    WINDOW = 3
    window = runs[-(WINDOW + 1):-1] if len(runs) > WINDOW else (runs[:-1] if len(runs) > 1 else [])
    if window:
        window_rates = []
        for r in window:
            p_n = r.get("noise_units", 0)
            p_t = r.get("processed_units", 0)
            if p_t > 0:
                window_rates.append(p_n / p_t * 100)
        if window_rates:
            baseline_rate = sum(window_rates) / len(window_rates)
            delta = noise_rate - baseline_rate
            result["noise_rate_delta"] = round(delta, 1)
            result["noise_rate_baseline"] = round(baseline_rate, 1)
            result["noise_rate_baseline_window"] = len(window)
            trend["噪声率"] = f"{baseline_rate:.1f}%（{len(window)}次均值）→ {noise_rate:.1f}% ({delta:+.1f}%)"
            # 离群标注：当前相对窗口均值偏离 >2pp，提示趋势可能是异常波动
            if abs(delta) > 2.0:
                trend["噪声率_离群"] = (
                    "⚠️ 单次偏离均值 >2pp，趋势可能为异常波动而非真恶化/改善"
                )
    else:
        result["noise_rate_delta"] = 0.0

    return issues, result, trend


def analyze_kn_baseline(baseline_dir: Path) -> tuple[list[dict], dict, dict]:
    latest_data = _load_json(baseline_dir / "baseline_latest.json")
    if not latest_data or not isinstance(latest_data, dict):
        return [], {"status": "no_data"}, {}

    dim_score_stats: dict[str, list[float]] = {}
    dim_source_stats: dict[str, dict[str, list[float]]] = {}
    total_queries = 0
    total_filtered = 0
    total_eval_true = 0
    total_eval_false = 0
    total_hs_kept = 0
    total_kt_kept = 0
    total_sag_kept = 0
    total_latency = 0.0
    latency_count = 0

    for query, m in latest_data.items():
        if not isinstance(m, dict):
            continue
        if _is_test_query(query):
            total_filtered += 1
            continue
        total_queries += 1
        dim = m.get("dimension", "unknown")
        score = m.get("avg_score", 0)
        dim_score_stats.setdefault(dim, []).append(score)
        total_eval_true += m.get("eval_counted_true", 0) or 0
        total_eval_false += m.get("eval_counted_false", 0) or 0

        hs_k = m.get("avg_hs_kept", 0) or 0
        kt_k = m.get("avg_kt_kept", 0) or 0
        sag_k = m.get("avg_sag_kept", 0) or 0
        lat = m.get("avg_latency_ms", 0) or 0
        total_hs_kept += hs_k
        total_kt_kept += kt_k
        total_sag_kept += sag_k
        if lat > 0:
            total_latency += lat
            latency_count += 1

        if dim not in dim_source_stats:
            dim_source_stats[dim] = {"hs_kept": [], "kt_kept": [], "sag_kept": [], "latency": []}
        dim_source_stats[dim]["hs_kept"].append(hs_k)
        dim_source_stats[dim]["kt_kept"].append(kt_k)
        dim_source_stats[dim]["sag_kept"].append(sag_k)
        if lat > 0:
            dim_source_stats[dim]["latency"].append(lat)

    dim_summary = {}
    for dim, scores in dim_score_stats.items():
        src = dim_source_stats.get(dim, {})
        def _avg(lst):
            return round(sum(lst) / len(lst), 2) if lst else 0
        dim_summary[dim] = {
            "count": len(scores),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0,
            "avg_hs_kept": _avg(src.get("hs_kept", [])),
            "avg_kt_kept": _avg(src.get("kt_kept", [])),
            "avg_sag_kept": _avg(src.get("sag_kept", [])),
            "avg_latency_ms": _avg(src.get("latency", [])),
        }

    overall_eval_rate = (
        total_eval_true / (total_eval_true + total_eval_false) * 100
        if (total_eval_true + total_eval_false) > 0
        else 0
    )

    overall_source = {
        "avg_hs_kept": round(total_hs_kept / total_queries, 2) if total_queries else 0,
        "avg_kt_kept": round(total_kt_kept / total_queries, 2) if total_queries else 0,
        "avg_sag_kept": round(total_sag_kept / total_queries, 2) if total_queries else 0,
        "avg_latency_ms": round(total_latency / latency_count, 0) if latency_count else 0,
    }

    issues = []
    for dim, s in dim_summary.items():
        if dim == "unknown":
            continue
        if s["count"] < 3:
            continue
        if s["avg_score"] < TH["kn_avg_score_low"]:
            issues.append({
                "severity": "P1",
                "flywheel": "Router",
                "desc": f"KN基线 dimension={dim} 均分 {s['avg_score']:.4f} < {TH['kn_avg_score_low']}",
                "detail": f"{s['count']} 个查询，HS={s['avg_hs_kept']} KT={s['avg_kt_kept']} SAG={s['avg_sag_kept']}",
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
        "overall_source": overall_source,
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


# === Integrity & Dependency Checks ===

def check_output_integrity(home: Path) -> list[dict]:
    """Check that critical output files exist and are valid JSON or JSONL."""
    # Files that are JSONL (one JSON object per line), not single JSON
    JSONL_FILES = {"clustering_baseline_prev.json"}
    issues = []
    for fw, subpath in REQUIRED_OUTPUTS.items():
        fpath = home / subpath
        if not fpath.exists() or fpath.stat().st_size == 0:
            issues.append({
                "severity": "P1",
                "flywheel": fw,
                "desc": f"产出文件 {fpath.name} 缺失或为空",
                "detail": f"路径: {fpath}",
            })
            continue
        try:
            raw = fpath.read_text(encoding="utf-8").strip()
            if fpath.name in JSONL_FILES:
                # Validate JSONL: each non-empty line must be valid JSON
                lines = [l for l in raw.splitlines() if l.strip()]
                if not lines:
                    issues.append({
                        "severity": "P1",
                        "flywheel": fw,
                        "desc": f"产出文件 {fpath.name} 为空",
                        "detail": f"路径: {fpath}",
                    })
                else:
                    for i, line in enumerate(lines):
                        json.loads(line)  # raises if invalid
            else:
                data = json.loads(raw)
                if not data:
                    issues.append({
                        "severity": "P1",
                        "flywheel": fw,
                        "desc": f"产出文件 {fpath.name} 解析为空",
                        "detail": f"路径: {fpath}",
                    })
        except json.JSONDecodeError as e:
            issues.append({
                "severity": "P1",
                "flywheel": fw,
                "desc": f"产出文件 {fpath.name} JSON 损坏",
                "detail": str(e)[:200],
            })
    return issues


def check_dependency_chain(states: dict[str, dict]) -> list[dict]:
    """Check that upstream tasks ran successfully before downstream tasks."""
    issues = []
    for downstream, upstreams in FLYWHEEL_DEPENDENCIES.items():
        down_state = states.get(downstream, {})
        down_run = down_state.get("run_at", "")
        down_status = down_state.get("status", "")
        if not down_run or down_status != "success":
            continue
        try:
            down_time = datetime.fromisoformat(down_run)
        except (ValueError, TypeError):
            continue
        for up_name in upstreams:
            up_state = states.get(up_name, {})
            up_run = up_state.get("run_at", "")
            up_status = up_state.get("status", "")
            if up_status != "success":
                issues.append({
                    "severity": "P1",
                    "flywheel": _CRON_TO_FLYWHEEL.get(downstream, downstream),
                    "desc": f"依赖链路异常: {downstream} 依赖 {up_name} 未成功",
                    "detail": f"{up_name} status={up_status}",
                })
                continue
            try:
                up_time = datetime.fromisoformat(up_run) if up_run else None
            except (ValueError, TypeError):
                up_time = None
            if up_time and down_time and up_time > down_time:
                issues.append({
                    "severity": "P1",
                    "flywheel": _CRON_TO_FLYWHEEL.get(downstream, downstream),
                    "desc": f"依赖时序异常: {downstream} 运行早于上游 {up_name}",
                    "detail": f"{downstream}: {down_run[:16]}, {up_name}: {up_run[:16]}",
                })
    return issues


def detect_zombie_state_files(cron_state_dir: Path) -> list[str]:
    """Find state files not belonging to active flywheel jobs.

    已知的非飞轮 state 文件（自愈框架/巡检/报告自身/去重字典）通过
    EXCLUDED_STATE_FILES 白名单排除，不再报告为 zombie。
    """
    zombies = []
    if not cron_state_dir.is_dir():
        return zombies
    for f in sorted(cron_state_dir.glob("*.json")):
        if f.stem in ACTIVE_CRON_JOBS:
            continue
        if f.stem in EXCLUDED_STATE_FILES:
            continue
        zombies.append(f.stem)
    return zombies


def detect_report_type(cron_state_dir: Path, now_utc: datetime) -> str:
    """Detect if this is a scheduled run or boot catch-up."""
    boot_state = _load_json(cron_state_dir / "cron-boot-detect.json")
    if boot_state:
        boot_run = boot_state.get("run_at", "")
        boot_status = boot_state.get("status", "")
        if boot_status == "partial" and boot_run:
            try:
                boot_dt = datetime.fromisoformat(boot_run)
                if boot_dt.tzinfo is None:
                    boot_dt = boot_dt.replace(tzinfo=timezone(timedelta(hours=8)))
                hours_ago = (now_utc - boot_dt.astimezone(timezone.utc)).total_seconds() / 3600
                if 0 <= hours_ago <= 12:
                    return "boot-catch-up"
            except (ValueError, TypeError):
                pass
    return "scheduled"


def format_7day_trend(data_flywheel: Path) -> list[str]:
    """Format 7-day rolling trend table."""
    records = load_daily_summary(data_flywheel)
    if len(records) < 2:
        return ["历史数据不足 2 天，7 天趋势待积累。"]
    lines = [
        "| 日期 | P0/P1 | Router得分 | 全关% | 空结果% | 错误% | KT降级 | Token耗尽% | SAG开启% | SAG召回量 | "
        "SAG延迟ms | Skill F1 | Skill活跃 | Skill调用次数 | KN unknown% | KN均分 | 聚类噪声% | KT孤立% | MEM占用% | USER占用% | Hindsight产出 | ERROR数 |"
    ]
    lines.append(
        "|------|-------|-----------|-------|---------|-------|--------|-----------|----------|-----------|"
        "----------|----------|----------|------------|-------------|--------|-----------|---------|---------|---------|--------------|--------|"
    )
    for r in records[-7:]:
        p0 = r.get("p0_count", 0)
        p1 = r.get("p1_count", 0)
        lines.append(
            f"| {r.get('date', '-')} | {p0}/{p1} | "
            f"{r.get('router_avg_score', '-')} | "
            f"{r.get('router_full_off_pct', '-')} | "
            f"{r.get('router_empty_pct', '-')} | "
            f"{r.get('router_error_rate', '-')} | "
            f"{r.get('router_kt_fallback_count', '-')} | "
            f"{r.get('token_exhaust_pct', '-')} | "
            f"{r.get('sag_on_pct', '-')} | "
            f"{r.get('sag_total_kept', '-')} | "
            f"{r.get('sag_avg_latency_ms', '-')} | "
            f"{r.get('skill_f1', '-')} | "
            f"{r.get('skill_active_count', '-')} | "
            f"{r.get('skill_total_uses', '-')} | "
            f"{r.get('kn_unknown_pct', '-')} | "
            f"{round(r.get('kn_avg_score', 0), 4) if r.get('kn_avg_score') else '-'} | "
            f"{r.get('cluster_noise_rate', '-')} | "
            f"{r.get('kt_orphan_pct', '-')} | "
            f"{r.get('memory_usage_pct', '-')} | "
            f"{r.get('memory_user_usage_pct', '-')} | "
            f"{r.get('memory_hindsight_count', '-')} | "
            f"{r.get('error_count', '-')} |"
        )
    return lines


# === Report Generator ===

def _resolve_trend_arrow(delta_val: float) -> str:
    if delta_val > 0.01:
        return "↑ 改善"
    elif delta_val < -0.01:
        return "↓ 恶化"
    return "→ 持平"


def generate_report(home: Path, dry_run: bool = False) -> tuple[str, list[dict]]:
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")
    # 报告在 CN 08:00（UTC 00:00）生成，此时 UTC 前一天的完整 24h 数据已就绪。
    # 数据窗口 = UTC 昨天，对应 CN 用户视角的"昨天"（晚间已完成的改动）。
    # 例：CN 7/16 08:00 生成报告 → 数据窗口 = UTC 7/15 = CN 7/15（含用户 7/15 晚间改动）
    data_window = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    cron_state_dir = home / CRON_STATE_SUBPATH
    cron_log_dir = home / CRON_LOG_SUBPATH
    trace_path = home / TRACE_LOG_SUBPATH
    kn_baseline_dir = home / KN_BASELINE_SUBPATH
    data_flywheel_dir = home / DATA_FLYWHEEL_SUBPATH
    skill_usage_path = home / SKILL_USAGE_SUBPATH
    error_log_path = home / ERROR_LOG_SUBPATH

    # Parse all data
    cron_states = parse_cron_states(cron_state_dir)
    trace = parse_trace_log(trace_path, filter_date=data_window)

    # Analyze
    cron_issues, cron_table, elapsed_ann = analyze_cron_jobs(cron_states, cron_log_dir, now)
    router_issues, router_m, router_trend = analyze_router(trace, data_flywheel_dir)
    skill_issues, skill_m, skill_trend = analyze_skill_eval(data_flywheel_dir, kn_baseline_dir)
    skill_usage_issues, skill_usage_m, skill_usage_trend = analyze_skill_usage(skill_usage_path, now)
    token_issues, token_m, token_trend = analyze_token_budget(trace)
    sag_contr_issues, sag_contr_m, sag_contr_trend = analyze_sag_contribution(trace)
    error_issues, error_m, error_trend = analyze_global_errors(error_log_path, data_window)
    kt_issues, kt_m, kt_trend = analyze_kt_baseline(data_flywheel_dir)
    cluster_issues, cluster_m, cluster_trend = analyze_clustering(data_flywheel_dir)
    kn_issues, kn_m, kn_trend = analyze_kn_baseline(kn_baseline_dir)
    memory_issues, memory_m, memory_trend = analyze_memory_cleanup(home / MEMORY_DIR_SUBPATH, data_window)

    credibility_warnings, credibility_notes = analyze_data_credibility(
        kt_m, router_m, kn_m, now
    )

    # Collect issues
    # Integrity & dependency checks
    integrity_issues = check_output_integrity(home)
    dep_issues = check_dependency_chain(cron_states)
    zombie_files = detect_zombie_state_files(cron_state_dir)

    all_issues = (cron_issues + router_issues + skill_issues + skill_usage_issues +
                  token_issues + sag_contr_issues + error_issues + kt_issues +
                  cluster_issues + kn_issues + memory_issues +
                  integrity_issues + dep_issues)
    p0 = [i for i in all_issues if i["severity"] == "P0"]
    p1 = [i for i in all_issues if i["severity"] == "P1"]

    L = []
    # 报告标题用 data_window（UTC 昨天，对应 CN 当天凌晨前已完整的 24h）
    # 这样标题日期、数据窗口、daily-summary 记录日期三者一致
    L.append(f"# Flywheel Health Report - {data_window}")
    L.append("")
    L.append(f"**Generated**: {now_str}")
    L.append(f"**Home**: `{home}`")
    report_type = detect_report_type(cron_state_dir, now)
    L.append(f"**Report type**: `{report_type}`")
    L.append(f"**Data window**: `{data_window}` (UTC, 完整 24h)")
    zombie_total = len(list(cron_state_dir.glob("*.json"))) - len(cron_table) if cron_state_dir.is_dir() else 0
    L.append(f"**Core cron tasks**: {len(cron_table)} 个（排除 {zombie_total} 个非飞轮）")
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
        L.append(f"- Hindsight 开启: {router_m['h_on']} | 知识树: {router_m['kt_on']} | Skill: {router_m['s_on']} | SAG: {router_m['sag_on']} ({router_m['sag_on_pct']}%)")
        L.append(f"- 召回成功: {router_m['success_count']} | 空结果: {router_m['empty_count']} | "
                 f"超时: {router_m['timeout_count']} | 错误: {router_m.get('error_count', 0)} | "
                 f"KT降级: {router_m.get('kt_fallback_count', 0)}")
        L.append(f"- 成功率: {router_m['success_rate']}% | 空结果率: {router_m['empty_rate']}% | "
                 f"错误率: {router_m.get('error_rate', 0)}% | KT降级率: {router_m.get('kt_fallback_rate', 0)}%")
        L.append(f"- 平均延迟: {router_m['avg_latency_ms']}ms | p50: {router_m['p50_latency_ms']}ms | "
                 f"p95: {router_m['p95_latency_ms']}ms | p99: {router_m['p99_latency_ms']}ms | 最大: {router_m['max_latency_ms']}ms")
        L.append(f"- 平均得分: {router_m['avg_score']} | 多跳展开: {router_m['multi_hop_count']} 次")
        L.append("")
        L.append("**Token 预算:**")
        if token_m.get("status") == "no_data":
            L.append("- 无 token_budget 数据")
        else:
            L.append(f"- 总预算: {token_m['total_budget']} tokens | 事件数: {token_m['event_count']}")
            ts = token_m["total_stats"]
            L.append(f"- 消耗: 平均 {ts['avg']} | p50 {ts['p50']} | p90 {ts['p90']} | 最大 {ts['max']}")
            L.append(f"- 耗尽率: {token_m['exhaust_pct']}% ({token_m['exhaust_count']}/{token_m['event_count']} 次接近耗尽)")
            hs = token_m["hs_stats"]
            kt = token_m["kt_stats"]
            sk = token_m["skill_stats"]
            L.append(f"- 分源消耗: Hindsight avg={hs['avg']}  KT avg={kt['avg']}  Skill avg={sk['avg']}")
        L.append("")
        L.append("**SAG 专项:**")
        L.append(f"- Router 召回尝试: {router_m['sag_recall_count']} | 异常: {router_m.get('sag_error_count', 0)} | 非空: {router_m['sag_non_empty_count']} | 累计注入: {router_m['sag_total_kept']} 条")
        L.append(f"- 平均延迟: {router_m['sag_avg_latency_ms']}ms | p50: {router_m['sag_p50_latency_ms']}ms | p95: {router_m['sag_p95_latency_ms']}ms")
        if sag_contr_m.get("status") != "no_data":
            rs = sag_contr_m["recall_stats"]
            ms = sag_contr_m["merge_stats"]
            # recall_count 与 router_m['sag_recall_count'] 相同，此处不重复显示
            L.append(f"- 成功召回: {sag_contr_m.get('recall_success_count', sag_contr_m['recall_count'])} 次 (零结果 {sag_contr_m['recall_zero']}), 平均 {rs['avg']} sections, 总计 {rs['total']}")
            if sag_contr_m.get("recall_error_count", 0) > 0:
                L.append(f"- 召回异常: {sag_contr_m['recall_error_count']} 次 (已计入上方尝试数)")
            L.append(f"- SAG 合并量: {sag_contr_m['merge_count']} 次，平均 {ms['avg']} 条，零结果率: {sag_contr_m['merge_zero_pct']}%")
    L.append("")

    # KN 基线
    L.append("### KN 基线")
    L.append("")
    if kn_m.get("status") == "no_data":
        L.append("- 无 baseline 数据")
    else:
        L.append(f"- 用户查询: {kn_m['total_queries']} | 已过滤测试查询: {kn_m['total_filtered']}")
        L.append(f"- 未知维度占比: {kn_m['unknown_dim_pct']}%")
        os = kn_m.get("overall_source", {})
        if os:
            L.append(f"- 整体源级贡献: HS={os.get('avg_hs_kept', 0)} "
                     f"KT={os.get('avg_kt_kept', 0)} SAG={os.get('avg_sag_kept', 0)} "
                     f"| 延迟: {os.get('avg_latency_ms', 0)}ms")
        L.append("  *Eval 命中率: 基线中 eval_counted_true/false 均为 0 "
                 "（LLM judge 评估结果未持久化至该字段，召回成功率参考 trace.log 数据）*")
        L.append("")
        L.append("| Dimension | 查询数 | 均分 | HS | KT | SAG | 延迟ms |")
        L.append("|-----------|--------|------|----|----|-----|--------|")
        for dim, s in sorted(kn_m["dim_summary"].items()):
            flag = " ⚠️" if dim == "unknown" else ""
            L.append(f"| {dim}{flag} | {s['count']} | {s['avg_score']} | "
                     f"{s.get('avg_hs_kept', 0)} | {s.get('avg_kt_kept', 0)} | "
                     f"{s.get('avg_sag_kept', 0)} | {s.get('avg_latency_ms', 0)} |")
    L.append("")

    # Skill
    L.append("### Skill 飞轮")
    L.append("")
    if skill_m.get("status") == "no_data":
        L.append("- 无 skill_eval 数据")
    else:
        L.append(f"- **匹配质量 (eval)**: F1={skill_m['avg_f1']} | Precision={skill_m['avg_precision']} | "
                 f"Recall={skill_m['avg_recall']}")
        L.append(f"- 评估查询数: {skill_m['n_queries']} | 时间: {skill_m['timestamp']}")
    if skill_usage_m.get("status") != "no_data":
        L.append("")
        L.append(f"- **真实使用**: 总 Skill {skill_usage_m['total_skills']} 个 | "
                 f"active {skill_usage_m['active_count']} | 已使用 {skill_usage_m['used_count']} | "
                 f"从未使用 {skill_usage_m['never_used_count']}")
        L.append(f"- 总使用次数: {skill_usage_m['total_uses']} | 总浏览: {skill_usage_m['total_views']}")
        if skill_usage_m.get("stale_count", 0) > 0:
            L.append(f"- 超 {TH['skill_unused_warn_days']} 天未使用: {skill_usage_m['stale_count']} 个")
        L.append("")
        L.append("**Top 10 使用最多:**")
        L.append("")
        L.append("| # | Skill | 使用 | 浏览 | 最后使用 |")
        L.append("|---|-------|------|------|---------|")
        for i, s in enumerate(skill_usage_m["top_used"], 1):
            L.append(f"| {i} | {s['name']} | {s['use_count']} | {s['view_count']} | {s['last_used_at']} |")
        if skill_usage_m.get("recent_7d"):
            L.append("")
            L.append(f"**近 7 天活跃 ({len(skill_usage_m['recent_7d'])} 个):**")
            L.append(", ".join(s["name"] for s in skill_usage_m["recent_7d"][:8]))
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
        L.append(f"- 噪声率: {cluster_m['noise_rate']}%{' ⚠️' if cluster_m['noise_rate'] > TH['cluster_noise_rate_high'] else ''}")
        L.append(f"- 聚类数: {cluster_m['cluster_count']} | Memory Links: {cluster_m['memory_links']}")
        L.append(f"- 总单元: {cluster_m['total_units']}")
        if "noise_rate_delta" in cluster_m:
            L.append(f"- 噪声率变化: {cluster_m['noise_rate_delta']:+.1f}%")
        L.append(f"- 时间: {cluster_m['timestamp']}")
    L.append("")

    # 记忆清理
    L.append("### 记忆清理")
    L.append("")
    if memory_m.get("status") == "no_data":
        L.append("- 无记忆清理数据")
    else:
        L.append(f"- MEMORY.md: {memory_m['memory_chars']:,}/{memory_m.get('memory_limit', 50000):,} chars ({memory_m['memory_usage_pct']}%){' ⚠️' if memory_m['memory_usage_pct'] > TH['memory_char_usage_high_pct'] else ''}")
        L.append(f"- USER.md:   {memory_m['user_chars']:,}/{memory_m.get('user_limit', 15000):,} chars ({memory_m['user_usage_pct']}%){' ⚠️' if memory_m['user_usage_pct'] > TH['memory_char_usage_high_pct'] else ''}")
        L.append(f"- 清理产出: compress {memory_m['total_compress']} | hindsight {memory_m['total_hindsight']} | remove {memory_m['total_remove']} | merge {memory_m['total_merge']}")
        if memory_m.get("v2_correct_rate", 0) > 0:
            L.append(f"- Phase 2 正确率: {memory_m['v2_correct_rate']}%")
        if memory_m.get("tokens_total", 0) > 0:
            L.append(f"- Token 消耗: {memory_m['tokens_total']:,}")
        L.append(f"- 耗时: {memory_m['elapsed_s']}s | 模式: {memory_m['mode']}")
    L.append("")

    # 全局错误
    L.append("### 全局错误监控")
    L.append("")
    if error_m.get("status") == "no_data":
        L.append("- 无 errors.log 数据")
    else:
        L.append(f"- 当日问题日志: {error_m.get('date_logs', 0)} 条 "
                 f"(ERROR {error_m.get('error_count', 0)} | WARNING {error_m.get('warning_count', 0)})")
        L.append(f"- ERROR 占比: {error_m.get('error_pct', 0)}%")
        top_mods = error_m.get("top_modules", [])
        if top_mods:
            L.append("")
            L.append("**Top 10 错误模块:**")
            L.append("")
            L.append("| # | 模块 | 条数 |")
            L.append("|---|------|------|")
            for i, m in enumerate(top_mods, 1):
                L.append(f"| {i} | {m['module']} | {m['count']} |")
        top_kws = error_m.get("top_keywords", [])
        if top_kws:
            L.append("")
            kw_str = ", ".join(f"{k['keyword']}({k['count']})" for k in top_kws)
            L.append(f"**关键词分布**: {kw_str}")
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
    all_trends.update(memory_trend)

    if all_trends:
        L.append("| 指标 | 变化 |")
        L.append("|------|------|")
        for key, val in sorted(all_trends.items()):
            L.append(f"| {key} | {val} |")
    else:
        L.append("无趋势数据（基线历史数据不足，V2 自动积累）")
    L.append("")

    # === 7 天滚动趋势 ===
    L.append("## 📊 7 天滚动趋势")
    L.append("")
    L.extend(format_7day_trend(data_flywheel_dir))
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
    if zombie_files:
        L.append(f"- 📝 非 飞轮 state 文件: {', '.join(zombie_files)}")
    L.append("")

    # Save daily summary for 7-day trend (date = 数据窗口日期)
    append_daily_summary(data_flywheel_dir, {
        "date": data_window,
        "report_type": report_type,
        "p0_count": len(p0),
        "p1_count": len(p1),
        "router_full_off_pct": router_m.get("full_off_pct", 0),
        "router_empty_pct": router_m.get("empty_rate", 0),
        "router_error_rate": router_m.get("error_rate", 0),
        "router_kt_fallback_count": router_m.get("kt_fallback_count", 0),
        "router_avg_score": router_m.get("avg_score", 0),
        "router_avg_latency_ms": router_m.get("avg_latency_ms", 0),
        "sag_on_pct": router_m.get("sag_on_pct", 0),
        "sag_total_kept": router_m.get("sag_total_kept", 0),
        "sag_avg_latency_ms": router_m.get("sag_avg_latency_ms", 0),
        "sag_merge_zero_pct": sag_contr_m.get("merge_zero_pct", 0),
        "token_exhaust_pct": token_m.get("exhaust_pct", 0),
        "skill_f1": skill_m.get("avg_f1", 0),
        "skill_active_count": skill_usage_m.get("active_count", 0),
        # used_count = 使用过（use_count>0）的不同 active skill 数量
        # total_uses = 所有 active skill 的 use_count 之和（总调用次数）
        "skill_used_count": skill_usage_m.get("used_count", 0),
        "skill_total_uses": skill_usage_m.get("total_uses", 0),
        "kn_unknown_pct": kn_m.get("unknown_dim_pct", 0),
        "kn_avg_score": sum(s["avg_score"] for s in kn_m.get("dim_summary", {}).values()) / max(len(kn_m.get("dim_summary", {})), 1) if kn_m.get("dim_summary") else 0,
        "cluster_noise_rate": cluster_m.get("noise_rate", 0),
        "kt_orphan_pct": kt_m.get("orphan_pct", 0),
        "memory_usage_pct": memory_m.get("memory_usage_pct", 0),
        "memory_user_usage_pct": memory_m.get("user_usage_pct", 0),
        "memory_hindsight_count": memory_m.get("total_hindsight", 0),
        "memory_compress_count": memory_m.get("total_compress", 0),
        "error_count": error_m.get("error_count", 0),
        "warning_count": error_m.get("warning_count", 0),
    })

    # === 优化方向 ===
    L.append("## 💡 优化方向")
    L.append("")
    recs = generate_recommendations(
        router_m, skill_m, kn_m, kt_m, cluster_m,
        all_issues, all_trends, credibility_warnings, zombie_files,
        token_m, sag_contr_m, skill_usage_m, error_m, memory_m
    )
    if recs:
        for r in recs:
            L.append(f"- **{r['flywheel']}**: {r['desc']}")
    else:
        L.append("✅ 当前无优先优化项，继续保持日常维护。")
    L.append("")

    return "\n".join(L), p0


def generate_recommendations(
    router_m: dict, skill_m: dict, kn_m: dict,
    kt_m: dict, cluster_m: dict,
    issues: list[dict], trends: dict[str, str],
    credibility_warnings: list[str], zombie_files: list[str],
    token_m: dict | None = None,
    sag_contr_m: dict | None = None,
    skill_usage_m: dict | None = None,
    error_m: dict | None = None,
    memory_m: dict | None = None,
) -> list[dict]:
    """Generate actionable optimization recommendations based on current metrics."""
    recs: list[dict] = []

    # --- Router ---
    if router_m.get("status") != "no_data":
        n = router_m.get("total_masks", 0)
        if n > 0 and n < TH["min_sample_size"]:
            recs.append({"flywheel": "Router", "desc": f"样本量不足（{n} 次 < {TH['min_sample_size']}），建议增加日常路由量或降低最小样本阈值"})
        full_off = router_m.get("full_off_pct", 0)
        if full_off > 15:
            recs.append({"flywheel": "Router", "desc": f"全关率 {full_off}% 偏高，建议检查 Router prompt 是否过度保守或模型超时频发"})
        empty = router_m.get("empty_rate", 0)
        if empty > 10:
            recs.append({"flywheel": "Router", "desc": f"空结果率 {empty}% 偏高，建议检查 Hindsight/知识树召回链路或降低 min_score 阈值"})
        avg_lat = router_m.get("avg_latency_ms", 0)
        if avg_lat > 8000:
            recs.append({"flywheel": "Router", "desc": f"平均延迟 {avg_lat}ms 偏高，建议排查 Hindsight daemon 连接池或 Reranker 超时"})
        avg_score = router_m.get("avg_score", 0)
        if 0 < avg_score < 0.4:
            recs.append({"flywheel": "Router", "desc": f"平均得分 {avg_score} 偏低，召回结果相关性不足，建议调整 embedding 或 reranker 模型"})

        sag_on = router_m.get("sag_on_pct", 0)
        if sag_on < 10 and n > 0:
            recs.append({"flywheel": "SAG", "desc": f"SAG 开启率仅 {sag_on}%，Router 极少触发 SAG 召回，建议检查 Router prompt 或 SAG 触发条件"})
        sag_kept = router_m.get("sag_total_kept", 0)
        if sag_on > 30 and sag_kept == 0:
            recs.append({"flywheel": "SAG", "desc": f"SAG 开启率 {sag_on}% 但召回量为 0，可能 SAG 服务异常或索引为空，建议排查 SAG 健康状态"})
        sag_lat = router_m.get("sag_avg_latency_ms", 0)
        if sag_lat > 3000:
            recs.append({"flywheel": "SAG", "desc": f"SAG 平均延迟 {sag_lat}ms 偏高，建议排查 SAG 服务性能或网络连接"})

        # SAG 贡献度
        if sag_contr_m and sag_contr_m.get("status") != "no_data":
            merge_zero = sag_contr_m.get("merge_zero_pct", 0)
            recall_success_count = sag_contr_m.get("recall_success_count", 0)
            recall_error_count = sag_contr_m.get("recall_error_count", 0)
            recall_zero = sag_contr_m.get("recall_zero", 0)
            # 全部成功召回为 0 section 的极端情况（排除 error 场景）
            if recall_success_count > 0 and recall_zero == recall_success_count:
                recs.append({"flywheel": "SAG", "desc": f"SAG 全部 {recall_success_count} 次成功召回均为 0 section，索引可能为空或搜索条件过严，建议检查 SAG 索引完整性和 query 构造逻辑"})
            elif merge_zero > 50 and sag_on > 10:
                recs.append({"flywheel": "SAG", "desc": f"SAG 合并零结果率 {merge_zero}%，召回内容未通过去重/打分，建议降低 SAG 阈值或优化 SAG 索引质量"})
            if recall_error_count > 0:
                recs.append({"flywheel": "SAG", "desc": f"SAG 召回异常 {recall_error_count} 次，建议检查 SAG 服务健康状态和熔断器日志"})

    # --- Token 预算 ---
    if token_m and token_m.get("status") != "no_data":
        exhaust = token_m.get("exhaust_pct", 0)
        if exhaust > TH["token_budget_exhaust_pct"]:
            recs.append({"flywheel": "Token", "desc": f"Token 预算耗尽率 {exhaust}%，可能导致召回截断，建议增加 total_budget 或优化各源 token 占用"})
        total_avg = token_m.get("total_stats", {}).get("avg", 0)
        budget = token_m.get("total_budget", 4000)
        if budget and total_avg / budget > 0.8:
            recs.append({"flywheel": "Token", "desc": f"Token 平均使用率 {total_avg/budget*100:.0f}% 偏高，建议关注高峰期耗尽风险"})

    # --- Skill ---
    if skill_m.get("status") != "no_data":
        f1 = skill_m.get("avg_f1", 0)
        if 0 < f1 < TH["skill_f1_low"]:
            recs.append({"flywheel": "Skill", "desc": f"F1={f1} 低于阈值，建议检查 skillopt-nightly-run 训练数据质量或调整评估基准"})
        elif 0 < f1 < 0.6:
            recs.append({"flywheel": "Skill", "desc": f"F1={f1} 有提升空间，建议关注 Precision/Recall 差异，优化 skill_matcher 关键词扩展"})
        precision = skill_m.get("avg_precision", 0)
        recall = skill_m.get("avg_recall", 0)
        if precision > 0 and recall > 0:
            if recall < precision * 0.7:
                recs.append({"flywheel": "Skill", "desc": f"Recall ({recall}) 远低于 Precision ({precision})，建议扩充同义词库或增加中英文双向匹配"})
            elif precision < recall * 0.7:
                recs.append({"flywheel": "Skill", "desc": f"Precision ({precision}) 远低于 Recall ({recall})，建议收紧匹配规则或增加负样本"})

    # --- Skill 真实使用 ---
    if skill_usage_m and skill_usage_m.get("status") != "no_data":
        never_used = skill_usage_m.get("never_used_count", 0)
        active = skill_usage_m.get("active_count", 0)
        if active and never_used / active > 0.15:
            recs.append({"flywheel": "Skill", "desc": f"{never_used}/{active} ({never_used/active*100:.0f}%) 个 active skill 从未使用，建议 review 并归档低价值 skill"})
        stale = skill_usage_m.get("stale_count", 0)
        if stale > TH["skill_unused_warn_count"]:
            recs.append({"flywheel": "Skill", "desc": f"{stale} 个 skill 超过 {TH['skill_unused_warn_days']} 天未使用，建议评估是否仍需维护"})

    # --- KN Baseline ---
    if kn_m.get("status") != "no_data":
        unknown_pct = kn_m.get("unknown_dim_pct", 0)
        if unknown_pct > 20:
            recs.append({"flywheel": "KN", "desc": f"unknown 维度占比 {unknown_pct}%，建议优化维度分类器或扩充基线查询覆盖"})
        dim_summary = kn_m.get("dim_summary", {})
        for dim, s in dim_summary.items():
            if dim == "unknown" or s.get("count", 0) < 3:
                continue
            if s.get("avg_score", 1) < TH["kn_avg_score_low"]:
                recs.append({"flywheel": "KN", "desc": f"dimension={dim} 均分 {s['avg_score']} 偏低，建议针对性增加该维度召回源或调整权重"})

    # --- 知识树 ---
    if kt_m.get("status") != "no_data":
        orphan = kt_m.get("orphan_pct", 0)
        if orphan > 50:
            recs.append({"flywheel": "知识树", "desc": f"孤立知识点 {orphan}%，建议运行 consolidate 补齐 knowledge_tree_edges 或检查 k_vector 兜底"})
        frag = kt_m.get("fragment_domains", 0)
        if frag > 10:
            recs.append({"flywheel": "知识树", "desc": f"碎片域 {frag} 个，建议合并相似域或调整 HDBSCAN min_cluster_size"})
        conf = kt_m.get("avg_confidence", 0)
        if 0 < conf < 0.8:
            recs.append({"flywheel": "知识树", "desc": f"平均置信度 {conf} 偏低，建议检查知识点提取 prompt 或增加准入校验"})

    # --- 聚类 ---
    if cluster_m.get("status") != "no_data":
        noise = cluster_m.get("noise_rate", 0)
        if noise > 30:
            recs.append({"flywheel": "聚类", "desc": f"噪声率 {noise}% 偏高，建议调整 HDBSCAN min_cluster_size 或增加 min_llm_size"})
        n_clusters = cluster_m.get("cluster_count", 0)
        if n_clusters > 0 and n_clusters < 3:
            recs.append({"flywheel": "聚类", "desc": f"聚类数仅 {n_clusters}，可能过粗，建议降低 min_cluster_size 或增加样本量"})
        links = cluster_m.get("memory_links", 0)
        if 0 < links < 50:
            recs.append({"flywheel": "聚类", "desc": f"Memory Links 仅 {links}，聚类间关联稀疏，建议检查 memory_links 写入逻辑"})

    # --- 记忆清理 ---
    if memory_m and memory_m.get("status") != "no_data":
        mem_usage = memory_m.get("memory_usage_pct", 0)
        user_usage = memory_m.get("user_usage_pct", 0)
        if mem_usage > 80:
            recs.append({"flywheel": "记忆", "desc": f"MEMORY.md 占用 {mem_usage}%，接近上限，建议增加清理力度或提高 compress/hindsight 迁移比例"})
        if user_usage > 80:
            recs.append({"flywheel": "记忆", "desc": f"USER.md 占用 {user_usage}%，接近上限，建议精简用户偏好和个人信息"})
        hindsight = memory_m.get("total_hindsight", 0)
        compress = memory_m.get("total_compress", 0)
        if hindsight == 0 and compress == 0 and mem_usage > 50:
            recs.append({"flywheel": "记忆", "desc": f"连续无 hindsight/compress 产出（占用 {mem_usage}%），建议检查分类 prompt 是否过于保守"})

    # --- 趋势恶化 ---
    for key, val in trends.items():
        if "→" in val and "(" in val:
            try:
                m = re.search(r"\(([+-]?\d+\.?\d*)", val)
                if not m:
                    continue
                delta = float(m.group(1))
                if delta > 0 and any(k in key for k in ["全关率", "空结果率", "噪声率", "孤立率", "unknown", "MEMORY占用率", "USER占用率"]):
                    recs.append({"flywheel": "趋势", "desc": f"{key} 恶化 ({val})，建议关注并排查根因"})
                elif delta < 0 and any(k in key for k in ["F1", "得分", "成功率"]):
                    recs.append({"flywheel": "趋势", "desc": f"{key} 下降 ({val})，建议关注并排查根因"})
            except (ValueError, IndexError):
                pass

    # --- 全局错误 ---
    if error_m and error_m.get("status") != "no_data":
        err_count = error_m.get("error_count", 0)
        if err_count > 50:
            recs.append({"flywheel": "系统", "desc": f"当日 ERROR 日志 {err_count} 条偏多，建议排查 top 错误模块"})
        top_mods = error_m.get("top_modules", [])
        if top_mods:
            top1 = top_mods[0]
            total = error_m.get("date_logs", 1)
            if total and top1["count"] / total > 0.5:
                recs.append({"flywheel": "系统", "desc": f"错误集中在 {top1['module']} ({top1['count']}/{total}, {top1['count']/total*100:.0f}%)，建议优先排查"})

    # --- 僵尸文件 ---
    if zombie_files:
        recs.append({"flywheel": "维护", "desc": f"发现 {len(zombie_files)} 个非飞轮 state 文件 ({', '.join(zombie_files[:3])})，建议清理以减少噪音"})

    return recs


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
        # 文件名用 CN 日期，与 flywheel-health-report.sh 一致（cron 17:00 CN = 09:00 UTC）
        # 报告标题/数据窗口内部用 UTC 昨天，但文件名用 CN 今天便于用户识别
        today_cn = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        output_path = output_dir / f"flywheel-report-{today_cn}.md"
        output_path.write_text(report, encoding="utf-8")
        print(f"\n[Report saved to: {output_path}]")

    sys.exit(1 if p0_issues else 0)


if __name__ == "__main__":
    main()