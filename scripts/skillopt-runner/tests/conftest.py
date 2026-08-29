"""conftest.py — make skillopt_runner importable in test context.

skillopt_runner 依赖 skillopt_sleep（独立子项目）与 Hermes 的 skill_manager_tool。
本模块保证这两者在测试环境中可导入：

1. **优先使用真实的 skillopt_sleep**（本机已部署该子项目时）。这样
   ``test_consolidate_optimizations.py`` 之类的用例能测到真实实现。
2. 真实包不可用时（CI / 干净 checkout），回退到轻量 mock —— 用
   ``types.ModuleType`` 伪造，只提供 skillopt_runner import 所需的符号。

⚠️ 历史坑：mock 包没有 ``__path__``，Python 因而不认为它是 package，
``from skillopt_sleep.consolidate import ...`` 会抛
``ModuleNotFoundError: 'skillopt_sleep' is not a package``。
校验真实实现前请先确认 USE_REAL_SLEEP，或按文件路径加载
（见 test_skillopt_rank_fix.py 的 _load_real_module）。
"""
import importlib
import sys
import types
import pathlib
from dataclasses import dataclass, field
from typing import List
from unittest.mock import MagicMock

import pytest


# 先把两个子项目根目录放进 sys.path，再做可用性探测：
#   skillopt-runner 自身（被 import 的主角）
#   skillopt-sleep（同级子项目，真实实现所在）
_HERE = pathlib.Path(__file__).resolve()
_RUNNER_ROOT = _HERE.parent.parent
_SLEEP_ROOT = _RUNNER_ROOT.parent / "skillopt-sleep"


def _real_sleep_importable() -> bool:
    """判断本机是否有可用的真实 skillopt_sleep 包。"""
    # 注意：conftest 早于任何测试模块执行，此时 sys.path 还没被补全，
    # 必须先插入 skillopt-sleep 根目录，否则探测必然失败。
    for p in (str(_SLEEP_ROOT), str(_RUNNER_ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        mod = importlib.import_module("skillopt_sleep.types")
    except Exception:
        return False
    return hasattr(mod, "SessionDigest") and hasattr(mod, "TaskRecord")


# 真实子项目可用 → 不注入 skillopt_sleep mock，让用例测到真实实现
USE_REAL_SLEEP = _real_sleep_importable()


# ── Mock skillopt_sleep package（仅在真实包不可用时使用）────────────────────

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

if not USE_REAL_SLEEP:
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


# ── 隔离生产反馈账本 ────────────────────────────────────────────────────────
# patch_skill_hermes 每次成功改写都会 append_ledger_event('skillopt_patch', ...)，
# 写入**真实** ledger（/root/.hermes/data/flywheel/ledger.jsonl）。跑测试会往
# 生产账本里塞进大量 test-skill / audit-skill 假记录 —— 它们会被 F-1 反向门控
# （recent_skill_patch_trend）读走，误判「反复打补丁仍不根治」而卡住真实改写。
# 这里把账本写入换成 no-op，测试不再污染生产数据。
try:
    import skillopt_runner as _sr

    if not getattr(_sr, "_LEDGER_MOCKED", False):
        _sr.append_ledger_event = lambda *a, **k: None
        _sr._LEDGER_MOCKED = True
except Exception:  # pragma: no cover - skillopt_runner 导入失败时交由用例报错
    pass
