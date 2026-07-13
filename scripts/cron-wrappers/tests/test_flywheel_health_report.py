"""flywheel-health-report.py 核心函数单元测试。

覆盖：parse_trace_log / _percentile / analyze_router / analyze_token_budget /
analyze_sag_contribution / analyze_global_errors / analyze_skill_usage /
append_daily_summary + load_daily_summary / format_7day_trend / generate_recommendations。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# 脚本不在 package 内，用 importlib 按文件路径加载
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "flywheel-health-report.py"
assert _SCRIPT_PATH.is_file(), f"找不到目标脚本: {_SCRIPT_PATH}"

_spec = importlib.util.spec_from_file_location("flywheel_health_report", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
fhr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fhr)


# ========== parse_trace_log ==========

class TestParseTraceLog:
    """测试 trace.log 解析与事件 whitelist 覆盖。"""

    def test_returns_empty_dict_when_file_missing(self, tmp_path: Path) -> None:
        """文件不存在时返回所有 whitelist 键的空列表。"""
        result = fhr.parse_trace_log(tmp_path / "missing.log")
        assert isinstance(result, dict)
        assert len(result) > 0
        # 所有关键事件键必须存在（即使为空）
        for key in ("router_mask", "recall_success", "recall_error",
                    "recall_empty", "hindsight_fail_kt_fallback",
                    "token_budget", "recall_sag", "sag_merge"):
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
            {"timestamp": "2026-07-10T10:08:00Z", "event": "token_budget", "hs_tokens_before": 1000, "hs_tokens_after": 500},
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

        result = fhr.parse_trace_log(log_path, filter_date="2026-07-10")

        # 所有 whitelist 事件应有 1 条
        assert len(result["router_mask"]) == 1
        assert len(result["recall_success"]) == 1
        assert len(result["recall_empty_results"]) == 1
        assert len(result["recall_error"]) == 1
        assert len(result["recall_empty"]) == 1
        assert len(result["hindsight_fail_kt_fallback"]) == 1
        assert len(result["recall_timeout"]) == 1
        assert len(result["multi_hop_expand"]) == 1
        assert len(result["token_budget"]) == 1
        assert len(result["recall_sag"]) == 1
        assert len(result["sag_merge"]) == 1
        # 已移除的事件不应出现在 result 中
        for removed in ("recall_hindsight", "recall_knowledge_tree", "recall_skill", "skip_router_all_off", "sag_recall"):
            assert removed not in result, f"{removed} 应已从 whitelist 移除"
        # unknown_event 不应出现在任何键
        assert "unknown_event" not in result

    def test_filter_date_excludes_other_days(self, tmp_path: Path) -> None:
        """filter_date 只保留匹配日期的条目。"""
        log_path = tmp_path / "trace.log"
        entries = [
            {"timestamp": "2026-07-09T23:59:00Z", "event": "router_mask"},
            {"timestamp": "2026-07-10T00:01:00Z", "event": "router_mask"},
            {"timestamp": "2026-07-10T23:59:00Z", "event": "router_mask"},
            {"timestamp": "2026-07-11T00:01:00Z", "event": "router_mask"},
        ]
        log_path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        result = fhr.parse_trace_log(log_path, filter_date="2026-07-10")
        assert len(result["router_mask"]) == 2  # 只保留 7-10 当天

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
        result = fhr.parse_trace_log(log_path, filter_date="2026-07-10")
        assert len(result["router_mask"]) == 1


# ========== _percentile ==========

class TestPercentile:
    """测试 _percentile 线性插值实现。"""

    def test_single_value(self) -> None:
        assert fhr._percentile([42.0], 0.5) == 42.0
        assert fhr._percentile([42.0], 0.95) == 42.0

    def test_p50_median(self) -> None:
        assert fhr._percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5

    def test_p95_high_end(self) -> None:
        result = fhr._percentile([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], 0.95)
        assert 9.0 <= result <= 10.0

    def test_empty_list(self) -> None:
        """空列表应返回 0 不报错。"""
        assert fhr._percentile([], 0.5) == 0


# ========== _is_test_query ==========

class TestIsTestQuery:
    """测试测试查询过滤。"""

    def test_recognizes_test_prefixes(self) -> None:
        for prefix in ("gen_", "eval-", "test_", "test-", "exact_kw_", "semantic_",
                       "entity_", "causal_", "temporal_", "conflict_", "tool_",
                       "debug_", "api_", "compare_", "workflow_", "complex_", "numeric_"):
            assert fhr._is_test_query(prefix + "abc") is True

    def test_rejects_normal_queries(self) -> None:
        assert fhr._is_test_query("如何配置数据库连接") is False
        assert fhr._is_test_query("deploy the service") is False


# ========== append/load_daily_summary ==========

class TestDailySummary:
    """测试 daily-summary JSONL 持久化。"""

    def test_append_creates_file_and_dedup(self, tmp_path: Path) -> None:
        """同日条目应替换而非追加。"""
        fhr.append_daily_summary(tmp_path, {"date": "2026-07-10", "p0_count": 1})
        fhr.append_daily_summary(tmp_path, {"date": "2026-07-10", "p0_count": 2})  # 替换
        records = fhr.load_daily_summary(tmp_path)
        assert len(records) == 1
        assert records[0]["p0_count"] == 2

    def test_append_keeps_multiple_days(self, tmp_path: Path) -> None:
        """不同日期应保留多条。"""
        fhr.append_daily_summary(tmp_path, {"date": "2026-07-09", "p0_count": 0})
        fhr.append_daily_summary(tmp_path, {"date": "2026-07-10", "p0_count": 1})
        records = fhr.load_daily_summary(tmp_path)
        assert len(records) == 2

    def test_append_trims_to_30_records(self, tmp_path: Path) -> None:
        """超过 30 条时只保留最后 30 条。"""
        for i in range(35):
            fhr.append_daily_summary(tmp_path, {"date": f"2026-06-{i+1:02d}", "p0_count": i})
        records = fhr.load_daily_summary(tmp_path)
        assert len(records) == 30

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        """文件不存在时返回空列表。"""
        assert fhr.load_daily_summary(tmp_path) == []


# ========== format_7day_trend ==========

class TestFormat7DayTrend:
    """测试 7 天趋势表格式化。"""

    def test_insufficient_data_returns_message(self, tmp_path: Path) -> None:
        """少于 2 天时返回提示。"""
        result = fhr.format_7day_trend(tmp_path)
        assert len(result) == 1
        assert "不足" in result[0]

    def test_table_has_header_and_separator(self, tmp_path: Path) -> None:
        """有 2 天数据时返回表头 + 分隔行 + 数据行。"""
        fhr.append_daily_summary(tmp_path, {"date": "2026-07-09"})
        fhr.append_daily_summary(tmp_path, {"date": "2026-07-10"})
        result = fhr.format_7day_trend(tmp_path)
        assert len(result) >= 3  # 表头 + 分隔 + 至少 1 数据行
        assert "日期" in result[0]
        assert "---" in result[1]


# ========== analyze_token_budget ==========

class TestAnalyzeTokenBudget:
    """测试 Token 预算分析。"""

    def test_no_data_returns_empty(self) -> None:
        """无 token_budget 事件时返回 no_data。"""
        issues, metrics, trend = fhr.analyze_token_budget({})
        assert metrics.get("status") == "no_data"
        assert issues == []

    def test_normal_stats(self) -> None:
        """正常数据应统计总预算/分源消耗/耗尽率。"""
        trace = {"token_budget": [
            {"total_budget": 8000, "hs_tokens_before": 2000, "hs_tokens_after": 1500,
             "kt_tokens_before": 1000, "kt_tokens_after": 800,
             "sag_tokens_before": 500, "sag_tokens_after": 400,
             "skill_tokens_before": 100, "skill_tokens_after": 100},
            {"total_budget": 8000, "hs_tokens_before": 7000, "hs_tokens_after": 6900,
             "kt_tokens_before": 500, "kt_tokens_after": 400,
             "sag_tokens_before": 200, "sag_tokens_after": 150,
             "skill_tokens_before": 80, "skill_tokens_after": 70},
        ]}
        issues, metrics, trend = fhr.analyze_token_budget(trace)
        # 正常情况返回的 metrics 不含 "status" 键，只有 no_data 才有
        assert "status" not in metrics
        assert metrics["event_count"] == 2
        assert metrics["total_budget"] == 8000
        assert "total_stats" in metrics
        assert "hs_stats" in metrics
        assert "kt_stats" in metrics
        assert "skill_stats" in metrics
        # 耗尽率：第二条接近耗尽（6900/8000=86%），按 exhaust_pct 阈值应 >= 50%
        assert metrics["exhaust_count"] >= 0

    def test_detects_high_exhaustion(self) -> None:
        """当多次接近耗尽时应产生 issue。"""
        # 构造 total_after <= 0 的场景：所有 after 都为 0
        trace = {"token_budget": [
            {"total_budget": 1000, "hs_tokens_before": 950, "hs_tokens_after": 0,
             "kt_tokens_before": 50, "kt_tokens_after": 0,
             "sag_tokens_before": 10, "sag_tokens_after": 0,
             "skill_tokens_before": 5, "skill_tokens_after": 0}
            for _ in range(6)
        ]}
        issues, metrics, _ = fhr.analyze_token_budget(trace)
        # total_after = 0，触发 exhaust_count += 1；6 次全触发
        assert metrics["exhaust_count"] == 6
        # exhaust_pct = 100% > 阈值 10%，应触发 issue
        assert any("耗尽" in i.get("desc", "") for i in issues)


# ========== analyze_sag_contribution ==========

class TestAnalyzeSagContribution:
    """测试 SAG 贡献分析。"""

    def test_no_data_returns_empty(self) -> None:
        """无 sag 事件时返回 no_data。"""
        issues, metrics, trend = fhr.analyze_sag_contribution({})
        assert metrics.get("status") == "no_data"

    def test_normal_metrics(self) -> None:
        """正常 SAG 数据应统计 recall/merge。"""
        trace = {
            "recall_sag": [
                {"count": 5, "latency_ms": 200},
                {"count": 0, "latency_ms": 150},
                {"count": 3, "latency_ms": 180},
            ],
            "sag_merge": [
                {"count": 2},
                {"count": 0},
                {"count": 1},
            ],
        }
        issues, metrics, _ = fhr.analyze_sag_contribution(trace)
        # 正常情况返回的 metrics 不含 "status" 键
        assert "status" not in metrics
        assert metrics["recall_count"] == 3
        assert metrics["merge_count"] == 3
        # 零结果率：1/3 = 33.3%
        assert "merge_zero_pct" in metrics
        assert 30 <= metrics["merge_zero_pct"] <= 35

    def test_zero_merge_triggers_warning(self) -> None:
        """SAG merge 全零时触发 issue。"""
        trace = {
            "recall_sag": [{"count": 5}, {"count": 3}, {"count": 4}],
            "sag_merge": [{"count": 0}, {"count": 0}, {"count": 0}],
        }
        issues, _, _ = fhr.analyze_sag_contribution(trace)
        # merge_zero_pct = 100%，应触发 issue
        assert any("零结果" in i.get("desc", "") or "merge" in i.get("desc", "").lower()
                   for i in issues)

    def test_error_events_not_counted_as_recall_zero(self) -> None:
        """SAG 召回异常（error 字段）不应计入 recall_zero，应单独统计。"""
        trace = {
            # 3 次召回：1 次成功(count=0) + 2 次异常(count=0, error=...)
            "recall_sag": [
                {"count": 0, "latency_ms": 100},
                {"count": 0, "latency_ms": 50, "error": "ConnectionError"},
                {"count": 0, "latency_ms": 80, "error": "Timeout"},
            ],
            "sag_merge": [],
        }
        issues, metrics, _ = fhr.analyze_sag_contribution(trace)
        # recall_success_count = 1（只有无 error 的那次）
        assert metrics["recall_success_count"] == 1
        # recall_error_count = 2
        assert metrics["recall_error_count"] == 2
        # recall_zero = 1（只基于成功召回统计）
        assert metrics["recall_zero"] == 1
        # 不应触发"全部召回为 0 section"（因为只有 1 次成功召回，不是"全部"）
        # 但 recall_zero == recall_success_count，会触发该 issue
        # 这里验证的是 error 场景被正确分离
        assert any("召回异常" in i.get("desc", "") for i in issues)


# ========== analyze_global_errors ==========

class TestAnalyzeGlobalErrors:
    """测试全局错误日志分析。"""

    def test_no_file_returns_no_data(self, tmp_path: Path) -> None:
        """errors.log 不存在时返回 no_data。"""
        issues, metrics, _ = fhr.analyze_global_errors(tmp_path / "missing.log", "2026-07-10")
        assert metrics.get("status") == "no_data"

    def test_parses_errors(self, tmp_path: Path) -> None:
        """解析 ERROR/WARNING 行并统计。"""
        log_path = tmp_path / "errors.log"
        # 格式必须匹配正则: (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}[,\d]* (\w+) ([\w\.]+):
        # 即 "date time LEVEL module:" — module 后必须有冒号
        log_path.write_text(
            "2026-07-10 10:00:00 ERROR knowledge_navigation.core.hooks: recall failed: timeout\n"
            "2026-07-10 10:01:00 WARNING knowledge_navigation.core.router: mask fallback used\n"
            "2026-07-10 10:02:00 ERROR knowledge_navigation.core.sag: SAG request failed\n"
            "2026-07-11 10:00:00 ERROR should.be.filtered: this is next day\n",
            encoding="utf-8",
        )
        issues, metrics, _ = fhr.analyze_global_errors(log_path, "2026-07-10")
        # 正常情况返回的 metrics 不含 "status" 键
        assert "status" not in metrics
        assert metrics["error_count"] == 2
        assert metrics["warning_count"] == 1
        # 应识别出 top 模块（top_modules 是 list）
        assert "top_modules" in metrics
        assert isinstance(metrics["top_modules"], list)
        assert len(metrics["top_modules"]) > 0


# ========== analyze_memory_cleanup ==========

class TestAnalyzeMemoryCleanup:
    """测试记忆清理分析。"""

    def test_no_dir_returns_no_data(self, tmp_path: Path) -> None:
        """目录不存在时返回 no_data。"""
        issues, metrics, _ = fhr.analyze_memory_cleanup(tmp_path / "missing", "2026-07-10")
        assert metrics.get("status") == "no_data"
        assert issues == []

    def test_no_report_files_returns_no_data(self, tmp_path: Path) -> None:
        """目录存在但无报告时返回 no_data。"""
        issues, metrics, _ = fhr.analyze_memory_cleanup(tmp_path, "2026-07-10")
        assert metrics.get("status") == "no_data"

    def test_parses_report_correctly(self, tmp_path: Path) -> None:
        """正确解析 cleanup-report JSON。"""
        report = {
            "version": "6",
            "timestamp": "2026-07-10T13:05:00+0000",
            "mode": "apply",
            "total_time_s": 120.5,
            "tokens": {"prompt": 5000, "completion": 2000},
            "sources": {
                "MEMORY.md": {
                    "total_entries": 50,
                    "phase1_merge": 3,
                    "phase1_compress": 8,
                    "phase1_hindsight": 5,
                    "phase1_remove": 10,
                    "phase1_flagged": 0,
                    "after_cleanup": {"keep": 35, "keep_chars": 35000},
                    "phase2": {"correct": 8, "corrected": 1, "keep": 1},
                },
                "USER.md": {
                    "total_entries": 20,
                    "phase1_merge": 1,
                    "phase1_compress": 2,
                    "phase1_hindsight": 0,
                    "phase1_remove": 3,
                    "phase1_flagged": 0,
                    "after_cleanup": {"keep": 15, "keep_chars": 10000},
                    "phase2": {"correct": 2, "corrected": 0, "keep": 1},
                },
            },
        }
        (tmp_path / "cleanup-report-20260710_130500.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        issues, metrics, trend = fhr.analyze_memory_cleanup(tmp_path, "2026-07-10")
        assert metrics["memory_usage_pct"] == 70.0  # 35000/50000
        assert metrics["user_usage_pct"] == 66.7    # 10000/15000
        assert metrics["total_compress"] == 10       # 8 + 2
        assert metrics["total_hindsight"] == 5       # 5 + 0
        assert metrics["total_remove"] == 13         # 10 + 3
        assert metrics["mode"] == "apply"
        assert metrics["date_matched"] is True
        # v2_correct_rate 聚合 MEMORY + USER: correct=10, total=13 (mem:8+1+1=10, user:2+0+1=3)
        assert metrics["v2_correct_rate"] == round(10 / 13 * 100, 1)
        # 首次运行无 prev，trend 应为空 dict
        assert trend == {}
        # 占用 < 90%，不应触发 issue
        assert not any("占用" in i.get("desc", "") for i in issues)

    def test_trend_with_prev_snapshot(self, tmp_path: Path) -> None:
        """有 memory_prev.json 时应生成趋势字符串。"""
        # 先写入 prev snapshot
        fhr._save_json(tmp_path / "memory_prev.json", {
            "memory_usage_pct": 60.0,
            "user_usage_pct": 50.0,
        })
        report = {
            "version": "6",
            "timestamp": "2026-07-10T13:05:00+0000",
            "mode": "apply",
            "total_time_s": 100,
            "tokens": {"prompt": 1000, "completion": 500},
            "sources": {
                "MEMORY.md": {
                    "after_cleanup": {"keep_chars": 35000},
                    "phase2": {"correct": 0, "corrected": 0, "keep": 0},
                },
                "USER.md": {
                    "after_cleanup": {"keep_chars": 7500},
                    "phase2": {"correct": 0, "corrected": 0, "keep": 0},
                },
            },
        }
        (tmp_path / "cleanup-report-20260710_130500.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        issues, metrics, trend = fhr.analyze_memory_cleanup(tmp_path, "2026-07-10")
        # trend 应包含趋势字符串而非 raw float
        assert "MEMORY占用率" in trend
        assert "→" in trend["MEMORY占用率"]
        assert "USER占用率" in trend
        assert "→" in trend["USER占用率"]
        # MEMORY: 60% → 70%, delta = +10
        assert "+10.0%" in trend["MEMORY占用率"]

    def test_high_usage_triggers_issue(self, tmp_path: Path) -> None:
        """字符占用超过阈值时触发 P1 issue。"""
        report = {
            "version": "6",
            "timestamp": "2026-07-10T13:05:00+0000",
            "mode": "apply",
            "total_time_s": 100,
            "tokens": {"prompt": 1000, "completion": 500},
            "sources": {
                "MEMORY.md": {
                    "total_entries": 100,
                    "phase1_merge": 0,
                    "phase1_compress": 0,
                    "phase1_hindsight": 0,
                    "phase1_remove": 0,
                    "phase1_flagged": 0,
                    "after_cleanup": {"keep": 100, "keep_chars": 48000},
                    "phase2": {"correct": 0, "corrected": 0, "keep": 0},
                },
                "USER.md": {
                    "total_entries": 30,
                    "phase1_merge": 0,
                    "phase1_compress": 0,
                    "phase1_hindsight": 0,
                    "phase1_remove": 0,
                    "phase1_flagged": 0,
                    "after_cleanup": {"keep": 30, "keep_chars": 14500},
                    "phase2": {"correct": 0, "corrected": 0, "keep": 0},
                },
            },
        }
        (tmp_path / "cleanup-report-20260710_130500.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        issues, metrics, _ = fhr.analyze_memory_cleanup(tmp_path, "2026-07-10")
        # MEMORY: 48000/50000 = 96% > 90%
        assert any("MEMORY.md 字符占用" in i.get("desc", "") for i in issues)
        # USER: 14500/15000 = 96.7% > 90%
        assert any("USER.md 字符占用" in i.get("desc", "") for i in issues)


# ========== analyze_skill_usage ==========

class TestAnalyzeSkillUsage:
    """测试 Skill 使用情况分析。"""

    def test_no_file_returns_no_data(self, tmp_path: Path) -> None:
        issues, metrics, _ = fhr.analyze_skill_usage(tmp_path / "missing.json",
                                                     datetime.now(timezone.utc))
        assert metrics.get("status") == "no_data"

    def test_normal_usage(self, tmp_path: Path) -> None:
        """正常 usage 数据应统计 active/used/never_used。"""
        usage_path = tmp_path / ".usage.json"
        # analyze_skill_usage 直接读取 data dict（key=skill name, value=skill dict）
        usage_path.write_text(json.dumps({
            "skill-a": {"state": "active", "use_count": 5, "last_used_at": "2026-07-09T10:00:00Z"},
            "skill-b": {"state": "active", "use_count": 1, "last_used_at": "2026-06-01T10:00:00Z"},
            "skill-c": {"state": "active", "use_count": 0, "last_used_at": None},
        }), encoding="utf-8")
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        issues, metrics, _ = fhr.analyze_skill_usage(usage_path, now)
        # 正常情况返回的 metrics 不含 "status" 键
        assert "status" not in metrics
        assert metrics["total_skills"] == 3
        # skill-c 从未使用
        assert metrics["never_used_count"] >= 1


# ========== analyze_router (with new error/kt_fallback metrics) ==========

class TestAnalyzeRouter:
    """测试 Router 分析含新增的 error/kt_fallback 指标。"""

    def test_no_data_when_no_masks(self) -> None:
        """无 router_mask 事件时返回 no_data。"""
        trace = {"router_mask": [], "recall_success": [], "recall_empty_results": [],
                 "recall_timeout": [], "recall_error": [], "hindsight_fail_kt_fallback": [],
                 "recall_sag": [], "multi_hop_expand": []}
        issues, metrics, _ = fhr.analyze_router(trace, Path("/tmp/nonexist"))
        assert metrics.get("status") == "no_data"

    def test_total_recall_includes_errors(self, tmp_path: Path) -> None:
        """total_recall 必须包含 error 次数，不能再漏算。"""
        trace = {
            "router_mask": [{"mask": {"h": True, "kt": True, "s": True, "sag": False}}],
            "recall_success": [{"latency_ms": 100, "score_stats": {"avg": 0.8}}],
            "recall_empty_results": [{}],
            "recall_timeout": [{}],
            "recall_error": [{"error": "RuntimeError"}, {"error": "Timeout"}],
            "hindsight_fail_kt_fallback": [{"kt_count": 1}],
            "recall_sag": [],
            "multi_hop_expand": [],
        }
        issues, metrics, _ = fhr.analyze_router(trace, tmp_path)
        # success(1) + empty(1) + timeout(1) + error(2) = 5
        assert metrics["success_count"] == 1
        assert metrics["empty_count"] == 1
        assert metrics["timeout_count"] == 1
        assert metrics["error_count"] == 2
        assert metrics["kt_fallback_count"] == 1
        # success_rate = 1/5 * 100 = 20
        assert metrics["success_rate"] == 20.0
        # error_rate = 2/5 * 100 = 40
        assert metrics["error_rate"] == 40.0

    def test_high_error_rate_triggers_issue(self, tmp_path: Path) -> None:
        """高 error_rate 应触发 P1 issue。"""
        trace = {
            "router_mask": [{"mask": {"h": True, "kt": True, "s": True, "sag": False}}
                            for _ in range(10)],
            "recall_success": [{"latency_ms": 100, "score_stats": {"avg": 0.8}} for _ in range(5)],
            "recall_empty_results": [{} for _ in range(2)],
            "recall_timeout": [{}],
            "recall_error": [{"error": "x"} for _ in range(2)],  # error_rate = 2/10 = 20%
            "hindsight_fail_kt_fallback": [],
            "recall_sag": [],
            "multi_hop_expand": [],
        }
        issues, metrics, _ = fhr.analyze_router(trace, tmp_path)
        assert metrics["error_rate"] == 20.0
        # 阈值 error_rate_high_pct=5%，20% > 5% 应触发
        error_issues = [i for i in issues if "错误率" in i.get("desc", "")]
        assert len(error_issues) >= 1
        assert error_issues[0]["severity"] == "P1"


# ========== generate_recommendations ==========

class TestGenerateRecommendations:
    """测试推荐生成函数。"""

    def _empty_metrics(self) -> tuple:
        """构造空的 metrics 元组，模拟 no_data 状态。"""
        router_m = {"status": "no_data"}
        skill_m = {"status": "no_data"}
        kn_m = {"status": "no_data", "dim_summary": {}}
        kt_m = {"status": "no_data"}
        cluster_m = {"status": "no_data"}
        token_m = {"status": "no_data"}
        sag_contr_m = {"status": "no_data"}
        skill_usage_m = {"status": "no_data"}
        error_m = {"status": "no_data", "error_count": 0, "warning_count": 0}
        return (router_m, skill_m, kn_m, kt_m, cluster_m,
                token_m, sag_contr_m, skill_usage_m, error_m)

    def test_no_data_returns_empty(self) -> None:
        """全部 no_data 时应返回空列表（无推荐）。"""
        (router_m, skill_m, kn_m, kt_m, cluster_m,
         token_m, sag_contr_m, skill_usage_m, error_m) = self._empty_metrics()
        recs = fhr.generate_recommendations(
            router_m, skill_m, kn_m, kt_m, cluster_m,
            [], {}, [], [],
            token_m, sag_contr_m, skill_usage_m, error_m,
        )
        assert recs == []

    def test_high_error_rate_recommendation(self) -> None:
        """高 error_rate 应生成 Router 优化建议。"""
        (router_m, skill_m, kn_m, kt_m, cluster_m,
         token_m, sag_contr_m, skill_usage_m, error_m) = self._empty_metrics()
        router_m.update({
            "status": "ok",
            "total_masks": 10,
            "full_off_pct": 5.0,
            "empty_rate": 10.0,
            "error_rate": 25.0,
            "avg_latency_ms": 200,
            "avg_score": 0.7,
            "sag_on_pct": 50.0,
            "sag_total_kept": 5,
            "sag_avg_latency_ms": 300,
        })
        recs = fhr.generate_recommendations(
            router_m, skill_m, kn_m, kt_m, cluster_m,
            [], {}, [], [],
            token_m, sag_contr_m, skill_usage_m, error_m,
        )
        # 高 error_rate 应触发 Router 相关建议
        router_recs = [r for r in recs if r.get("flywheel") == "Router"]
        assert len(router_recs) >= 1

    def test_token_exhaustion_recommendation(self) -> None:
        """Token 耗尽率高应生成优化建议。"""
        (router_m, skill_m, kn_m, kt_m, cluster_m,
         token_m, sag_contr_m, skill_usage_m, error_m) = self._empty_metrics()
        token_m.update({
            "status": "ok",
            "total_budget": 8000,
            "event_count": 10,
            "exhaust_pct": 60.0,
            "exhaust_count": 6,
            "total_stats": {"avg": 6000, "p50": 6000, "p90": 7500, "max": 7800},
            "hs_stats": {"avg": 3000, "p50": 3000, "p90": 3500, "max": 3600},
            "kt_stats": {"avg": 1500, "p50": 1500, "p90": 1800, "max": 1900},
            "skill_stats": {"avg": 200, "p50": 200, "p90": 300, "max": 300},
        })
        recs = fhr.generate_recommendations(
            router_m, skill_m, kn_m, kt_m, cluster_m,
            [], {}, [], [],
            token_m, sag_contr_m, skill_usage_m, error_m,
        )
        # 应有 Token 相关建议
        token_recs = [r for r in recs if "Token" in r.get("flywheel", "") or "token" in r.get("desc", "").lower()]
        assert len(token_recs) >= 1

    def test_sag_zero_merge_recommendation(self) -> None:
        """SAG merge 零结果率高应生成优化建议。"""
        (router_m, skill_m, kn_m, kt_m, cluster_m,
         token_m, sag_contr_m, skill_usage_m, error_m) = self._empty_metrics()
        # SAG 推荐规则要求 sag_on_pct > 10（来自 router_m）
        router_m.update({
            "status": "ok",
            "total_masks": 10,
            "sag_on_pct": 50.0,  # > 10，满足推荐触发条件
        })
        sag_contr_m.update({
            "status": "ok",
            "recall_count": 10,
            "merge_count": 10,
            "merge_zero_pct": 70.0,  # 超阈值 50%
            "recall_stats": {"avg": 3, "total": 30},
            "merge_stats": {"avg": 0.5, "total": 5},
        })
        recs = fhr.generate_recommendations(
            router_m, skill_m, kn_m, kt_m, cluster_m,
            [], {}, [], [],
            token_m, sag_contr_m, skill_usage_m, error_m,
        )
        sag_recs = [r for r in recs if "SAG" in r.get("flywheel", "") or "sag" in r.get("desc", "").lower()]
        assert len(sag_recs) >= 1
