"""测试复盘修复的问题：幂等性、文件名清洗、LLM 失败不缓存、飞书幂等等。"""
import json
import os
import re
from unittest.mock import patch, MagicMock

import pytest

from tests._helpers import make_reflection as _make_reflection


# ── _sanitize_filename ──────────────────────────────

class TestSanitizeFilename:
    def test_colon_replaced(self, module):
        assert ":" not in module._sanitize_filename("配置优化：Redis 缓存")

    def test_slash_replaced(self, module):
        assert "/" not in module._sanitize_filename("路径/文件名")

    def test_backslash_replaced(self, module):
        assert "\\" not in module._sanitize_filename("路径\\文件名")

    def test_pipe_replaced(self, module):
        assert "|" not in module._sanitize_filename("A|B")

    def test_question_mark_replaced(self, module):
        assert "?" not in module._sanitize_filename("为什么？")

    def test_asterisk_replaced(self, module):
        assert "*" not in module._sanitize_filename("通配符*")

    def test_angle_brackets_replaced(self, module):
        assert "<" not in module._sanitize_filename("<标签>")
        assert ">" not in module._sanitize_filename("<标签>")

    def test_double_quote_replaced(self, module):
        assert '"' not in module._sanitize_filename('说"你好"')

    def test_control_chars_replaced(self, module):
        assert "\x00" not in module._sanitize_filename("test\x00null")

    def test_empty_returns_untitled(self, module):
        assert module._sanitize_filename("") == "untitled"

    def test_max_length(self, module):
        name = "A" * 100
        assert len(module._sanitize_filename(name, max_len=60)) == 60

    def test_strips_trailing_dots(self, module):
        assert not module._sanitize_filename("test.").endswith(".")


# ── _safe_int ───────────────────────────────────────

class TestSafeInt:
    def test_normal_int(self, module):
        assert module._safe_int(5, 0) == 5

    def test_string_int(self, module):
        assert module._safe_int("5", 0) == 5

    def test_float_string(self, module):
        assert module._safe_int("5.0", 0) == 5

    def test_float(self, module):
        assert module._safe_int(5.7, 0) == 5

    def test_invalid_string(self, module):
        assert module._safe_int("high", 3) == 3

    def test_none(self, module):
        assert module._safe_int(None, 3) == 3


# ── LLM 失败不缓存 ──────────────────────────────────

class TestLLMFailureNoCache:
    def test_filter_failure_does_not_cache(self, module, tmp_config):
        """LLM 调用失败时不写缓存，下次运行会重试。"""
        sessions = [{
            "id": "fail-s1", "title": "测试", "text": "A" * 2500, "text_len": 2500,
        }]
        with patch.object(module, "call_llm_json", side_effect=Exception("LLM down")):
            reflections = module.phase_synthesize(sessions, dry_run=True)

        assert reflections == []
        verdict_file = os.path.join(tmp_config["cache"]["verdict_dir"], "fail-s1.json")
        assert not os.path.exists(verdict_file)

    def test_filter_failure_retry_next_run(self, module, tmp_config):
        """LLM 失败后下次运行能重新处理。"""
        sessions = [{
            "id": "retry-s1", "title": "重试", "text": "A" * 2500, "text_len": 2500,
        }]
        # 第一次失败
        with patch.object(module, "call_llm_json", side_effect=Exception("LLM down")):
            module.phase_synthesize(sessions, dry_run=True)

        # 第二次成功
        with patch.object(module, "call_llm_json", return_value={"score": 4, "reason": "good"}), \
             patch.object(module, "call_llm", return_value="# 反思标题\n内容"), \
             patch.object(module, "sag_ingest", return_value=True):
            reflections = module.phase_synthesize(sessions, dry_run=True)

        assert len(reflections) == 1


# ── patterns 幂等 ───────────────────────────────────

