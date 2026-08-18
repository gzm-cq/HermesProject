"""
test_health_check_all.py — 全量健康巡检单元测试

覆盖核心函数：count_processes, detect_duplicate_processes, run, _psql,
write_check, 以及 check_postgres / check_mcp 中 split("\n") 修复验证。
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch, MagicMock, call

import pytest

# ── 待测模块 ──────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT_PATH = os.path.join(_SCRIPT_DIR, "health-check-all.py")

_spec = importlib.util.spec_from_file_location("health_check_all", _SCRIPT_PATH)
hca = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hca)


# ══════════════════════════════════════════════════════════
# 1. run() — 通用命令执行
# ══════════════════════════════════════════════════════════
class TestRun:
    def test_string_cmd_uses_shell(self):
        """字符串命令默认 shell=True"""
        with patch.object(hca, "subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
            hca.run("echo hello")
            assert mock_sp.run.call_args[0][0] == "echo hello"
            assert mock_sp.run.call_args[1]["shell"] is True

    def test_list_cmd_uses_no_shell(self):
        """列表命令默认 shell=False"""
        with patch.object(hca, "subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
            hca.run(["ls", "-la"])
            assert mock_sp.run.call_args[0][0] == ["ls", "-la"]
            assert mock_sp.run.call_args[1]["shell"] is False

    def test_timeout_returns_error(self):
        """超时返回空字符串和 -1"""
        with patch.object(hca, "subprocess") as mock_sp:
            mock_sp.TimeoutExpired = subprocess.TimeoutExpired
            mock_sp.run.side_effect = subprocess.TimeoutExpired("cmd", 10)
            stdout, stderr, rc = hca.run("sleep 100", timeout=1)
            assert stdout == ""
            assert stderr == "timeout"
            assert rc == -1

    def test_exception_returns_error(self):
        """异常返回错误信息"""
        stdout, stderr, rc = hca.run("/nonexistent/command_xyz123")
        assert stdout == ""
        # shell=True 时 shell 自身返回非零（Linux=127, Windows=1），不是 -1
        assert rc in (127, 1, -1)


# ══════════════════════════════════════════════════════════
# 2. _psql() — 数据库查询执行
# ══════════════════════════════════════════════════════════
class TestPsql:
    def test_basic_command(self):
        """_psql 构造正确的 psql 命令"""
        with patch.object(hca, "subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(stdout="1", stderr="", returncode=0)
            hca._psql("mydb", "SELECT 1", host="10.0.0.1", port=5432,
                       user="admin", timeout=5)
            args, kwargs = mock_sp.run.call_args
            cmd = args[0]
            assert cmd[0] == "psql"
            assert "-h" in cmd and cmd[cmd.index("-h") + 1] == "10.0.0.1"
            assert "-p" in cmd and cmd[cmd.index("-p") + 1] == "5432"
            assert "-U" in cmd and cmd[cmd.index("-U") + 1] == "admin"
            assert "-d" in cmd and cmd[cmd.index("-d") + 1] == "mydb"
            assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "SELECT 1"
            assert kwargs["timeout"] == 5

    def test_password_from_env(self):
        """PGPASSWORD 环境变量传入 env"""
        with patch.object(hca, "subprocess") as mock_sp, \
             patch.dict(os.environ, {"PGPASSWORD": "secret123"}):
            mock_sp.run.return_value = MagicMock(stdout="1", stderr="", returncode=0)
            hca._psql("mydb", "SELECT 1")
            _, kwargs = mock_sp.run.call_args
            assert kwargs["env"]["PGPASSWORD"] == "secret123"


# ══════════════════════════════════════════════════════════
# 3. write_check() — 写入 JSON 文件
# ══════════════════════════════════════════════════════════
class TestWriteCheck:
    def test_writes_json_to_tmpdir(self):
        """write_check 写入正确的 JSON 文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(hca, "TMPDIR", tmpdir):
                hca.write_check("test_svc", "ok", {"key": "val"})
                filepath = os.path.join(tmpdir, "test_svc.json")
                assert os.path.exists(filepath)
                with open(filepath) as f:
                    data = json.load(f)
                    assert data["status"] == "ok"
                    assert data["checks"] == {"key": "val"}


