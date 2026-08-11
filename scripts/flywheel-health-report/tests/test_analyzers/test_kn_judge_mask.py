"""Mask 级 KN Judge + Auto-Tuner 因果绑定单测（T1/T2/T4 落地的回归保障）。

覆盖：
- T1  recall_logger._normalize_source：source 规范标注（h/kt/sag/skill）
- T2  kn_judge：_parse_judge_response（JSON/代码块/单数字/越界裁剪）、
        _format_summaries_by_source（按来源分组）、
        _judge_one_masked（单次 LLM 返回结构化多路评分）、
        run_judge_within_window（30 天滚动窗口 + per-mask 聚合 + 样本不足 error）
- T4  tuner：_feedback_key_trusted（全局 vs mask 样本门控）、
        _param_judge_trusted（任一主观键可信即可）、
        determine_direction（首次粗步幅探方向 + judge 不可信时不推边界）、
        update_state（改善时记录 best_value，恶化保留）
"""
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

# ---- 路径加载（与 test_p1_p4_fixes.py 同模式）----
SRC_FHR = r"d:\HermesProject\scripts\flywheel-health-report\src"
SRC_KN = r"d:\HermesProject\plugins\knowledge-navigation\src"
for _p in (SRC_FHR, SRC_KN):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from flywheel_health_report import config as fhr_config  # noqa: E402
from flywheel_health_report.analyzers import kn_judge  # noqa: E402
from flywheel_health_report.auto_tuner import tuner  # noqa: E402
from knowledge_navigation.core import recall_logger  # noqa: E402

NOW = datetime.now(timezone.utc)


# =====================================================================
# T1: recall_logger._normalize_source
# =====================================================================
def test_normalize_source_empty_and_unknown_to_hindsight():
    assert recall_logger._normalize_source(None) == "hindsight"
    assert recall_logger._normalize_source("") == "hindsight"
    assert recall_logger._normalize_source("unknown") == "hindsight"
    assert recall_logger._normalize_source("foo") == "hindsight"


def test_normalize_source_aliases():
    assert recall_logger._normalize_source("h") == "hindsight"
    assert recall_logger._normalize_source("hindsight") == "hindsight"
    assert recall_logger._normalize_source("hind") == "hindsight"
    assert recall_logger._normalize_source("kt") == "knowledge_tree"
    assert recall_logger._normalize_source("knowledge_tree") == "knowledge_tree"
    assert recall_logger._normalize_source("sag") == "sag"
    assert recall_logger._normalize_source("session") == "sag"
    assert recall_logger._normalize_source("skill") == "skill"


# =====================================================================
# T2: kn_judge._parse_judge_response
# =====================================================================
def test_parse_judge_clean_json():
    content = json.dumps(
        {"overall": 0.8, "hindsight": 0.9, "knowledge_tree": 0.6, "sag": 0.7}
    )
    r = kn_judge._parse_judge_response(content)
    assert r == {"overall": 0.8, "hindsight": 0.9, "knowledge_tree": 0.6, "sag": 0.7}


def test_parse_judge_code_block_and_clamp():
    # 代码块包裹 + 越界值裁剪
    content = '```json\n{"overall": 1.7, "hindsight": -0.3, "knowledge_tree": null, "sag": 0.5}\n```'
    r = kn_judge._parse_judge_response(content)
    assert r["overall"] == 1.0
    assert r["hindsight"] == 0.0
    assert r["knowledge_tree"] is None
    assert r["sag"] == 0.5


def test_parse_judge_single_number_fallback():
    r = kn_judge._parse_judge_response("0.42")
    assert r["overall"] == 0.42
    assert r["hindsight"] is None
    assert r["knowledge_tree"] is None
    assert r["sag"] is None


def test_parse_judge_overall_missing_uses_score():
    content = json.dumps({"score": 0.65, "hindsight": 0.7})
    r = kn_judge._parse_judge_response(content)
    assert r["overall"] == 0.65
    assert r["hindsight"] == 0.7


def test_parse_judge_garbage_returns_none():
    assert kn_judge._parse_judge_response("not-a-json-at-all") is None


