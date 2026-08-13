"""pytest fixtures 共享模块。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 确保 knowledge_navigation 在 pytest 下可导入（其 src 位于 workspace 根下的 plugins/）。
_KN_SRC = str(Path(__file__).resolve().parents[3] / "plugins" / "knowledge-navigation" / "src")
if _KN_SRC not in sys.path:
    sys.path.insert(0, _KN_SRC)


@pytest.fixture
def tmp_hermes_home(tmp_path):
    """创建临时 HERMES_HOME 目录结构。"""
    home = tmp_path / "hermes"
    for sub in ["data/flywheel", "cron-state", "cron-log", "logs/reports",
                "memories", "baselines/kn", "baselines/kt", "backups/auto-tuner"]:
        (home / sub).mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text("# test env\n", encoding="utf-8")
    return home