# ══════════════════════════════════════════════════════════
# 4. count_processes() — 进程计数
# ══════════════════════════════════════════════════════════
class TestCountProcesses:
    def test_basic_count(self):
        """匹配的进程数正确"""
        ps_output = (
            "12345 python3 /usr/bin/hermes_cli gateway\n"
            "12346 python3 /usr/bin/hermes_cli gateway\n"
            "99999 sleep 1000\n"
        )
        with patch.object(hca, "run") as mock_run:
            mock_run.return_value = (ps_output, "", 0)
            count = hca.count_processes(r"hermes_cli.*gateway")
            assert count == 2

    def test_excludes_self_and_parent(self):
        """排除当前脚本 PID 和父 PID"""
        ps_output = (
            f"{hca._SCRIPT_PID} python3 /usr/bin/hermes_cli gateway\n"
            f"{hca._PARENT_PID} bash -c hermes_cli\n"
            "12345 python3 /usr/bin/hermes_cli gateway\n"
        )
        with patch.object(hca, "run") as mock_run:
            mock_run.return_value = (ps_output, "", 0)
            count = hca.count_processes(r"hermes_cli")
            assert count == 1

    def test_excludes_postgres_backend(self):
        """排除 postgres: 开头的后端连接"""
        ps_output = (
            "12345 postgres: autovacuum worker\n"
            "12346 python3 /usr/bin/hermes_cli gateway\n"
        )
        with patch.object(hca, "run") as mock_run:
            mock_run.return_value = (ps_output, "", 0)
            count = hca.count_processes(r"hermes_cli")
            assert count == 1

    def test_empty_output(self):
        """空输出返回 0"""
        with patch.object(hca, "run") as mock_run:
            mock_run.return_value = ("", "", 0)
            assert hca.count_processes(r"anything") == 0


# ══════════════════════════════════════════════════════════
# 5. detect_duplicate_processes() — 重复进程检测
# ══════════════════════════════════════════════════════════
class TestDetectDuplicate:
    def test_single_process_not_duplicate(self):
        """单个进程不算重复"""
        ps_output = "12345 1 python3 /usr/bin/some-server --config\n"
        with patch.object(hca, "run") as mock_run:
            mock_run.return_value = (ps_output, "", 0)
            assert hca.detect_duplicate_processes(r"some-server") is False

    def test_granian_workers_parent_child(self):
        """granian parent + child 结构不算重复"""
        ps_output = (
            "10001 1 python3 -m granian --workers 4 main:app\n"
            "20001 10001 python3 -m granian --workers 4 main:app\n"
            "20002 10001 python3 -m granian --workers 4 main:app\n"
        )
        with patch.object(hca, "run") as mock_run:
            mock_run.return_value = (ps_output, "", 0)
            assert hca.detect_duplicate_processes(r"granian") is False

    def test_multiple_roots_is_duplicate(self):
        """多个根进程（无父子关系）= 真的重复"""
        ps_output = (
            "10001 1 python3 -m litellm --config config.yaml\n"
            "10002 1 python3 -m litellm --config config.yaml\n"
        )
        with patch.object(hca, "run") as mock_run:
            mock_run.return_value = (ps_output, "", 0)
            assert hca.detect_duplicate_processes(r"litellm") is True


