"""conftest.py — mock skillopt_sleep imports so skillopt_runner can be imported locally."""
import sys
import types
import pathlib
from dataclasses import dataclass, field
from typing import List
from unittest.mock import MagicMock

import pytest


# ── Mock skillopt_sleep package ──────────────────────────────────────────────

@dataclass
class MockSessionDigest:
    session_id: str = ""
    project: str = ""
    started_at: str = ""
    ended_at: str = ""
    user_prompts: List[str] = field(default_factory=list)
    assistant_finals: List[str] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    files_touched: List[str] = field(default_factory=list)
    feedback_signals: List[str] = field(default_factory=list)
    n_user_turns: int = 0
    n_assistant_turns: int = 0
    raw_path: str = ""


@dataclass
class MockTaskRecord:
    id: str = ""
    intent: str = ""
    context_excerpt: str = ""
    reference: str = ""
    reference_kind: str = ""
    outcome: str = ""
    tags: List[str] = field(default_factory=list)
    system: str = ""
    judge: str = ""


@dataclass
class MockEditRecord:
    target: str = ""
    op: str = ""
    content: str = ""
    anchor: str = ""
    rationale: str = ""


@dataclass
class MockReplayResult:
    response: str = ""
    fail_reason: str = ""


# Build mock modules
_pkg = types.ModuleType("skillopt_sleep")
_types_mod = types.ModuleType("skillopt_sleep.types")
_types_mod.SessionDigest = MockSessionDigest
_types_mod.TaskRecord = MockTaskRecord
_types_mod.EditRecord = MockEditRecord
_types_mod.ReplayResult = MockReplayResult

_mine_mod = types.ModuleType("skillopt_sleep.mine")
_mine_mod.mine = MagicMock(return_value=[])

_config_mod = types.ModuleType("skillopt_sleep.config")
_config_mod.load_config = MagicMock(return_value=MagicMock())
_config_mod.SleepConfig = MagicMock

_cycle_mod = types.ModuleType("skillopt_sleep.cycle")
_cycle_mod.run_sleep_cycle = MagicMock()

sys.modules["skillopt_sleep"] = _pkg
sys.modules["skillopt_sleep.types"] = _types_mod
sys.modules["skillopt_sleep.mine"] = _mine_mod
sys.modules["skillopt_sleep.config"] = _config_mod
sys.modules["skillopt_sleep.cycle"] = _cycle_mod

# ── Mock tools.skill_manager_tool (Hermes 依赖) ─────────────────────────────
_tools_pkg = types.ModuleType("tools")
_tools_mgr = types.ModuleType("tools.skill_manager_tool")
_tools_mgr.skill_manage = MagicMock(return_value={"success": True})
_tools_pkg.skill_manager_tool = _tools_mgr  # make attribute accessible for @patch resolution
sys.modules["tools"] = _tools_pkg
sys.modules["tools.skill_manager_tool"] = _tools_mgr

# Now safe to import skillopt_runner
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
