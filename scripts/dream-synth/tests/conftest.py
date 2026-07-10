"""pytest fixtures for dream-synth tests."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_DIR / "scripts"

spec = importlib.util.spec_from_file_location("dream_daily", SCRIPT_DIR / "dream-daily.py")
dream_daily = importlib.util.module_from_spec(spec)
sys.modules["dream_daily"] = dream_daily
spec.loader.exec_module(dream_daily)


@pytest.fixture
def module():
    return dream_daily


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """用临时目录的 config 替换全局 CFG."""
    verdict_dir = tmp_path / "verdicts"
    promote_log = tmp_path / "promote-log.json"
    db_path = tmp_path / "state.db"

    cfg = {
        "session": {
            "min_messages": 2,
            "min_tokens": 10,
            "db_path": str(db_path),
        },
        "llm": {
            "cheap": "test-cheap",
            "smart": "test-smart",
            "base_url": "http://test-llm:4142",
        },
        "sag": {
            "base_url": "http://test-sag:4173",
        },
        "feishu": {
            "chat_id": "oc_test",
        },
        "wiki": {
            "base_path": str(tmp_path / "wiki"),
        },
        "cache": {
            "verdict_dir": str(verdict_dir),
            "promote_log": str(promote_log),
        },
    }

    config_file = tmp_path / "config.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True)

    monkeypatch.setattr(dream_daily, "CONFIG_PATH", config_file)
    monkeypatch.setattr(dream_daily, "CFG", cfg)
    monkeypatch.setattr(dream_daily, "PROMPTS_DIR", PROJECT_DIR / "prompts")

    return cfg
