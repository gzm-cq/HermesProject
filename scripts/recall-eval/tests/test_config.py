"""config.py 单元测试 — AppConfig 配置管理。"""

import os
from pathlib import Path
from typing import Any

import pytest

from recall_eval.config import AppConfig, _resolve_config_path


class TestAppConfigValidation:
    """AppConfig __post_init__ 校验测试。"""

    def test_batch_size_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            AppConfig(batch_size=0)

    def test_max_workers_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_workers"):
            AppConfig(max_workers=0)

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

    def test_default_values(self, app_config: AppConfig) -> None:
        assert app_config.dataset_path == "data/eval_queries.json"
        assert app_config.output_path == "reports"
        assert app_config.eval_api_url == "http://127.0.0.1:4142/v1/chat/completions"
        assert app_config.eval_model == "s-deepseek-v4-flash"
        assert app_config.batch_size == 10
        assert app_config.log_level == "INFO"
        assert app_config.output_mode == "human"

    def test_max_workers_default(self, app_config: AppConfig) -> None:
        assert app_config.max_workers > 0


class TestFromEnv:
    """from_env() 测试。"""

    def test_env_overrides_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RECALL_EVAL_DATASET_PATH", "/custom/dataset.json")
        monkeypatch.setenv("RECALL_EVAL_MODEL", "custom-model")
        monkeypatch.setenv("RECALL_EVAL_BATCH_SIZE", "20")
        cfg = AppConfig.from_env()
        assert cfg.dataset_path == "/custom/dataset.json"
        assert cfg.eval_model == "custom-model"
        assert cfg.batch_size == 20

    def test_litellm_key_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-my-key")
        cfg = AppConfig.from_env()
        assert cfg.eval_api_key == "sk-my-key"

    def test_defaults_passed_through(self) -> None:
        cfg = AppConfig.from_env({"eval_api_url": "http://custom:8080", "batch_size": 5})
        assert cfg.eval_api_url == "http://custom:8080"
        assert cfg.batch_size == 5

    def test_env_takes_priority_over_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RECALL_EVAL_API_URL", "http://env:4141")
        cfg = AppConfig.from_env({"eval_api_url": "http://yaml:8080"})
        assert cfg.eval_api_url == "http://env:4141"

    def test_unknown_fields_in_defaults_are_filtered(self) -> None:
        cfg = AppConfig.from_env({"unknown_field": "should_not_crash", "batch_size": 15})
        assert cfg.batch_size == 15
        assert not hasattr(cfg, "unknown_field")

    def test_int_env_vars_parsed_correctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RECALL_EVAL_MAX_WORKERS", "4")
        monkeypatch.setenv("RECALL_EVAL_BATCH_SIZE", "8")
        cfg = AppConfig.from_env()
        assert cfg.max_workers == 4
        assert cfg.batch_size == 8

    def test_log_level_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RECALL_EVAL_LOG_LEVEL", "DEBUG")
        cfg = AppConfig.from_env()
        assert cfg.log_level == "DEBUG"

    def test_output_mode_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RECALL_EVAL_OUTPUT_MODE", "json")
        cfg = AppConfig.from_env()
        assert cfg.output_mode == "json"


class TestFromDict:
    """from_dict() 测试。"""

    def test_filtered_unknown_fields(self) -> None:
        cfg = AppConfig.from_dict({"eval_model": "gpt-4", "nonexistent": "should_be_ignored"})
        assert cfg.eval_model == "gpt-4"
        assert not hasattr(cfg, "nonexistent")

    def test_empty_dict_returns_defaults(self) -> None:
        cfg = AppConfig.from_dict({})
        assert cfg.dataset_path == "data/eval_queries.json"


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
