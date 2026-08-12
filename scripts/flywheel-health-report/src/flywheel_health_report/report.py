"""report.py — 7 天趋势表与报告生成器。

从 flywheel-health-report.py L1549-1983 搬入。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import (
    TH, _CRON_TO_FLYWHEEL, _FLYWHEEL_ORDER,
    CRON_STATE_SUBPATH, CRON_LOG_SUBPATH, TRACE_LOG_SUBPATH,
    KN_BASELINE_SUBPATH, DATA_FLYWHEEL_SUBPATH, SKILL_USAGE_SUBPATH,
    ERROR_LOG_SUBPATH, MEMORY_DIR_SUBPATH, EXCLUDED_STATE_FILES,
)
from .parsers import (
    parse_cron_states, parse_cron_jobs_json,
    parse_trace_log, append_daily_summary, load_daily_summary,
)
from .utils import _resolve_trend_arrow
from .integrity import (
    check_output_integrity, check_dependency_chain,
    detect_zombie_state_files, detect_report_type,
)
from .analyzers.cron_jobs import analyze_cron_jobs
from .analyzers.router import analyze_router
from .analyzers.skill import analyze_skill_eval, analyze_skill_usage
from .analyzers.token_usage import analyze_token_usage
from .analyzers.sag import analyze_sag_contribution
from .analyzers.global_errors import analyze_global_errors
from .analyzers.kt_baseline import analyze_kt_baseline
from .analyzers.clustering import analyze_clustering
from .analyzers.kn_baseline import analyze_kn_baseline, analyze_data_credibility
from .analyzers.kn_judge import run_judge_within_window, summarize_param_tuning
from .analyzers.memory_cleanup import analyze_memory_cleanup
from .analyzers.self_evolving import analyze_self_evolving
from .recommendations import generate_recommendations
from .runner import load_runner_summary


def format_7day_trend(data_flywheel: Path) -> list[str]:
    """Format 7-day rolling trend table."""
    records = load_daily_summary(data_flywheel)
    if len(records) < 2:
        return ["历史数据不足 2 天，7 天趋势待积累。"]
    lines = [
        "| 日期 | P0/P1 | Router得分 | 全关% | 空结果% | 错误% | KT降级 | Token消耗avg | Skill占比% | SAG开启% | SAG召回量 | "
        "SAG延迟ms | Skill F1 | Skill活跃 | Skill调用次数 | KN unknown% | KN均分 | 聚类噪声% | KT孤立% | MEM占用% | USER占用% | Hindsight产出 | ERROR数 |"
    ]
    lines.append(
        "|------|-------|-----------|-------|---------|-------|--------|-------------|-----------|----------|-----------|"
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
            f"{r.get('token_total_avg', '-')} | "
            f"{r.get('token_skill_share_pct', '-')} | "
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

def generate_report(home: Path, dry_run: bool = False) -> tuple[str, list[dict]]:
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")
    # 报告在 CN 08:00（UTC 00:00）生成，此时 UTC 前一天的完整 24h 数据已就绪。
    # 数据窗口 = UTC 昨天 + 前天（2 天滚动），保证 Router 样本量 ≥ 50。
    # 例：CN 7/24 08:00 生成报告 → 数据窗口 = [UTC 7/23, UTC 7/22]
    data_window = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    data_window_prev = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    data_windows = [data_window, data_window_prev]

    cron_state_dir = home / CRON_STATE_SUBPATH
    cron_log_dir = home / CRON_LOG_SUBPATH
    trace_path = home / TRACE_LOG_SUBPATH
    kn_baseline_dir = home / KN_BASELINE_SUBPATH
    data_flywheel_dir = home / DATA_FLYWHEEL_SUBPATH
    skill_usage_path = home / SKILL_USAGE_SUBPATH
    error_log_path = home / ERROR_LOG_SUBPATH

    # Parse all data — merge cron-state files (rich) with jobs.json (supplementary)
    cron_states = parse_cron_states(cron_state_dir)
    cron_states.update(parse_cron_jobs_json(home, cron_states))
    trace = parse_trace_log(trace_path, filter_dates=data_windows)

    # Analyze
    cron_issues, cron_table, elapsed_ann = analyze_cron_jobs(cron_states, cron_log_dir, now)
    router_issues, router_m, router_trend = analyze_router(trace, data_flywheel_dir)
    skill_issues, skill_m, skill_trend = analyze_skill_eval(data_flywheel_dir, kn_baseline_dir)
    skill_usage_issues, skill_usage_m, skill_usage_trend = analyze_skill_usage(skill_usage_path, now)
    token_issues, token_m, token_trend = analyze_token_usage(trace)
    sag_contr_issues, sag_contr_m, sag_contr_trend = analyze_sag_contribution(trace)
    error_issues, error_m, error_trend = analyze_global_errors(error_log_path, data_window)
    kt_issues, kt_m, kt_trend = analyze_kt_baseline(data_flywheel_dir)
    cluster_issues, cluster_m, cluster_trend = analyze_clustering(data_flywheel_dir)
    kn_issues, kn_m, kn_trend = analyze_kn_baseline(kn_baseline_dir)
    memory_issues, memory_m, memory_trend = analyze_memory_cleanup(home / MEMORY_DIR_SUBPATH, data_window)
    se_issues, se_m, se_trend = analyze_self_evolving(home)

    # ===== KN LLM Judge：知识导航召回质量评估（KN_MIN_SCORE 调优主反馈）=====
    #   since = CN 昨天 00:00 (CST = UTC+8)，until = CN 今天 00:00
    #   形成 CN 自然日切窗，trace.log 存储 UTC 时间戳，过滤时自动匹配。
    #   注意：只对 scheduled 报告（主流程）执行；catch-up 只跑轻量分析，避免重复 judge 耗 token
    import datetime as _dt
    kn_judge_m: dict[str, Any] = {}
    try:
        _cn_utc_offset = _dt.timedelta(hours=8)
        _cn_now = _dt.datetime.now(_dt.timezone.utc) + _cn_utc_offset
        _cn_today = _cn_now.replace(hour=0, minute=0, second=0, microsecond=0)
        _cn_yesterday = _cn_today - _dt.timedelta(days=1)
        # CN 日期 → UTC 时间戳用于过滤 trace.log（存储 UTC timestamp）
        _since = (_cn_yesterday - _cn_utc_offset).isoformat()
        _until = (_cn_today - _cn_utc_offset).isoformat()
        kn_judge_m = run_judge_within_window(home, _since, _until)
    except Exception as _knj_exc:
        # judge 异常不影响整体报告，只在 kn_judge_m 里挂 error 字段
        kn_judge_m = {"kn_judge_error": f"exception:{type(_knj_exc).__name__}:{str(_knj_exc)[:80]}"}

    # ===== 参数优化现状（读取 auto-tuner-log / state）=====
    log_file = home / DATA_FLYWHEEL_SUBPATH / "auto-tuner-log.jsonl"
    state_file = home / DATA_FLYWHEEL_SUBPATH / "auto-tuner-state.json"
    try:
        param_tuning_m = summarize_param_tuning(log_file, state_file)
    except Exception as _pt_exc:
        param_tuning_m = {"error": f"{type(_pt_exc).__name__}:{str(_pt_exc)[:80]}"}

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
                  cluster_issues + kn_issues + memory_issues + se_issues +
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
    all_state_files = list(cron_state_dir.glob("*.json")) if cron_state_dir.is_dir() else []
    excluded_count = sum(1 for f in all_state_files if f.stem in EXCLUDED_STATE_FILES)
    zombie_total = len(zombie_files) + excluded_count
    L.append(f"**Core cron tasks**: {len(cron_table)} 个（排除 {excluded_count} 个基础设施 + {len(zombie_files)} 个孤儿 state）")
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
    # 合并 runner-summary（阶段 0 登记的内部执行任务），覆盖/补充 cron_table
    runner_summary = load_runner_summary(home)
    runner_tasks_override: dict[str, dict] = {}
    if isinstance(runner_summary, dict) and runner_summary.get("stages"):
        for stage_key, stage_info in runner_summary["stages"].items():
            if not isinstance(stage_info, dict):
                continue
            for t in stage_info.get("tasks", []) or []:
                if not isinstance(t, dict):
                    continue
                cname = t.get("cron_name", "")
                if cname:
                    runner_tasks_override[cname] = t

    L.append("## 📊 任务可靠性")
    L.append("")
    L.append("| 任务 | 飞轮 | 状态 | 上次运行 | 耗时 | 耗时异常 |")
    L.append("|------|------|------|---------|------|---------|")
    all_names = sorted(set(cron_table.keys()) | set(runner_tasks_override.keys()))
    for name in all_names:
        # Runner 登记的内部任务优先（替换 cron-state 中的旧数据）
        override = runner_tasks_override.get(name)
        if override:
            raw_status = override.get("status", "internal_running")
            # 已执行的任务直接显示实际状态
            if raw_status == "done":
                status_disp = "success"
                icon = "✅"
            elif raw_status == "failed":
                status_disp = "fail"
                icon = "❌"
            elif raw_status == "skipped":
                status_disp = "skipped"
                icon = "⚪"
            else:
                status_disp = "internal_running"
                icon = "🔄"
            fw = override.get("flywheel") or _CRON_TO_FLYWHEEL.get(name, name)
            run_short = runner_summary.get("generated_at", "")[:16] or "本次"
            elapsed_str = "—"
            note = override.get("note", "")
            loc = override.get("exec_location", "")
            ann = f"本次内部执行 @ {loc}" if loc else "本次内部执行"
            if note:
                ann = f"{ann}｜{note}"
        else:
            info = cron_table[name]
            status_disp = info["status"]
            icon = "✅" if status_disp == "success" else "❌" if status_disp == "fail" else "⚪"
            fw = _CRON_TO_FLYWHEEL.get(name, name)
            run_short = (info["run_at"] or "—")[:16] if info["run_at"] else "—"
            elapsed_str = f"{info['elapsed']}s" if info['elapsed'] else "—"
            ann = elapsed_ann.get(name, "—")
        status_text = {"internal_running": "内部执行中", "success": "成功", "fail": "失败", "skipped": "跳过"}.get(status_disp, status_disp)
        L.append(f"| {name} | {fw} | {icon} {status_text} | {run_short} | {elapsed_str} | {ann} |")
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
        L.append(f"- 路由总次数: {router_m['total_masks']}（真实 {router_m['real_total']}，eval 测试 {router_m['eval_total']}）| "
                 f"样本量: {'充足' if router_m['real_total'] >= TH['min_sample_size'] else '⚠️ 偏少'}")
        L.append(f"- 全关率: {router_m['full_off_pct']}% ({router_m['full_off']}/{router_m['real_total']}) | "
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
        # Router 决策质量（confidence / fallback_reason，来自 router_mask 事件 meta）
        _conf_avg = router_m.get("router_confidence_avg")
        _conf_disp = f"{_conf_avg:.3f}" if _conf_avg is not None else "N/A"
        L.append(f"- 决策置信度: {_conf_disp} | 低置信度率: {router_m.get('router_confidence_low_pct', 0)}% | "
                 f"决策 fallback 率: {router_m.get('router_fallback_pct', 0)}% "
                 f"({router_m.get('router_fallback_total', 0)} 次) "
                 f"原因: {router_m.get('router_fallback_reasons') or '无'}")
        L.append("")
        L.append("**Token 实际消耗（纯观测，无预算控制）:**")
        if token_m.get("status") == "no_data":
            L.append("- 无 token_usage 数据")
        else:
            share = token_m.get("source_share_pct", {})
            L.append(f"- 事件数: {token_m['event_count']} | 累计消耗: {token_m['grand_total_tokens']:,} tokens")
            L.append("")
            L.append("| 来源 | avg | p50 | p90 | max | 占比 |")
            L.append("|------|-----|-----|-----|-----|------|")
            for key, label in (("hs", "Hindsight"), ("sag", "SAG"),
                               ("kt", "知识树"), ("skill", "Skill")):
                st = token_m[f"{key}_stats"]
                L.append(f"| {label} | {st['avg']} | {st['p50']} | {st['p90']} | "
                         f"{st['max']} | {share.get(key, 0)}% |")
            ts = token_m["total_stats"]
            L.append(f"| **合计** | {ts['avg']} | {ts['p50']} | {ts['p90']} | {ts['max']} | 100% |")
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

    # --- Router: KN LLM Judge 质量评估（KN_MIN_SCORE 调优主反馈）---
    L.append("")
    L.append("**KN LLM Judge 召回质量评估 (LLM 评估, 200 样本):**")
    _jerr = kn_judge_m.get("kn_judge_error")
    _jn = kn_judge_m.get("kn_judge_sample_count", 0)
    if _jerr and not _jn:
        L.append(f"- ⚠️ 本轮未执行 judge: `{_jerr}`（样本不足或配置未启用，反馈链路用 kn_avg_score 兜底）")
    else:
        _jrate = kn_judge_m.get("kn_judge_relevant_rate", 0)
        _javg = kn_judge_m.get("kn_judge_avg_relevance", 0)
        _jci_lo = kn_judge_m.get("kn_judge_ci_lo")
        _jci_hi = kn_judge_m.get("kn_judge_ci_hi")
        _jfb = "（kn_avg_score 兜底）" if kn_judge_m.get("kn_judge_fallback") else ""
        L.append(f"- 样本量: {_jn} 条 {_jfb}")
        if isinstance(_jrate, (int, float)):
            L.append(f"- 相关率 (评分 ≥ 0.5): {round(_jrate * 100, 1)}%")
        if isinstance(_javg, (int, float)):
            L.append(f"- 平均 relevance: {round(float(_javg), 4)}")
        # mask 级相关性（参数→其影响那一路质量的因果绑定依据）
        for _short, _label in (("h", "hindsight"), ("kt", "knowledge_tree"), ("sag", "sag")):
            _sc = kn_judge_m.get(f"kn_judge_sample_count_{_short}", 0)
            _rt = kn_judge_m.get(f"kn_judge_relevant_rate_{_short}")
            if isinstance(_rt, (int, float)):
                _trusted = "✓" if int(_sc) >= 12 else f"✗(样本{_sc})"
                L.append(f"- 相关性[{_label}]: {round(_rt * 100, 1)}% (样本 {_sc} {_trusted})")
        if isinstance(_jci_lo, (int, float)) and isinstance(_jci_hi, (int, float)) and float(_jci_hi) > float(_jci_lo):
            L.append(f"- Bootstrap 95% CI: [{round(float(_jci_lo), 4)}, {round(float(_jci_hi), 4)}]")
        if _jerr:
            L.append(f"- 📝 信息: `{_jerr}`")

    # --- 参数优化现状（与其他参数优化模式一致）---
    L.append("")
    if isinstance(param_tuning_m, dict) and param_tuning_m.get("error"):
        L.append(f"**参数优化现状**：⚠️ 读取失败: `{param_tuning_m['error']}`")
    elif isinstance(param_tuning_m, dict):
        L.append("**参数优化现状 (Auto-Tuner):**")
        params = param_tuning_m.get("params") or {}
        if params:
            L.append("")
            L.append("| 参数 | 当前值 | 区间 | 步长 | 初值→当前 | 历史 | 状态 | 说明 |")
            L.append("|------|--------|------|------|-----------|------|------|------|")
            for pname in ["KN_MIN_SCORE", "KN_MAX_RESULTS", "KN_MAX_TEXT_LENGTH",
                          "KN_TEMPORAL_HALFLIFE", "KN_TEMPORAL_FLOOR_WEIGHT",
                          "KN_SAG_MAX_INJECT", "KN_SAG_SEARCH_TOP_K", "KN_SAG_MIN_SCORE",
                          "KN_SAG_POINTER_THRESHOLD",
                          "KN_CROSS_DOMAIN_DEDUP_DEMOTE_FACTOR",
                          "KN_LAMBDA_MRR", "KN_SCORE_SPAN_TOP3_THRESHOLD", "KN_SCORE_SPAN_HALF_THRESHOLD",
                          "KN_CAUSAL_BOOST_ALPHA", "KN_CAUSAL_BOOST_CAP"]:
                info = params.get(pname)
                if not info:
                    continue
                init_v = info.get("initial_value")
                impr = info.get("improvement_since_initial")
                hist = info.get("history") or []
                state_tags = []
                if info.get("locked"):
                    state_tags.append("🔒 收敛")
                if info.get("suspended"):
                    state_tags.append("⏸️ 暂停")
                if info.get("converged") and not info.get("locked") and not info.get("suspended"):
                    state_tags.append("🎯 近收敛")
                pending = "⏳待重启" if info.get("last_status") == "pending_restart" else info.get("last_status", "")
                state_line = " ".join(state_tags + ([pending] if pending else [])) or "调优中"
                hist_line = f"{len(hist)} 条"
                if info.get("state_counts"):
                    sc = info["state_counts"]
                    hist_line += (
                        f"（no_change={sc.get('no_change_count', 0)}"
                        f" / degrad={sc.get('degradation_count', 0)}）"
                    )
                impr_str = (
                    f"{init_v} → {info.get('current')}"
                    if init_v is not None else f"— → {info.get('current')}"
                )
                if impr is not None:
                    try:
                        sign = "+" if float(impr) >= 0 else ""
                        impr_str += f" ({sign}{round(float(impr), 4)})"
                    except (TypeError, ValueError):
                        pass
                prng = info.get("range") or ["", ""]
                L.append(
                    f"| {pname} | {info.get('current')} | "
                    f"[{prng[0]}, {prng[1]}] | {info.get('step')} | "
                    f"{impr_str} | {hist_line} | {state_line} | "
                    f"{(hist[-1]['reason'] if hist else '无调优历史')} |"
                )
            L.append("")
        else:
            L.append("- 暂无参数调优历史（首次运行会自动积累）")

        if param_tuning_m.get("any_pending_restart"):
            L.append("- ⚠️ 存在 pending_restart 调优记录，请按飞书通知重启 hermes-gateway 使生效")
        _last = param_tuning_m.get("last_tune") or {}
        if _last and _last.get("parameter"):
            L.append(
                f"- 最近一次调优: {_last.get('date', '')} "
                f"{_last.get('parameter')}: {_last.get('old')} → {_last.get('new')} "
                f"({_last.get('status', '')})"
            )
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

    # 能力飞轮 / Self-Evolving（F-5 + B 自动写回闭环）
    L.append("### 能力飞轮 / Self-Evolving")
    L.append("")
    if se_m.get("status") == "no_data":
        L.append("- 暂无 Self-Evolving 产出（尚未运行，或输出目录/写回块均为空）")
    else:
        L.append(f"- 最近运行: {se_m.get('last_run') or '—'}")
        L.append(f"- 驱动产出文件: {se_m.get('output_files', 0)}（声明写回 {se_m.get('applied_from_output', 0)}，精炼未落地 {se_m.get('refined_not_applied', 0)}）")
        L.append(f"- **实际写回 SKILL.md**: {se_m.get('se_applied_skill_count', 0)} 个 skill | 最近写回: {se_m.get('last_se_applied') or '—'}")
        if se_m.get("se_applied_skills"):
            L.append(f"- 已进化 skill: {', '.join(se_m['se_applied_skills'][:10])}")
        if se_m.get("ledger_deployed"):
            L.append(f"- 统一账本(ledger): {se_m.get('ledger_events', 0)} 事件（applied={se_m.get('ledger_applied', 0)}, blocked={se_m.get('ledger_blocked', 0)}）")
        else:
            L.append("- 📝 统一账本(ledger)未部署：applied/blocked 计数缺失，建议将 scripts/common 纳入部署清单（F-1 待部署）")
    L.append("")

    # 全局错误
    L.append("### 全局错误监控")
    L.append("")
    if error_m.get("status") == "no_data":
        L.append("- 无 errors.log 数据")
    else:
        filtered = error_m.get("filtered_errors", 0)
        L.append(f"- 当日问题日志: {error_m.get('date_logs', 0)} 条 "
                 f"(ERROR {error_m.get('error_count', 0)} | WARNING {error_m.get('warning_count', 0)})")
        if filtered > 0:
            L.append(f"- 已过滤重启级联噪音: {filtered} 条")
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
        # Token 纯观测（不参与 auto-tuner 反馈，仅供趋势追踪）
        "token_total_avg": token_m.get("total_stats", {}).get("avg", 0),
        "token_skill_share_pct": token_m.get("source_share_pct", {}).get("skill", 0),
        "skill_f1": skill_m.get("avg_f1", 0),
        "skill_active_count": skill_usage_m.get("active_count", 0),
        # used_count = 使用过（use_count>0）的不同 active skill 数量
        # total_uses = 所有 active skill 的 use_count 之和（总调用次数）
        "skill_used_count": skill_usage_m.get("used_count", 0),
        "skill_total_uses": skill_usage_m.get("total_uses", 0),
        "kn_unknown_pct": kn_m.get("unknown_dim_pct", 0),
        "kn_avg_score": sum(s["avg_score"] for s in kn_m.get("dim_summary", {}).values()) / max(len(kn_m.get("dim_summary", {})), 1) if kn_m.get("dim_summary") else 0,
        # KN LLM Judge：调优主反馈（无 judge 时为空，auto-tuner 会用 kn_avg_score 兜底）
        "kn_judge_sample_count": kn_judge_m.get("kn_judge_sample_count", 0),
        "kn_judge_relevant_rate": kn_judge_m.get("kn_judge_relevant_rate"),
        "kn_judge_avg_relevance": kn_judge_m.get("kn_judge_avg_relevance"),
        # mask 级 judge：各召回来源(h/kt/sag)独立相关性，供参数→其影响那一路质量的因果绑定
        "kn_judge_sample_count_h": kn_judge_m.get("kn_judge_sample_count_h", 0),
        "kn_judge_relevant_rate_h": kn_judge_m.get("kn_judge_relevant_rate_h"),
        "kn_judge_avg_relevance_h": kn_judge_m.get("kn_judge_avg_relevance_h"),
        "kn_judge_sample_count_kt": kn_judge_m.get("kn_judge_sample_count_kt", 0),
        "kn_judge_relevant_rate_kt": kn_judge_m.get("kn_judge_relevant_rate_kt"),
        "kn_judge_avg_relevance_kt": kn_judge_m.get("kn_judge_avg_relevance_kt"),
        "kn_judge_sample_count_sag": kn_judge_m.get("kn_judge_sample_count_sag", 0),
        "kn_judge_relevant_rate_sag": kn_judge_m.get("kn_judge_relevant_rate_sag"),
        "kn_judge_avg_relevance_sag": kn_judge_m.get("kn_judge_avg_relevance_sag"),
        "kn_judge_fallback": bool(kn_judge_m.get("kn_judge_fallback", False)),
        # 参数优化状态（摘要，给趋势图/后续报告读）
        "param_tune_active_count": param_tuning_m.get("active_count", 0) if isinstance(param_tuning_m, dict) else 0,
        "param_tune_any_pending": bool(param_tuning_m.get("any_pending_restart", False)) if isinstance(param_tuning_m, dict) else False,
        "cluster_noise_rate": cluster_m.get("noise_rate", 0),
        "kt_orphan_pct": kt_m.get("orphan_pct", 0),
        "memory_usage_pct": memory_m.get("memory_usage_pct", 0),
        "memory_user_usage_pct": memory_m.get("user_usage_pct", 0),
        "memory_hindsight_count": memory_m.get("total_hindsight", 0),
        "memory_compress_count": memory_m.get("total_compress", 0),
        "error_count": error_m.get("error_count", 0),
        "warning_count": error_m.get("warning_count", 0),
        # 能力飞轮 / Self-Evolving（F-5 + B）
        "se_output_files": se_m.get("output_files", 0),
        "se_applied_skill_count": se_m.get("se_applied_skill_count", 0),
        "se_last_run": se_m.get("last_run"),
        "se_last_se_applied": se_m.get("last_se_applied"),
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