# =====================================================================
# T2: kn_judge._format_summaries_by_source
# =====================================================================
def test_format_summaries_by_source_grouping():
    rec = {
        "recalled_summaries": [
            {"source": "hindsight", "title": "A", "text": "xa"},
            {"source": "knowledge_tree", "title": "B"},
            {"source": "sag", "title": "C"},
            {"source": "skill", "title": "D"},
            {"source": "weird", "title": "E"},
        ]
    }
    text = kn_judge._format_summaries_by_source(rec)
    assert "hindsight（历史回溯笔记）" in text
    assert "knowledge_tree（知识树节点）" in text
    assert "sag（会话摘要）" in text
    assert "其他来源" in text and "D" in text  # skill → 其他来源
    assert "E" in text  # weird → 其他来源


def test_format_summaries_empty():
    assert "无内容摘要" in kn_judge._format_summaries_by_source({"recalled_summaries": []})


# =====================================================================
# T2: kn_judge._judge_one_masked（mock urlopen 返回结构化评分）
# =====================================================================
def _fake_response(payload: dict):
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(
        {"choices": [{"message": {"content": json.dumps(payload)}}]}
    ).encode("utf-8")
    # `with urlopen(...) as resp:` 会调用 resp.__enter__()，
    # 裸 MagicMock 的 __enter__ 默认返回「新」MagicMock（丢失 read.return_value）。
    # 必须让 __enter__ 返回 resp 自身，模拟真实上下文管理器返回自己。
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_judge_one_masked_structured():
    rec = {
        "query_trunc": "如何配置 router",
        "kept_results": 3, "total_results": 10,
        "excluded_marked": 2, "avg_score": 0.72, "injected_count": 5,
        "recalled_summaries": [
            {"source": "hindsight", "title": "t1", "text": "x"},
            {"source": "sag", "title": "t2", "text": "y"},
        ],
    }
    payload = {"overall": 0.7, "hindsight": 0.8, "knowledge_tree": None, "sag": 0.9}
    with mock.patch.object(kn_judge.urllib.request, "urlopen", return_value=_fake_response(payload)), \
         mock.patch.object(kn_judge.ssl, "create_default_context", return_value=mock.Mock()):
        score, ok = kn_judge._judge_one_masked(rec, {"url": "http://x", "key": "k", "model": "m"})
    assert ok is True
    assert score == payload


def test_judge_one_masked_empty_content_fails():
    rec = {"query_trunc": "q", "kept_results": 0, "total_results": 0,
           "excluded_marked": 0, "avg_score": 0, "injected_count": 0,
           "recalled_summaries": []}
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(
        {"choices": [{"message": {"content": ""}}]}
    ).encode("utf-8")
    with mock.patch.object(kn_judge.urllib.request, "urlopen", return_value=resp), \
         mock.patch.object(kn_judge.ssl, "create_default_context", return_value=mock.Mock()):
        score, err = kn_judge._judge_one_masked(rec, {"url": "http://x", "key": "k", "model": "m"})
    assert score is None
    assert isinstance(err, Exception)


# =====================================================================
# T2: kn_judge.run_judge_within_window（滚动窗口 + per-mask 聚合）
# =====================================================================
def _make_home_with_trace(tmp_path: Path) -> Path:
    home = tmp_path / "hermes"
    (home / "plugins" / "knowledge-navigation").mkdir(parents=True)
    (home / "plugins" / "knowledge-navigation" / "trace.log").write_text("x\n", encoding="utf-8")
    return home