class TestPatternsIdempotency:
    def test_duplicate_topic_skipped(self, module, tmp_config):
        """已写入过的 topic 不会重复写入。"""
        pattern_log = tmp_config["cache"].get("pattern_log",
            os.path.join(os.path.dirname(tmp_config["cache"]["verdict_dir"]), "pattern-log.json"))
        os.makedirs(os.path.dirname(pattern_log), exist_ok=True)
        with open(pattern_log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"topic": "已写入主题", "date": "2026-01-01"}, ensure_ascii=False) + "\n")

        reflections = [_make_reflection("s1", "A"), _make_reflection("s2", "B")]
        result_data = {"patterns": [{"topic": "已写入主题", "evidence_count": 2}]}

        ingest_calls = []
        with patch.object(module, "call_llm_json", return_value=result_data), \
             patch.object(module, "sag_ingest", side_effect=lambda *a, **k: ingest_calls.append(a) or True):
            result = module.phase_patterns(reflections, dry_run=False)

        assert len(ingest_calls) == 0
        assert len(result) == 0

    def test_new_topic_written_and_logged(self, module, tmp_config):
        """新 topic 写入 SAG 并记录到日志。"""
        pattern_log = tmp_config["cache"].get("pattern_log",
            os.path.join(os.path.dirname(tmp_config["cache"]["verdict_dir"]), "pattern-log.json"))

        reflections = [_make_reflection("s1", "A"), _make_reflection("s2", "B")]
        result_data = {"patterns": [{"topic": "新主题", "evidence_count": 2}]}

        with patch.object(module, "call_llm_json", return_value=result_data), \
             patch.object(module, "sag_ingest", return_value=True):
            result = module.phase_patterns(reflections, dry_run=False)

        assert len(result) == 1
        assert os.path.exists(pattern_log)
        with open(pattern_log, encoding="utf-8") as f:
            entry = json.loads(f.readline())
            assert entry["topic"] == "新主题"


# ── promote 文件名清洗 + 幂等 ───────────────────────

class TestPromoteFilenameAndIdempotency:
    def test_filename_sanitized(self, module, tmp_config):
        """LLM 输出含冒号的标题不会导致崩溃。"""
        reflections = [_make_reflection("s1", "配置优化：Redis 缓存")]

        with patch.object(module, "call_llm_json",
                          return_value={"promote": True, "category": "concepts"}):
            result = module.phase_promote(reflections, dry_run=False)

        assert len(result) == 1
        wiki_path = os.path.join(tmp_config["wiki"]["base_path"], "concepts", "配置优化：Redis 缓存.md")
        assert not os.path.exists(wiki_path)
        safe_path = os.path.join(tmp_config["wiki"]["base_path"], "concepts", "配置优化_Redis 缓存.md")
        assert os.path.exists(safe_path)

    def test_existing_wiki_file_only_logs(self, module, tmp_config):
        """Wiki 文件已存在时只补日志，不重写。"""
        reflections = [_make_reflection("s1", "已存在文件")]
        wiki_dir = os.path.join(tmp_config["wiki"]["base_path"], "concepts")
        os.makedirs(wiki_dir, exist_ok=True)
        wiki_path = os.path.join(wiki_dir, "已存在文件.md")
        with open(wiki_path, "w", encoding="utf-8") as f:
            f.write("原始内容")

        with patch.object(module, "call_llm_json",
                          return_value={"promote": True, "category": "concepts"}):
            result = module.phase_promote(reflections, dry_run=False)

        assert len(result) == 1
        with open(wiki_path, encoding="utf-8") as f:
            assert f.read() == "原始内容"
        promote_log = tmp_config["cache"]["promote_log"]
        assert os.path.exists(promote_log)


# ── feishu 幂等 ─────────────────────────────────────

