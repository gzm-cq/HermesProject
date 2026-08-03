from __future__ import annotations

from datetime import datetime, timezone

from ..config import TH
from ..parsers import _load_json, _save_json


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