def test_run_judge_within_window_aggregation():
    import tempfile
    tmp = tempfile.mkdtemp()
    home = Path(tmp) / "hermes"
    (home / "plugins" / "knowledge-navigation").mkdir(parents=True)
    (home / "plugins" / "knowledge-navigation" / "trace.log").write_text("x\n", encoding="utf-8")

    recent = (NOW - timedelta(days=1)).isoformat()
    old = (NOW - timedelta(days=60)).isoformat()
    fake_records = [{"timestamp": recent} for _ in range(25)]
    fake_records.append({"timestamp": old})  # 超出滚动窗口，应被排除

    score = {"overall": 0.8, "hindsight": 0.9, "knowledge_tree": None, "sag": 0.7}
    with mock.patch.object(kn_judge, "_ensure_collect_baseline_imported", return_value=True), \
         mock.patch.object(kn_judge, "_collect_all_recalls", return_value=fake_records), \
         mock.patch.object(kn_judge, "_load_llm_config", return_value={"url": "u", "key": "k", "model": "m"}), \
         mock.patch.object(kn_judge, "_judge_one_masked", return_value=(score, True)):
        out = kn_judge.run_judge_within_window(home, since_iso="2000-01-01T00:00:00", until_iso="")

    # 25 条在窗口内，1 条 60 天前被排除 → 样本 25
    assert out["kn_judge_sample_count"] == 25
    assert out["kn_judge_fallback"] is False
    # 全局聚合
    assert out["kn_judge_relevant_rate"] == 1.0
    assert out["kn_judge_avg_relevance"] == 0.8
    # mask 级
    assert out["kn_judge_sample_count_h"] == 25
    assert out["kn_judge_relevant_rate_h"] == 1.0
    assert out["kn_judge_avg_relevance_h"] == 0.9
    assert out["kn_judge_sample_count_kt"] == 0  # knowledge_tree 无召回
    assert out["kn_judge_sample_count_sag"] == 25
    assert out["kn_judge_avg_relevance_sag"] == 0.7


def test_run_judge_window_ignores_report_day_boundary():
    """回归：report.py 传入 1 天 CN 日切窗（until≈now-8h）时，
    judge 不能把「今天至今」产生的 recall 全部截断——上界必须取 now。

    旧 bug：上界用 until（CN 今日 00:00 ≈ 8h 前），导致最近 1h 的
    recall 被排除，sample_count 从 30 掉成 5（甚至 0），mask 键长期为 None。
    """
    import tempfile
    tmp = tempfile.mkdtemp()
    home = Path(tmp) / "hermes"
    (home / "plugins" / "knowledge-navigation").mkdir(parents=True)
    (home / "plugins" / "knowledge-navigation" / "trace.log").write_text("x\n", encoding="utf-8")

    # 25 条最近 1h（落在 until=now-8h 之后，旧 bug 会被砍）
    recent = [(NOW - timedelta(hours=1)).isoformat() for _ in range(25)]
    # 5 条 15 天前（在 30 天窗口内、且在 until 之前）
    oldish = [(NOW - timedelta(days=15)).isoformat() for _ in range(5)]
    fake_records = [{"timestamp": t} for t in recent + oldish]

    # 模拟 report.py 的 1 天 CN 自然日窗口
    cn_offset = timedelta(hours=8)
    cn_now = NOW + cn_offset
    cn_today = cn_now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = (cn_today - timedelta(days=1) - cn_offset).isoformat()
    until = (cn_today - cn_offset).isoformat()  # ≈ now - 8h

    score = {"overall": 0.8, "hindsight": 0.9, "knowledge_tree": None, "sag": 0.7}
    with mock.patch.object(kn_judge, "_ensure_collect_baseline_imported", return_value=True), \
         mock.patch.object(kn_judge, "_collect_all_recalls", return_value=fake_records), \
         mock.patch.object(kn_judge, "_load_llm_config", return_value={"url": "u", "key": "k", "model": "m"}), \
         mock.patch.object(kn_judge, "_judge_one_masked", return_value=(score, True)):
        out = kn_judge.run_judge_within_window(home, since_iso=since, until_iso=until)

    # 全部 30 条都应在 [now-30d, now] 滚动窗口内，不被 1 天日切窗截断
    assert out["kn_judge_sample_count"] == 30, out
    assert out.get("kn_judge_error") is None, out.get("kn_judge_error")
    assert out["kn_judge_fallback"] is False