class TestFeishuIdempotency:
    def test_already_pushed_today_skips(self, module, tmp_config):
        """已推送过的 session_id 不再重复推送。"""
        from datetime import datetime
        feishu_log = tmp_config["cache"].get("feishu_log",
            os.path.join(os.path.dirname(tmp_config["cache"]["verdict_dir"]), "feishu-log.json"))
        os.makedirs(os.path.dirname(feishu_log), exist_ok=True)
        with open(feishu_log, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "date": "2026-07-20", "time": "10:00",
                "session_ids": ["s1"],
                "titles": ["测试"],
            }, ensure_ascii=False) + "\n")

        reflections = [_make_reflection("s1", "测试")]
        with patch.object(module.subprocess, "run") as mock_run:
            module.phase_feishu(reflections, [], dry_run=False)
            mock_run.assert_not_called()

    def test_push_records_log(self, module, tmp_config):
        """推送成功后写入日志。"""
        feishu_log = tmp_config["cache"].get("feishu_log",
            os.path.join(os.path.dirname(tmp_config["cache"]["verdict_dir"]), "feishu-log.json"))
        reflections = [_make_reflection("s1", "推送测试")]

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        with patch.object(module.subprocess, "run", return_value=mock_proc):
            module.phase_feishu(reflections, [], dry_run=False)

        assert os.path.exists(feishu_log)

    def test_unarchived_reason_in_message(self, module, tmp_config):
        """飞书消息中包含未归档原因。"""
        reflections = [_make_reflection("s1", "低分反思", score=2)]
        captured_msg = []

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        def capture_run(args, **kwargs):
            # --markdown 后面跟的就是消息内容
            if "--markdown" in args:
                idx = args.index("--markdown")
                captured_msg.append(args[idx + 1])
            return mock_proc

        with patch.object(module.subprocess, "run", side_effect=capture_run):
            module.phase_feishu(reflections, [], dry_run=False)

        assert len(captured_msg) > 0
        assert "未归档原因" in captured_msg[0]

    def test_top5_assertion(self, module, tmp_config):
        """超过 5 条时只取 top-5，消息中只有 5 条。"""
        reflections = [_make_reflection(f"s{i}", f"标题{i}", score=i) for i in range(1, 11)]
        captured_msg = []

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        def capture_run(args, **kwargs):
            if "--markdown" in args:
                idx = args.index("--markdown")
                captured_msg.append(args[idx + 1])
            return mock_proc

        with patch.object(module.subprocess, "run", side_effect=capture_run):
            module.phase_feishu(reflections, [], dry_run=False)

        msg = captured_msg[0]
        # top-5 条目编号 1-5
        for i in range(1, 6):
            assert f"{i}." in msg
        # 不应有第 6 条
        assert "6." not in msg or "6." not in msg.split("Top-5")[1][:50]


# ── 时间戳更新逻辑 ──────────────────────────────────

class TestTimestampUpdate:
    def test_single_phase_patterns_does_not_update_ts(self, module, tmp_config, tmp_path):
        """单独运行 patterns 不更新时间戳。"""
        verdict_dir = tmp_config["cache"]["verdict_dir"]
        ts_file = os.path.join(verdict_dir, "last_run.txt")

        with patch("sys.argv", ["dream-daily.py", "--phase", "patterns"]), \
             patch.object(module, "sag_search", return_value=[]):
            module.main()

        assert not os.path.exists(ts_file)

    def test_single_phase_promote_does_not_update_ts(self, module, tmp_config, tmp_path):
        """单独运行 promote 不更新时间戳。"""
        verdict_dir = tmp_config["cache"]["verdict_dir"]
        ts_file = os.path.join(verdict_dir, "last_run.txt")

        with patch("sys.argv", ["dream-daily.py", "--phase", "promote"]), \
             patch.object(module, "sag_search", return_value=[]):
            module.main()

        assert not os.path.exists(ts_file)

    def test_single_phase_feishu_does_not_update_ts(self, module, tmp_config, tmp_path):
        """单独运行 feishu 不更新时间戳。"""
        verdict_dir = tmp_config["cache"]["verdict_dir"]
        ts_file = os.path.join(verdict_dir, "last_run.txt")

        with patch("sys.argv", ["dream-daily.py", "--phase", "feishu"]), \
             patch.object(module, "sag_search", return_value=[]):
            module.main()

        assert not os.path.exists(ts_file)


