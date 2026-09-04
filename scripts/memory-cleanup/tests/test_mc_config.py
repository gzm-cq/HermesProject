"""config.py 单元测试 — AppConfig 配置管理。"""

import os
from pathlib import Path
from typing import Any

import pytest

from memory_cleanup.config import AppConfig, _resolve_config_path


class TestAppConfigValidation:
    """AppConfig __post_init__ 校验测试。"""

    def test_batch_size_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            AppConfig(batch_size=0)

    def test_max_workers_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_workers"):
            AppConfig(max_workers=0)

    def test_memory_char_limit_minimum(self) -> None:
        with pytest.raises(ValueError, match="memory_char_limit"):
            AppConfig(memory_char_limit=50)

    def test_user_char_limit_minimum(self) -> None:
        with pytest.raises(ValueError, match="user_char_limit"):
            AppConfig(user_char_limit=50)

    def test_invalid_output_mode(self) -> None:
        with pytest.raises(ValueError, match="output_mode"):
            AppConfig(output_mode="invalid")

    def test_valid_output_mode_json(self) -> None:
        cfg = AppConfig(output_mode="json")
        assert cfg.output_mode == "json"

    def test_valid_output_mode_human(self) -> None:
        cfg = AppConfig(output_mode="human")
        assert cfg.output_mode == "human"


class TestAppConfigDefaults:
    """AppConfig 默认值测试。"""

    def test_default_values(self) -> None:
        cfg = AppConfig()
        assert cfg.memory_path == "/root/.hermes/memories/MEMORY.md"
        assert cfg.user_path == "/root/.hermes/memories/USER.md"
        assert cfg.llm_url == "http://127.0.0.1:4142/v1/chat/completions"
        assert cfg.llm_model == "s-deepseek-v4-flash"
        assert cfg.batch_size == 10
        assert cfg.memory_char_limit == 50000
        assert cfg.user_char_limit == 15000
        assert cfg.log_level == "INFO"
        assert cfg.entry_delimiter == "\n§\n"

    def test_max_workers_default(self) -> None:
        cfg = AppConfig()
        assert cfg.max_workers > 0


class TestFromEnv:
    """from_env() 测试。"""

    def test_env_overrides_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_CLEANUP_MEMORY_PATH", "/custom/memory.md")
        monkeypatch.setenv("MEMORY_CLEANUP_LLM_MODEL", "custom-model")
        monkeypatch.setenv("MEMORY_CLEANUP_BATCH_SIZE", "10")
        cfg = AppConfig.from_env()
        assert cfg.memory_path == "/custom/memory.md"
        assert cfg.llm_model == "custom-model"
        assert cfg.batch_size == 10

    def test_litellm_key_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-my-key")
        cfg = AppConfig.from_env()
        assert cfg.llm_key == "sk-my-key"

    def test_defaults_passed_through(self) -> None:
        cfg = AppConfig.from_env({"llm_url": "http://custom:8080", "batch_size": 5})
        assert cfg.llm_url == "http://custom:8080"
        assert cfg.batch_size == 5

    def test_env_takes_priority_over_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_CLEANUP_LLM_URL", "http://env:4141")
        cfg = AppConfig.from_env({"llm_url": "http://yaml:8080"})
        assert cfg.llm_url == "http://env:4141"

    def test_unknown_fields_in_defaults_are_filtered(self) -> None:
        """修复验证：未知字段不会导致 TypeError。"""
        cfg = AppConfig.from_env({"unknown_field": "should_not_crash", "batch_size": 15})
        assert cfg.batch_size == 15
        assert not hasattr(cfg, "unknown_field")

    def test_int_env_vars_parsed_correctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_CLEANUP_MAX_WORKERS", "4")
        monkeypatch.setenv("MEMORY_CLEANUP_MEMORY_CHAR_LIMIT", "30000")
        cfg = AppConfig.from_env()
        assert cfg.max_workers == 4
        assert cfg.memory_char_limit == 30000

    def test_log_level_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_CLEANUP_LOG_LEVEL", "DEBUG")
        cfg = AppConfig.from_env()
        assert cfg.log_level == "DEBUG"

    def test_output_mode_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEMORY_CLEANUP_OUTPUT_MODE", "json")
        cfg = AppConfig.from_env()
        assert cfg.output_mode == "json"

    def test_output_mode_default_human(self) -> None:
        cfg = AppConfig.from_env()
        assert cfg.output_mode == "human"


class TestFromDict:
    """from_dict() 测试。"""

    def test_filtered_unknown_fields(self) -> None:
        cfg = AppConfig.from_dict({"llm_model": "gpt-4", "nonexistent": "should_be_ignored"})
        assert cfg.llm_model == "gpt-4"
        assert not hasattr(cfg, "nonexistent")

    def test_empty_dict_returns_defaults(self) -> None:
        cfg = AppConfig.from_dict({})
        assert cfg.memory_path == "/root/.hermes/memories/MEMORY.md"