def test_run_judge_within_window_sample_below_min():
    import tempfile
    tmp = tempfile.mkdtemp()
    home = Path(tmp) / "hermes"
    (home / "plugins" / "knowledge-navigation").mkdir(parents=True)
    (home / "plugins" / "knowledge-navigation" / "trace.log").write_text("x\n", encoding="utf-8")

    # since 设在未来 → 窗口下界=未来 → 无记录落入窗口 → 样本不足
    future = (NOW + timedelta(days=1)).isoformat()
    with mock.patch.object(kn_judge, "_ensure_collect_baseline_imported", return_value=True), \
         mock.patch.object(kn_judge, "_collect_all_recalls", return_value=[]), \
         mock.patch.object(kn_judge, "_load_llm_config", return_value={"url": "u", "key": "k", "model": "m"}):
        out = kn_judge.run_judge_within_window(home, since_iso=future, until_iso="")
    assert "kn_judge_error" in out
    assert "sample_below_min" in out["kn_judge_error"]


# =====================================================================
# T4: tuner 信任门控 + 粗→细 + best-so-far
# =====================================================================
def test_feedback_key_trusted_global_vs_mask():
    # 全局键：需 kn_judge_sample_count >= min_sample(20)
    assert tuner._feedback_key_trusted("kn_judge_relevant_rate", {"kn_judge_sample_count": 25}) is True
    assert tuner._feedback_key_trusted("kn_judge_relevant_rate", {"kn_judge_sample_count": 10}) is False
    # mask 键：需 kn_judge_sample_count_h >= mask_min_sample(12)
    assert tuner._feedback_key_trusted("kn_judge_relevant_rate_h", {"kn_judge_sample_count_h": 12}) is True
    assert tuner._feedback_key_trusted("kn_judge_relevant_rate_h", {"kn_judge_sample_count_h": 5}) is False
    # before/after 任一方不足即不可信
    assert tuner._feedback_key_trusted(
        "kn_judge_relevant_rate_h",
        {"kn_judge_sample_count_h": 12},
        {"kn_judge_sample_count_h": 3},
    ) is False


def test_param_judge_trusted_any_subjective_ok():
    # 任一主观键可信即可
    assert tuner._param_judge_trusted(
        "kn_judge_relevant_rate_h,router_empty_pct",
        {"kn_judge_sample_count_h": 12},
    ) is True
    # 全主观键但都不可信 → False
    assert tuner._param_judge_trusted(
        "kn_judge_relevant_rate_h,kn_judge_avg_relevance_kt",
        {"kn_judge_sample_count_h": 3},
    ) is False
    # 无非主观键 → 视为可信（走客观指标）
    assert tuner._param_judge_trusted("router_empty_pct", {}) is True


def test_determine_direction_coarse_first_tune():
    # 首次调优（last_tune=None）→ 粗步幅 = step * 2，离最小值近 → up
    fd = tuner.determine_direction(
        "KN_MIN_SCORE", 0.50, 0.40, 0.65, 0.05, "router_empty_pct", None
    )
    assert fd["direction"] == "up"
    assert fd["new_value"] == 0.60  # 0.50 + 0.05*2
    assert "粗步幅" in fd["reason"]


def test_determine_direction_untrusted_judge_no_boundary_push():
    # judge 样本不足 → 反馈全跳过 → improved=None → 走位置策略（不推边界）
    last = {
        "parameter": "KN_MIN_SCORE", "date": "2026-08-09", "status": "applied",
        "direction": "up",
        "metrics_before": {"kn_judge_relevant_rate_h": 0.5},
        "metrics_after": {"kn_judge_relevant_rate_h": 0.6},
    }
    # 注意：last 中没有 kn_judge_sample_count_h → 不可信
    fd = tuner.determine_direction(
        "KN_MIN_SCORE", 0.50, 0.40, 0.65, 0.05, "kn_judge_relevant_rate_h", last
    )
    # 离最小值近 → up，但用正常步幅（last_tune 非 None 不粗搜）
    assert fd["direction"] == "up"
    assert fd["new_value"] == 0.55  # 0.50 + 0.05（非粗步幅）
    assert "样本不足" in fd["reason"] or "位置策略" in fd["reason"]