# ══════════════════════════════════════════════════════════
# 6. check_postgres() — split("\n") 修复验证
# ══════════════════════════════════════════════════════════
class TestCheckPostgres:
    @patch.object(hca, "_psql")
    @patch.object(hca, "run")
    @patch.object(hca, "write_check")
    def test_split_newline_db_list(self, mock_write, mock_run, mock_psql):
        """
        BUG 验证：_psql 返回多行 stdout（含真实换行符 \n），
        之前用 split("\\\\n") 字面量反斜杠-n 导致无法分割。
        修复后用 split("\\n") 正确分割。
        """
        # docker ps → 返回容器名
        # df -h → 返回磁盘使用率
        mock_run.side_effect = [
            ("shared-postgres", "", 0),  # docker ps
            ("45", "", 0),               # df -h
        ]

        # 模拟 _psql 返回三条数据库名（含真实换行符）
        def psql_side_effect(db, query, **kwargs):
            if "datname" in query:
                return ("postgres\nhindsight\nsag_lite", "", 0)
            elif "count" in query:
                return ("5", "", 0)
            elif "vector" in query:
                return ("1", "", 0)
            return ("", "", 0)

        mock_psql.side_effect = psql_side_effect

        hca.check_postgres()

        # 验证 write_check 被调用，数据库列表包含三个库
        call_args = mock_write.call_args
        assert call_args is not None
        name, status, checks = call_args[0]
        assert name == "postgres"
        assert checks["databases"] == ["postgres", "hindsight", "sag_lite"]
        assert checks["process_alive"] is True

    @patch.object(hca, "_psql")
    @patch.object(hca, "run")
    @patch.object(hca, "write_check")
    def test_empty_db_list(self, mock_write, mock_run, mock_psql):
        """_psql 返回空字符串时数据库列表为空"""
        mock_run.side_effect = [
            ("shared-postgres", "", 0),  # docker ps
            ("45", "", 0),               # df -h
        ]
        mock_psql.side_effect = [
            ("5", "", 0),                # active_connections
            ("", "", 0),                 # databases — empty
            ("0", "", 0),                # pgvector
        ]

        hca.check_postgres()

        call_args = mock_write.call_args
        assert call_args is not None
        _, _, checks = call_args[0]
        assert checks["databases"] == []


# ══════════════════════════════════════════════════════════
# 7. check_mcp() — split("\n") 修复 + MCP 逻辑
# ══════════════════════════════════════════════════════════
class TestCheckMCP:
    @patch.object(hca, "count_processes")
    @patch.object(hca, "run")
    @patch.object(hca, "write_check")
    def test_grep_output_split_newline(self, mock_write, mock_run, mock_count):
        """
        BUG 验证：grep -c 返回多行输出（含真实换行符 \n），
        之前用 split('\\\\n') 字面量反斜杠-n 导致 config_present 判断错误。
        修复后用 split('\\n') 正确取最后一行。
        """
        # 模拟 grep 返回多行结果（含换行符）
        mock_run.side_effect = [
            ("12345", "", 0),               # systemctl show hermes-gateway MainPID
            ("42\n42\n42\n42\n42\n", "", 0), # grep -c mcp_servers: — 5 行结果
            ("active", "", 0),               # systemctl is-active sag.service
            ("", "", 0),                     # ps -eo pid=,args= (empty)
            ("000", "", 0),                 # curl windows-mcp
        ]
        mock_count.side_effect = lambda pat: 1

        hca.check_mcp()

        call_args = mock_write.call_args
        assert call_args is not None
        name, status, checks = call_args[0]
        assert name == "mcp"
        # grep 返回 5 行，每行 42，取最后一行 42 → 42 > 0 → True
        assert checks["config_present"] is True

    @patch.object(hca, "count_processes")
    @patch.object(hca, "run")
    @patch.object(hca, "write_check")
    def test_mcp_pid_list_split_newline(self, mock_write, mock_run, mock_count):
        """
        BUG 验证：ps -eo pid=,args= 输出含真实换行符，
        之前用 split('\\\\n') 字面量反斜杠-n 导致无法遍历进程列表。
        修复后用 split('\\n') 正确遍历。
        """
        ps_output = (
            "12345 node /usr/bin/axiom-wiki-mcp-sse.mjs\n"
            "12346 node /usr/bin/postgres-mcp-sse.mjs\n"
            "12347 node /root/.codegraph/versions/v1.0.1/node "
            "/root/.codegraph/versions/v1.0.1/lib/dist/bin/codegraph.js serve --mcp --path /mnt/d/HermesProject\n"
        )
        mock_run.side_effect = [
            ("12345", "", 0),               # systemctl show hermes-gateway MainPID
            ("3", "", 0),                    # grep -c mcp_servers:
            ("active", "", 0),               # systemctl is-active sag.service
            (ps_output, "", 0),             # ps -eo pid=,args=
            ("000", "", 0),                 # curl windows-mcp
        ]
        mock_count.side_effect = lambda pat: 1

        hca.check_mcp()

        call_args = mock_write.call_args
        assert call_args is not None
        name, status, checks = call_args[0]
        assert name == "mcp"
        # 3 个 MCP pid 应该被正确解析
        assert len(checks["mcp_pids"]) == 3
        assert checks["mcp_pids"] == [12345, 12346, 12347]

    @patch.object(hca, "write_check")
    @patch.object(hca, "run")
    @patch.object(hca, "count_processes")
    def test_grep_single_line_result(self, mock_count, mock_run, mock_write):
        """grep -c 单行结果（无换行符）也能正确解析"""
        mock_run.side_effect = [
            ("12345", "", 0),               # systemctl show hermes-gateway MainPID
            ("3", "", 0),                    # grep -c mcp_servers: — 单行
            ("active", "", 0),               # systemctl is-active sag.service
            ("", "", 0),                     # ps -eo pid=,args= (empty)
            ("000", "", 0),                 # curl windows-mcp
        ]
        mock_count.side_effect = lambda pat: 1

        hca.check_mcp()

        call_args = mock_write.call_args
        assert call_args is not None
        _, _, checks = call_args[0]
        assert checks["config_present"] is True


