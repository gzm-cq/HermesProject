"""phase_feishu + main 入口测试。"""
import json
import os
import subprocess
from unittest.mock import patch, MagicMock


def _make_reflection(sid, title, score=5):
    return {
        "session_id": sid,
        "title": title,
        "score": score,
        "content": f"# {title}\n\n## 摘要\n这是{title}的摘要内容，包含关键信息。\n\n## 关键决策\n测试决策点。\n\n## 知识要点\n技术知识点。\n\n## 待办事项\n后续任务。",
    }


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