# ── call_llm_json 正则修复 ──────────────────────────

class TestCallLlmJsonRegex:
    def test_direct_json_parsed(self, module, tmp_config):
        """直接输出 JSON 时能正确解析。"""
        with patch.object(module, "call_llm", return_value='{"score": 4, "reason": "good"}'):
            result = module.call_llm_json("prompt", "model")
        assert result["score"] == 4

    def test_json_embedded_in_text(self, module, tmp_config):
        """JSON 嵌在文本中时能提取。"""
        raw = 'Here is the result:\n{"score": 3, "reason": "ok"}\nDone.'
        with patch.object(module, "call_llm", return_value=raw):
            result = module.call_llm_json("prompt", "model")
        assert result["score"] == 3

    def test_nested_json(self, module, tmp_config):
        """嵌套 JSON 对象能正确解析。"""
        raw = '{"score": 4, "reason": "ok", "details": {"a": 1, "b": 2}}'
        with patch.object(module, "call_llm", return_value=raw):
            result = module.call_llm_json("prompt", "model")
        assert result["score"] == 4
        assert result["details"]["a"] == 1

    def test_no_json_returns_empty(self, module, tmp_config):
        """无 JSON 输出时返回空 dict。"""
        with patch.object(module, "call_llm", return_value="no json here"):
            result = module.call_llm_json("prompt", "model", max_retries=0)
        assert result == {}


# ── main 顶层异常处理 ───────────────────────────────

class TestMainExceptionHandling:
    def test_pipeline_exception_does_not_crash(self, module, tmp_config, capsys):
        """流水线异常被捕获，不抛出未处理异常。"""
        with patch("sys.argv", ["dream-daily.py", "--dry-run"]), \
             patch.object(module, "read_sessions", side_effect=RuntimeError("DB error")):
            with pytest.raises(SystemExit):
                module.main()

        captured = capsys.readouterr()
        assert "异常中断" in captured.err


# ── promote 单独运行从 SAG 拉取 ─────────────────────

class TestPromoteSagFallback:
    def test_promote_phase_pulls_from_sag(self, module, tmp_config):
        """单独运行 promote 时从 SAG 拉取 reflections。"""
        sag_sections = [
            {"title": "SAG反思", "content": "内容", "metadata": {"session_id": "sag1", "score": 4}},
        ]
        with patch("sys.argv", ["dream-daily.py", "--dry-run", "--phase", "promote"]), \
             patch.object(module, "sag_search", return_value=sag_sections) as mock_search, \
             patch.object(module, "phase_promote", return_value=[]) as mock_promote:
            module.main()

        mock_search.assert_called_once()
        call_args = mock_search.call_args
        assert call_args[1].get("source_filter") == "dream-synth" or \
               (len(call_args[0]) >= 3 and call_args[0][2] == "dream-synth")
        mock_promote.assert_called_once()
        passed_reflections = mock_promote.call_args[0][0]
        assert len(passed_reflections) == 1
        assert passed_reflections[0]["title"] == "SAG反思"


# ── feishu 摘要 fallback ────────────────────────────

class TestFeishuSummaryFallback:
    def test_no_summary_section_uses_content_prefix(self, module, tmp_config):
        """摘要正则匹配失败时用 content 前缀作为 fallback。"""
        reflections = [{
            "session_id": "s1", "title": "无摘要反思", "score": 4,
            "content": "这段内容没有标准摘要格式，但有一些文本。",
        }]
        captured_msg = []

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        def capture_run(args, **kwargs):
            if "--markdown" in args:
                idx = args.index("--markdown")
                captured_msg.append(args[idx + 1])
            return mock_proc

        with patch.object(module.subprocess, "run", side_effect=capture_run):
            module.phase_feishu(reflections, [], dry_run=False)

        assert len(captured_msg) > 0
        assert "这段内容" in captured_msg[0]
