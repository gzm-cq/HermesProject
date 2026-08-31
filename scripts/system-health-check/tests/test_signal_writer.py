"""
test_signal_writer.py — signal_writer.py 单元测试

覆盖: parse_extra 空格分隔解析, names_to_services 服务→unit映射,
all_ok/needs_agent 判定, 信号文件 JSON 输出完整性。
"""

import importlib.util
import json
import os
import sys

import pytest

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT_PATH = os.path.join(_SCRIPT_DIR, "signal_writer.py")

_spec = importlib.util.spec_from_file_location("signal_writer", _SCRIPT_PATH)
sw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sw)


# ══════════════════════════════════════════════════════════
# 1. parse_extra — 空格分隔的 name:status(detail) 解析
# ══════════════════════════════════════════════════════════
class TestParseExtra:
    def test_empty_string(self):
        assert sw.parse_extra("") == []

    def test_single_ok_entry(self):
        assert sw.parse_extra("smb-mounts:ok") == [
            {"name": "smb-mounts", "status": "ok", "detail": ""}
        ]

    def test_multiple_entries_space_separated(self):
        # bash 侧 EXTRA_CHECKS 是空格分隔拼接
        result = sw.parse_extra("smb-mounts:ok wsl-keepalive:fail mnt_c:not_mounted")
        assert len(result) == 3
        assert result[0] == {"name": "smb-mounts", "status": "ok", "detail": ""}
        assert result[1] == {"name": "wsl-keepalive", "status": "fail", "detail": ""}
        assert result[2] == {"name": "mnt_c", "status": "not_mounted", "detail": ""}

    def test_entry_with_detail(self):
        result = sw.parse_extra("bifrost_llm:fail(16_models)")
        assert result[0] == {"name": "bifrost_llm", "status": "fail", "detail": "16_models"}

    def test_mixed_with_and_without_detail(self):
        result = sw.parse_extra(
            "smb-mounts:ok bifrost_llm:ok(16_models) hindsight_recall:fail(pg=False)"
        )
        assert result[0]["detail"] == ""
        assert result[1] == {"name": "bifrost_llm", "status": "ok", "detail": "16_models"}
        assert result[2]["name"] == "hindsight_recall"
        assert result[2]["status"] == "fail"
        assert result[2]["detail"] == "pg=False"

    def test_trailing_whitespace_tolerated(self):
        result = sw.parse_extra("  smb-mounts:ok   wsl-keepalive:fail  ")
        assert len(result) == 2


# ══════════════════════════════════════════════════════════
# 2. names_to_services — 空格分隔服务名 → {name, unit}
# ══════════════════════════════════════════════════════════
class TestNamesToServices:
    SVC_MAP = {
        "hermes": "hermes-gateway",
        "bifrost": "bifrost",
        "hindsight": "hindsight-daemon",
        "sag": "sag",
        "postgres": "docker",
        "dashboard": "hermes-dashboard",
    }

    def test_empty_string(self):
        assert sw.names_to_services("", self.SVC_MAP) == []

    def test_single_service_with_unit(self):
        result = sw.names_to_services("hermes", self.SVC_MAP)
        assert result == [{"name": "hermes", "unit": "hermes-gateway"}]

    def test_postgres_maps_to_docker(self):
        # postgres 跑在 docker 容器 shared-postgres
        result = sw.names_to_services("postgres", self.SVC_MAP)
        assert result == [{"name": "postgres", "unit": "docker"}]

    def test_unknown_service_gets_empty_unit(self):
        # mcp 等不在 SVC_MAP 的服务 unit 为空，由 agent 自行判断
        result = sw.names_to_services("mcp", self.SVC_MAP)
        assert result == [{"name": "mcp", "unit": ""}]

    def test_multiple_space_separated(self):
        # bash 侧 FAILED_SERVICES 是 ' '.join 空格分隔
        result = sw.names_to_services("bifrost postgres", self.SVC_MAP)
        assert result == [
            {"name": "bifrost", "unit": "bifrost"},
            {"name": "postgres", "unit": "docker"},
        ]