def test_determine_direction_uses_mask_feedback_when_summary_trusted():
    # 修复回归：summary_rec 带样本计数 → 信任门控通过 → mask 反馈真正参与决策
    summary = {
        "kn_judge_sample_count_sag": 21,
        "kn_judge_relevant_rate_sag": 0.3333,
        "sag_total_kept": 3,
    }
    last = {
        "parameter": "KN_SAG_MAX_INJECT", "status": "applied", "direction": "up",
        # 上轮 sag 相关率 0.7 → 本轮 0.3333（真实值），明显恶化
        "metrics_before": {"kn_judge_relevant_rate_sag": 0.7, "sag_total_kept": 5},
        "metrics_after":  {"kn_judge_relevant_rate_sag": 0.3333, "sag_total_kept": 3},
    }
    fd = tuner.determine_direction(
        "KN_SAG_MAX_INJECT", 3.0, 2.0, 6.0, 1.0,
        "kn_judge_relevant_rate_sag,sag_total_kept", last, summary_rec=summary
    )
    # sag 相关率恶化 → 反向上轮(up) → down；且应基于 mask 反馈而非位置策略
    assert fd["direction"] == "down"
    assert "未改善" in fd["reason"] or "反向" in fd["reason"]
    assert "样本不足" not in fd["reason"]
    assert "位置策略" not in fd["reason"]


def test_determine_direction_skips_mask_feedback_without_summary():
    # 对照：不传 summary_rec（旧 bug 路径）→ 样本计数查不到 → 反馈被跳过 → 回退位置策略
    last = {
        "parameter": "KN_SAG_MAX_INJECT", "status": "applied", "direction": "up",
        "metrics_before": {"kn_judge_relevant_rate_sag": 0.7},
        "metrics_after":  {"kn_judge_relevant_rate_sag": 0.3333},
    }
    fd = tuner.determine_direction(
        "KN_SAG_MAX_INJECT", 3.0, 2.0, 6.0, 1.0,
        "kn_judge_relevant_rate_sag", last  # 未传 summary_rec
    )
    # 反馈因不可信被忽略 → 走位置策略（不依据真实 mask 恶化信号）
    assert "样本不足" in fd["reason"] or "位置策略" in fd["reason"]


def test_update_state_best_value_kept_on_degradation():
    ns = tuner.update_state(
        {}, "KN_MIN_SCORE", "up", 0.55,
        metrics_improved=True, no_change=False,
        tune_date="2026-08-10", old_value=0.50,
    )
    assert ns["KN_MIN_SCORE"]["best_value"] == 0.55
    assert ns["KN_MIN_SCORE"]["initial_value"] == 0.50

    ns2 = tuner.update_state(
        ns, "KN_MIN_SCORE", "up", 0.45,
        metrics_improved=False, no_change=False,
        tune_date="2026-08-11", old_value=0.55,
    )
    # 恶化不覆盖 best_value（best-so-far 记忆）
    assert ns2["KN_MIN_SCORE"]["best_value"] == 0.55
    assert ns2["KN_MIN_SCORE"]["degradation_count"] == 1


if __name__ == "__main__":
    # 允许直接 python 执行（对齐 test_p1_p4_fixes.py 风格）
    test_normalize_source_empty_and_unknown_to_hindsight()
    test_normalize_source_aliases()
    test_parse_judge_clean_json()
    test_parse_judge_code_block_and_clamp()
    test_parse_judge_single_number_fallback()
    test_parse_judge_overall_missing_uses_score()
    test_parse_judge_garbage_returns_none()
    test_format_summaries_by_source_grouping()
    test_format_summaries_empty()
    test_judge_one_masked_structured()
    test_judge_one_masked_empty_content_fails()
    test_run_judge_within_window_aggregation()
    test_run_judge_within_window_sample_below_min()
    test_feedback_key_trusted_global_vs_mask()
    test_param_judge_trusted_any_subjective_ok()
    test_determine_direction_coarse_first_tune()
    test_determine_direction_untrusted_judge_no_boundary_push()
    test_determine_direction_uses_mask_feedback_when_summary_trusted()
    test_determine_direction_skips_mask_feedback_without_summary()
    test_update_state_best_value_kept_on_degradation()
    print("✅ 全部 mask 级 judge + tuner 单测通过")