class TestResolveConfigPath:
    """_resolve_config_path() 测试。"""

    def test_absolute_path(self, tmp_path: Any) -> None:
        p = tmp_path / "config.yaml"
        p.write_text("key: value", encoding="utf-8")
        result = _resolve_config_path(str(p))
        assert result == p.resolve()

    def test_relative_path_exists(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        orig_dir = Path.cwd()
        try:
            os.chdir(str(tmp_path))
            p = tmp_path / "myconfig.yaml"
            p.write_text("key: value", encoding="utf-8")
            result = _resolve_config_path("myconfig.yaml")
            assert result == p.resolve()
        finally:
            os.chdir(str(orig_dir))

    def test_not_found_returns_resolved(self) -> None:
        result = _resolve_config_path("/definitely/not/exists.yaml")
        assert str(result).endswith("exists.yaml")


class TestKeywordBackfillConfig:
    """keyword_backfill 相关配置测试。"""

    def test_default_keyword_backfill_true(self) -> None:
        """keyword_backfill 默认值应为 true。"""
        cfg = AppConfig()
        assert cfg.keyword_backfill is True

    def test_default_hindsight_keyword_count(self) -> None:
        """hindsight_keyword_count 默认值应为 5。"""
        cfg = AppConfig()
        assert cfg.hindsight_keyword_count == 5

    def test_hindsight_keyword_count_min_boundary(self) -> None:
        """hindsight_keyword_count 不能小于 3。"""
        with pytest.raises(ValueError, match="hindsight_keyword_count"):
            AppConfig(hindsight_keyword_count=2)

    def test_hindsight_keyword_count_max_boundary(self) -> None:
        """hindsight_keyword_count 不能大于 8。"""
        with pytest.raises(ValueError, match="hindsight_keyword_count"):
            AppConfig(hindsight_keyword_count=9)

    def test_hindsight_keyword_count_valid_values(self) -> None:
        """hindsight_keyword_count 在 3-8 范围内应有效。"""
        for n in (3, 5, 8):
            cfg = AppConfig(hindsight_keyword_count=n)
            assert cfg.hindsight_keyword_count == n

    def test_keyword_backfill_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量 MEMORY_CLEANUP_KEYWORD_BACKFILL 应生效。"""
        monkeypatch.setenv("MEMORY_CLEANUP_KEYWORD_BACKFILL", "false")
        cfg = AppConfig.from_env()
        assert cfg.keyword_backfill is False

    def test_hindsight_keyword_count_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量 MEMORY_CLEANUP_HINDSIGHT_KEYWORD_COUNT 应生效。"""
        monkeypatch.setenv("MEMORY_CLEANUP_HINDSIGHT_KEYWORD_COUNT", "6")
        cfg = AppConfig.from_env()
        assert cfg.hindsight_keyword_count == 6

    def test_keyword_backfill_from_dict(self) -> None:
        """from_dict 应支持 keyword_backfill。"""
        cfg = AppConfig.from_dict({"keyword_backfill": False})
        assert cfg.keyword_backfill is False

    def test_hindsight_keyword_count_from_dict(self) -> None:
        """from_dict 应支持 hindsight_keyword_count。"""
        cfg = AppConfig.from_dict({"hindsight_keyword_count": 7})
        assert cfg.hindsight_keyword_count == 7


class TestCapacitySafeRatioValidation:
    """memory_capacity_safe_ratio 校验测试（2026-09-04 容量守卫）。"""

    def test_ratio_zero_invalid(self) -> None:
        with pytest.raises(ValueError, match="memory_capacity_safe_ratio"):
            AppConfig(memory_capacity_safe_ratio=0.0)

    def test_ratio_negative_invalid(self) -> None:
        with pytest.raises(ValueError, match="memory_capacity_safe_ratio"):
            AppConfig(memory_capacity_safe_ratio=-0.1)

    def test_ratio_over_one_invalid(self) -> None:
        with pytest.raises(ValueError, match="memory_capacity_safe_ratio"):
            AppConfig(memory_capacity_safe_ratio=1.1)

    def test_ratio_one_valid(self) -> None:
        cfg = AppConfig(memory_capacity_safe_ratio=1.0)
        assert cfg.memory_capacity_safe_ratio == 1.0

    def test_ratio_default(self) -> None:
        cfg = AppConfig()
        assert cfg.memory_capacity_safe_ratio == 0.85

    def test_cold_memory_days_default_60(self) -> None:
        cfg = AppConfig()
        assert cfg.cold_memory_days == 60

    def test_protected_keywords_present(self) -> None:
        cfg = AppConfig()
        assert "偏好" in cfg.lifecycle_protected_keywords
        assert "user wants" in cfg.lifecycle_protected_keywords

    def test_ratio_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("MEMORY_CLEANUP_MEMORY_CAPACITY_SAFE_RATIO", "0.9")
        cfg = AppConfig.from_env()
        assert cfg.memory_capacity_safe_ratio == 0.9