# ══════════════════════════════════════════════════════════
# 3. main() — 端到端信号文件输出
# ══════════════════════════════════════════════════════════
class TestMain:
    def _run_main(self, health_raw, env_overrides, tmp_path):
        """以给定 stdin + env 调用 main()，返回写入的信号 dict。"""
        out_path = tmp_path / "health-signal.json"
        env = {
            "SIGNAL_FAILED": "",
            "SIGNAL_WARN": "",
            "SIGNAL_EXTRA": "",
            "SIGNAL_INFRA_STATUS": "ok",
            "SIGNAL_NOW_ISO": "2026-08-31T08:00:00+0800",
            "SIGNAL_FILE_PATH": str(out_path),
            "SIGNAL_CRON_ERRORS": "",
        }
        env.update(env_overrides)

        old_stdin = sys.stdin
        old_env = {k: os.environ.get(k) for k in env}
        try:
            sys.stdin = type("S", (), {"read": lambda self: json.dumps(health_raw)})()
            os.environ.update(env)
            sw.main()
        finally:
            sys.stdin = old_stdin
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        assert out_path.exists(), "信号文件未写入"
        with open(out_path) as f:
            return json.load(f)

    def test_all_ok(self, tmp_path):
        signal = self._run_main(
            {"hermes": {"status": "ok"}, "bifrost": {"status": "ok"}},
            {
                "SIGNAL_EXTRA": "smb-mounts:ok wsl-keepalive:ok mnt_c:ok mnt_d:ok "
                               "sag-mcp-bridge:ok bifrost_llm:ok(16_models) "
                               "hindsight_recall:ok(pg=True)",
            },
            tmp_path,
        )
        assert signal["all_ok"] is True
        assert signal["needs_agent"] is False
        assert signal["infra_status"] == "ok"
        assert signal["failed_services"] == []
        assert signal["warn_services"] == []
        assert len(signal["extra_checks"]) == 7

    def test_failed_services_detected(self, tmp_path):
        signal = self._run_main(
            {"hermes": {"status": "fail"}, "bifrost": {"status": "ok"}},
            {"SIGNAL_FAILED": "hermes", "SIGNAL_INFRA_STATUS": "fail"},
            tmp_path,
        )
        assert signal["all_ok"] is False
        assert signal["needs_agent"] is True
        assert signal["failed_services"] == [{"name": "hermes", "unit": "hermes-gateway"}]

    def test_extra_check_fail_triggers_agent(self, tmp_path):
        # infra_status=ok 但 extra_checks 有 fail → 仍需 agent
        signal = self._run_main(
            {"hermes": {"status": "ok"}},
            {"SIGNAL_EXTRA": "smb-mounts:ok mnt_c:not_mounted", "SIGNAL_INFRA_STATUS": "warn"},
            tmp_path,
        )
        assert signal["all_ok"] is False
        assert signal["needs_agent"] is True
        assert any(e["name"] == "mnt_c" and e["status"] == "not_mounted"
                   for e in signal["extra_checks"])

    def test_warn_services_recorded(self, tmp_path):
        signal = self._run_main(
            {"mcp": {"status": "warn"}, "hermes": {"status": "ok"}},
            {"SIGNAL_WARN": "mcp", "SIGNAL_INFRA_STATUS": "warn"},
            tmp_path,
        )
        assert signal["warn_services"] == [{"name": "mcp", "unit": ""}]
        assert signal["needs_agent"] is True

    def test_cron_errors_propagated(self, tmp_path):
        signal = self._run_main(
            {"hermes": {"status": "ok"}},
            {"SIGNAL_CRON_ERRORS": "dream-daily:error(exit 2)"},
            tmp_path,
        )
        assert signal["cron_errors"] == "dream-daily:error(exit 2)"

    def test_health_check_raw_embedded(self, tmp_path):
        raw = {"hermes": {"status": "ok", "checks": {"process_alive": True}}}
        signal = self._run_main(raw, {}, tmp_path)
        assert signal["health_check_raw"] == raw

    def test_svc_map_embedded_for_agent(self, tmp_path):
        signal = self._run_main({"hermes": {"status": "ok"}}, {}, tmp_path)
        assert signal["svc_map"]["postgres"] == "docker"
        assert signal["svc_map"]["hermes"] == "hermes-gateway"

    def test_timestamp_from_env(self, tmp_path):
        signal = self._run_main(
            {"hermes": {"status": "ok"}},
            {"SIGNAL_NOW_ISO": "2026-08-31T09:30:00+0800"},
            tmp_path,
        )
        assert signal["timestamp"] == "2026-08-31T09:30:00+0800"

    def test_invalid_json_stdin_falls_back_to_raw(self, tmp_path):
        """stdin 非合法 JSON 时 health_check_raw 保留原文而非崩溃。"""
        out_path = tmp_path / "health-signal.json"
        env = {
            "SIGNAL_FAILED": "",
            "SIGNAL_WARN": "",
            "SIGNAL_EXTRA": "",
            "SIGNAL_INFRA_STATUS": "ok",
            "SIGNAL_NOW_ISO": "2026-08-31T08:00:00+0800",
            "SIGNAL_FILE_PATH": str(out_path),
            "SIGNAL_CRON_ERRORS": "",
        }
        old_stdin = sys.stdin
        old_env = {k: os.environ.get(k) for k in env}
        try:
            sys.stdin = type("S", (), {"read": lambda self: "not json at all"})()
            os.environ.update(env)
            sw.main()
        finally:
            sys.stdin = old_stdin
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        with open(out_path) as f:
            signal = json.load(f)
        assert signal["health_check_raw"] == {"raw": "not json at all"}
        assert signal["all_ok"] is True  # 无 failed/坏 extra → 仍判 ok