# ══════════════════════════════════════════════════════════
# 8. check_hindsight() / check_sag() — _psql split 修复
# ══════════════════════════════════════════════════════════
class TestCheckHindsight:
    @patch.object(hca, "_psql")
    @patch.object(hca, "count_processes")
    @patch.object(hca, "run")
    @patch.object(hca, "write_check")
    def test_pg_connection_ok(self, mock_write, mock_run, mock_count, mock_psql):
        """_psql SELECT 1 返回 '1' → pg_connection=True"""
        mock_run.side_effect = [
            ("enabled", "", 0),   # systemctl is-enabled
            ("active", "", 0),    # systemctl is-active
            ("Mon 2026-08-17 08:10:07 CST", "", 0),  # systemctl show ActiveEnterTimestamp
            ("http://200", "", 0),  # curl health
        ]
        mock_count.return_value = 1
        mock_psql.return_value = ("1", "", 0)

        hca.check_hindsight()

        call_args = mock_write.call_args
        assert call_args is not None
        _, _, checks = call_args[0]
        assert checks["pg_connection"] is True


class TestCheckSAG:
    @patch.object(hca, "_psql")
    @patch.object(hca, "run")
    @patch.object(hca, "write_check")
    def test_pg_connection_ok(self, mock_write, mock_run, mock_psql):
        """_psql SELECT 1 返回 '1' → pg_connection=True"""
        mock_run.side_effect = [
            ("12345", "", 0),    # systemctl show sag.service
            ("200", "", 0),      # curl SAG API /api/v1/system/health
            ("12346", "", 0),    # systemctl show sag-mcp.service
            ("200", "", 0),      # curl sag-mcp
        ]
        mock_psql.return_value = ("1", "", 0)

        hca.check_sag()

        call_args = mock_write.call_args
        assert call_args is not None
        _, _, checks = call_args[0]
        assert checks["pg_connection"] is True


# ══════════════════════════════════════════════════════════
# 9. get_systemd_pids() — 缓存系统服务 PID
# ══════════════════════════════════════════════════════════
class TestGetSystemdPids:
    def test_parses_multiline_output(self):
        """多行 systemd PID 输出被正确解析"""
        output = "12345\n0\n\n67890\nabc\n"
        with patch.object(hca, "run") as mock_run:
            mock_run.return_value = (output, "", 0)
            hca._SYSTEMD_PIDS = None
            pids = hca.get_systemd_pids()
            assert 12345 in pids
            assert 67890 in pids
            assert 0 not in pids

    def test_caching(self):
        """第二次调用不走 run"""
        with patch.object(hca, "run") as mock_run:
            mock_run.return_value = ("12345", "", 0)
            hca._SYSTEMD_PIDS = None
            pids1 = hca.get_systemd_pids()
            pids2 = hca.get_systemd_pids()
            assert mock_run.call_count == 1
            assert pids1 == pids2