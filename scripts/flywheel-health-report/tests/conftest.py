"""pytest fixtures 共享模块。"""

from __future__ import annotations

import pytest


@pytest.fixture
def tmp_hermes_home(tmp_path):
    """创建临时 HERMES_HOME 目录结构。"""
    home = tmp_path / "hermes"
    for sub in ["data/flywheel", "cron-state", "cron-log", "logs/reports",
                "memories", "baselines/kn", "baselines/kt", "backups/auto-tuner"]:
        (home / sub).mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text("# test env\n", encoding="utf-8")
    return home
