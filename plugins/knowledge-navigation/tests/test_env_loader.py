"""Tests for env_loader: get_env_bool and get_env_list."""

from __future__ import annotations

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _clear_env_cache():
    """每个测试前重置缓存，防止测试间污染。"""
    from knowledge_navigation.core import env_loader
    env_loader._env_cache = {}
    env_loader._env_cache_ts = 0.0
    yield
    env_loader._env_cache = {}
    env_loader._env_cache_ts = 0.0


# ── get_env_bool ──────────────────────────────────────────────────────────────


class TestGetEnvBool:
    @pytest.mark.parametrize("val,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("YES", True),
        ("Yes", True),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("NO", False),
        ("No", False),
    ])
    def test_known_values(self, val, expected):
        with patch.dict("os.environ", {"KEY": val}, clear=False):
            assert env_loader.get_env_bool("KEY", False) is expected

    def test_whitespace_stripped(self):
        with patch.dict("os.environ", {"KEY": "  true  "}, clear=False):
            assert env_loader.get_env_bool("KEY", False) is True

    def test_default_when_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            assert env_loader.get_env_bool("MISSING", True) is True
            assert env_loader.get_env_bool("MISSING", False) is False

    def test_default_when_invalid(self):
        with patch.dict("os.environ", {"KEY": "xyz"}, clear=False):
            assert env_loader.get_env_bool("KEY", True) is True
            assert env_loader.get_env_bool("KEY", False) is False

    def test_default_when_empty_string(self):
        with patch.dict("os.environ", {"KEY": ""}, clear=False):
            assert env_loader.get_env_bool("KEY", False) is False

    def test_none_key(self):
        with patch.dict("os.environ", {}, clear=True):
            assert env_loader.get_env_bool(None, False) is False  # type: ignore[arg-type]


# ── get_env_list ──────────────────────────────────────────────────────────────


class TestGetEnvList:
    def test_comma_separated(self):
        with patch.dict("os.environ", {"KEY": "a,b,c"}, clear=False):
            assert env_loader.get_env_list("KEY", []) == ["a", "b", "c"]

    def test_whitespace_trimmed(self):
        with patch.dict("os.environ", {"KEY": " a , b , c "}, clear=False):
            assert env_loader.get_env_list("KEY", []) == ["a", "b", "c"]

    def test_custom_separator(self):
        with patch.dict("os.environ", {"KEY": "a;b;c"}, clear=False):
            assert env_loader.get_env_list("KEY", [], separator=";") == ["a", "b", "c"]

    def test_single_value(self):
        with patch.dict("os.environ", {"KEY": "only"}, clear=False):
            assert env_loader.get_env_list("KEY", []) == ["only"]

    def test_empty_string_returns_default(self):
        with patch.dict("os.environ", {"KEY": ""}, clear=False):
            assert env_loader.get_env_list("KEY", ["default"]) == ["default"]

    def test_missing_key_returns_default(self):
        with patch.dict("os.environ", {}, clear=True):
            assert env_loader.get_env_list("MISSING", ["a", "b"]) == ["a", "b"]

    def test_consecutive_separators_filtered(self):
        with patch.dict("os.environ", {"KEY": "a,,b"}, clear=False):
            assert env_loader.get_env_list("KEY", []) == ["a", "b"]

    def test_default_is_not_mutated(self):
        default = ["x"]
        with patch.dict("os.environ", {}, clear=True):
            result = env_loader.get_env_list("MISSING", default)
            assert result == default
            assert result is not default  # 返回的是新列表，不影响 default

    def test_none_key(self):
        with patch.dict("os.environ", {}, clear=True):
            assert env_loader.get_env_list(None, ["a"]) == ["a"]  # type: ignore[arg-type]
