"""phase_feishu + main 入口测试。"""
import json
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from tests._helpers import make_reflection as _make_reflection


class TestPhaseFeishu:
    def test_no_unsorted_skips(self, module, tmp_config):
        """全部都归档了，就不推送。"""
        reflections = [_make_reflection("s1", "已归档")]
        promoted = [_make_reflection("s1", "已归档")]

        with patch.object(module, "subprocess") as mock_sub:
            module.phase_feishu(reflections, promoted, dry_run=True)
            mock_sub.run.assert_not_called()

    def test_dry_run_prints_no_subprocess(self, module, tmp_config):
        reflections = [_make_reflection("s1", "未归档A", score=4)]
        promoted = []

        with patch.object(module.subprocess, "run") as mock_run:
            module.phase_feishu(reflections, promoted, dry_run=True)
            mock_run.assert_not_called()

    def test_fresh_count_shows_in_message(self, module, tmp_config, capsys):
        """fresh_count 传入时报告显示今日提炼数。"""
        reflections = [_make_reflection("s1", "今日新", score=5)]
        promoted = []

        module.phase_feishu(reflections, promoted, dry_run=True, fresh_count=1)

        captured = capsys.readouterr()
        assert "今日提炼 **1** 篇新反思" in captured.out

    def test_no_fresh_count_shows_cumulative(self, module, tmp_config, capsys):
        """fresh_count=None 时报告显示累计数。"""
        reflections = [_make_reflection("s1", "旧反思", score=5)]
        promoted = []

        module.phase_feishu(reflections, promoted, dry_run=True, fresh_count=None)

        captured = capsys.readouterr()
        assert "累计 **1** 篇反思" in captured.out
        assert "今日提炼" not in captured.out

    def test_successful_push_calls_lark_cli(self, module, tmp_config):
        reflections = [_make_reflection("s1", "未归档A", score=4)]
        promoted = []

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        with patch.object(module.subprocess, "run", return_value=mock_proc) as mock_run:
            module.phase_feishu(reflections, promoted, dry_run=False)

        assert mock_run.called
        args = mock_run.call_args[0][0]
        assert "lark-cli" in args
        assert "--chat-id" in args
        assert "--markdown" in args
        assert "--as" in args
        assert args[args.index("--as") + 1] == "bot"

    def test_failed_push_prints_error(self, module, tmp_config, capsys):
        reflections = [_make_reflection("s1", "未归档A", score=4)]
        promoted = []

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "permission denied"

        with patch.object(module.subprocess, "run", return_value=mock_proc):
            module.phase_feishu(reflections, promoted, dry_run=False)

        captured = capsys.readouterr()
        assert "推送失败" in captured.err

    def test_top5_only(self, module, tmp_config):
        """多于 5 条时只取 top-5。"""
        reflections = [_make_reflection(f"s{i}", f"标题{i}", score=i) for i in range(1, 10)]
        promoted = []

        with patch.object(module.subprocess, "run") as mock_run:
            module.phase_feishu(reflections, promoted, dry_run=True)

    def test_exception_handled_gracefully(self, module, tmp_config, capsys):
        reflections = [_make_reflection("s1", "测试")]
        promoted = []

        with patch.object(module.subprocess, "run", side_effect=Exception("command not found")):
            module.phase_feishu(reflections, promoted, dry_run=False)

        captured = capsys.readouterr()
        assert "推送异常" in captured.err


class TestMainEntry:
    def test_dry_run_does_not_update_timestamp(self, module, tmp_config, tmp_path):
        verdict_dir = tmp_config["cache"]["verdict_dir"]
        os.makedirs(verdict_dir, exist_ok=True)
        ts_file = os.path.join(verdict_dir, "last_run.txt")

        with patch("sys.argv", ["dream-daily.py", "--dry-run", "--phase", "patterns"]), \
             patch.object(module, "sag_search", return_value=[]):
            module.main()

        assert not os.path.exists(ts_file)

    def test_phase_synthesize_only(self, module, tmp_config):
        with patch("sys.argv", ["dream-daily.py", "--dry-run", "--phase", "synthesize"]), \
             patch.object(module, "read_sessions", return_value=[]) as mock_read:
            module.main()
            mock_read.assert_called_once()

    def test_full_pipeline_syntax(self, module, tmp_config):
        """完整流水线至少能跑通语法，不抛异常。"""
        with patch("sys.argv", ["dream-daily.py", "--dry-run"]), \
             patch.object(module, "read_sessions", return_value=[]), \
             patch.object(module, "sag_search", return_value=[]):
            module.main()

    def test_synthesize_failure_exits_2_and_no_cursor(self, module, tmp_config, tmp_path, capsys):
        """synthesize 有失败时：退出码 2、游标不推进、飞书不推送。"""
        from tests._helpers import make_session as _make_session
        verdict_dir = tmp_config["cache"]["verdict_dir"]
        os.makedirs(verdict_dir, exist_ok=True)
        ts_file = os.path.join(verdict_dir, "last_run.txt")

        sessions = [_make_session("s1")]
        with patch("sys.argv", ["dream-daily.py"]), \
             patch.object(module, "read_sessions", return_value=sessions), \
             patch.object(module, "call_llm_json", side_effect=Exception("LLM down")), \
             patch.object(module, "sag_health_check", return_value=True), \
             patch.object(module, "subprocess") as mock_sub:
            with pytest.raises(SystemExit) as exc_info:
                module.main()

        assert exc_info.value.code == 2
        # 失败时游标不推进 → last_run.txt 不存在
        assert not os.path.exists(ts_file)
        # 飞书不推送（mock_sub.run 未被调用）
        mock_sub.run.assert_not_called()
        captured = capsys.readouterr()
        assert "业务失败" in captured.err
        assert "✅" not in captured.out

    def test_all_success_advances_cursor(self, module, tmp_config, tmp_path):
        """synthesize 全部成功时：游标正常推进。"""
        from tests._helpers import make_session as _make_session
        verdict_dir = tmp_config["cache"]["verdict_dir"]
        os.makedirs(verdict_dir, exist_ok=True)
        ts_file = os.path.join(verdict_dir, "last_run.txt")

        sessions = [_make_session("s1")]
        with patch("sys.argv", ["dream-daily.py"]), \
             patch.object(module, "read_sessions", return_value=sessions), \
             patch.object(module, "call_llm_json", return_value={"score": 5}), \
             patch.object(module, "call_llm", return_value="# 标题\n内容"), \
             patch.object(module, "sag_health_check", return_value=True), \
             patch.object(module, "sag_ingest", return_value="doc-123"), \
             patch.object(module, "sag_search", return_value=[]), \
             patch.object(module, "subprocess") as mock_sub:
            module.main()

        assert os.path.exists(ts_file)
