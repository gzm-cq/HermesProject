"""parse_trace_log / append_daily_summary / load_daily_summary 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

from flywheel_health_report.parsers import (
    parse_trace_log,
    append_daily_summary,
    load_daily_summary,
    parse_cron_jobs_json,
)


# ========== parse_trace_log ==========

class TestParseTraceLog:
    """测试 trace.log 解析与事件 whitelist 覆盖。"""

    def test_returns_empty_dict_when_file_missing(self, tmp_path: Path) -> None:
        """文件不存在时返回所有 whitelist 键的空列表。"""
        result = parse_trace_log(tmp_path / "missing.log")
        assert isinstance(result, dict)
        assert len(result) > 0
        # 所有关键事件键必须存在（即使为空）
        for key in ("router_mask", "recall_success", "recall_error",
                    "recall_empty", "hindsight_fail_kt_fallback",
                    "token_usage", "recall_sag", "sag_merge"):
            assert key in result
            assert result[key] == []

    def test_parses_all_whitelist_events(self, tmp_path: Path) -> None:
        """覆盖全部 whitelist 事件的解析。"""
        log_path = tmp_path / "trace.log"
        entries = [
            {"timestamp": "2026-07-10T10:00:00Z", "event": "router_mask", "mask": {"h": True, "kt": True, "s": False, "sag": False}},
            {"timestamp": "2026-07-10T10:01:00Z", "event": "recall_success", "latency_ms": 120, "score_stats": {"avg": 0.8}},
            {"timestamp": "2026-07-10T10:02:00Z", "event": "recall_empty_results"},
            {"timestamp": "2026-07-10T10:03:00Z", "event": "recall_error", "error": "RuntimeError: API down"},
            {"timestamp": "2026-07-10T10:04:00Z", "event": "recall_empty"},
            {"timestamp": "2026-07-10T10:05:00Z", "event": "hindsight_fail_kt_fallback", "kt_count": 2},
            {"timestamp": "2026-07-10T10:06:00Z", "event": "recall_timeout"},
            {"timestamp": "2026-07-10T10:07:00Z", "event": "multi_hop_expand"},
            {"timestamp": "2026-07-10T10:08:00Z", "event": "token_usage", "hs_tokens": 72, "sag_tokens": 0, "kt_tokens": 340, "skill_tokens": 4266, "total_tokens": 4678},
            {"timestamp": "2026-07-10T10:09:00Z", "event": "recall_sag", "count": 3},
            {"timestamp": "2026-07-10T10:10:00Z", "event": "sag_merge", "count": 2},
            # 以下事件已从 whitelist 移除（无 analyze 函数消费 / 冗余日志）
            {"timestamp": "2026-07-10T10:11:00Z", "event": "sag_recall", "count": 3},
            {"timestamp": "2026-07-10T10:12:00Z", "event": "recall_hindsight"},
            {"timestamp": "2026-07-10T10:13:00Z", "event": "recall_knowledge_tree"},
            {"timestamp": "2026-07-10T10:14:00Z", "event": "recall_skill"},
            {"timestamp": "2026-07-10T10:15:00Z", "event": "skip_router_all_off"},
            {"timestamp": "2026-07-10T10:16:00Z", "event": "unknown_event"},  # 不在 whitelist，应被忽略
        ]
        log_path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        result = parse_trace_log(log_path, filter_dates=["2026-07-10"])

        # 所有 whitelist 事件应有 1 条
        assert len(result["router_mask"]) == 1
        assert len(result["recall_success"]) == 1
        assert len(result["recall_empty_results"]) == 1
        assert len(result["recall_error"]) == 1
        assert len(result["recall_empty"]) == 1
        assert len(result["hindsight_fail_kt_fallback"]) == 1
        assert len(result["recall_timeout"]) == 1
        assert len(result["multi_hop_expand"]) == 1
        assert len(result["token_usage"]) == 1
        assert len(result["recall_sag"]) == 1
        assert len(result["sag_merge"]) == 1
        # 已移除的事件不应出现在 result 中
        for removed in ("recall_hindsight", "recall_knowledge_tree", "recall_skill", "skip_router_all_off", "sag_recall"):
            assert removed not in result, f"{removed} 应已从 whitelist 移除"
        # unknown_event 不应出现在任何键
        assert "unknown_event" not in result

    def test_filter_date_excludes_other_days(self, tmp_path: Path) -> None:
        """filter_dates 多日期过滤。"""
        log_path = tmp_path / "trace.log"
        entries = [
            {"timestamp": "2026-07-09T23:59:00Z", "event": "router_mask"},
            {"timestamp": "2026-07-10T00:01:00Z", "event": "router_mask"},
            {"timestamp": "2026-07-10T23:59:00Z", "event": "router_mask"},
            {"timestamp": "2026-07-11T00:01:00Z", "event": "router_mask"},
        ]
        log_path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        # 单日期过滤
        result = parse_trace_log(log_path, filter_dates=["2026-07-10"])
        assert len(result["router_mask"]) == 2  # 只保留 7-10 当天

        # 多日期过滤（2 天窗口）
        result2 = parse_trace_log(log_path, filter_dates=["2026-07-10", "2026-07-11"])
        assert len(result2["router_mask"]) == 3  # 保留 7-10（2 条）+ 7-11（1 条）

    def test_skips_blank_and_invalid_lines(self, tmp_path: Path) -> None:
        """空行和无效 JSON 应被跳过。"""
        log_path = tmp_path / "trace.log"
        log_path.write_text(
            "\n".join([
                "",
                "not json",
                json.dumps({"timestamp": "2026-07-10T10:00:00Z", "event": "router_mask"}),
                "  ",
            ]),
            encoding="utf-8",
        )
        result = parse_trace_log(log_path, filter_dates=["2026-07-10"])
        assert len(result["router_mask"]) == 1


# ========== append/load_daily_summary ==========

class TestDailySummary:
    """测试 daily-summary JSONL 持久化。"""

    def test_append_creates_file_and_dedup(self, tmp_path: Path) -> None:
        """同日条目应替换而非追加。"""
        append_daily_summary(tmp_path, {"date": "2026-07-10", "p0_count": 1})
        append_daily_summary(tmp_path, {"date": "2026-07-10", "p0_count": 2})  # 替换
        records = load_daily_summary(tmp_path)
        assert len(records) == 1
        assert records[0]["p0_count"] == 2

    def test_append_keeps_multiple_days(self, tmp_path: Path) -> None:
        """不同日期应保留多条。"""
        append_daily_summary(tmp_path, {"date": "2026-07-09", "p0_count": 0})
        append_daily_summary(tmp_path, {"date": "2026-07-10", "p0_count": 1})
        records = load_daily_summary(tmp_path)
        assert len(records) == 2

    def test_append_trims_to_30_records(self, tmp_path: Path) -> None:
        """超过 30 条时只保留最后 30 条。"""
        for i in range(35):
            append_daily_summary(tmp_path, {"date": f"2026-06-{i+1:02d}", "p0_count": i})
        records = load_daily_summary(tmp_path)
        assert len(records) == 30

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        """文件不存在时返回空列表。"""
        assert load_daily_summary(tmp_path) == []


# ========== parse_cron_jobs_json ==========

class TestParseCronJobsJson:
    """jobs.json 兜底解析（无 cron-state 文件时）。"""

    def test_null_last_run_at_coerced_to_dash(self, tmp_path: Path) -> None:
        """last_run_at 为 null 时不应返回 None（否则渲染任务可靠性表会 TypeError）。"""
        home = tmp_path / "h"
        cron = home / "cron"
        cron.mkdir(parents=True)
        jobs = {
            "jobs": [
                {"name": "dream-daily", "enabled": True,
                 "last_status": "ok", "last_run_at": None},
            ]
        }
        (cron / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        res = parse_cron_jobs_json(home, {})
        assert "dream-daily" in res
        assert res["dream-daily"]["run_at"] == "—"
        assert res["dream-daily"]["status"] == "success"

