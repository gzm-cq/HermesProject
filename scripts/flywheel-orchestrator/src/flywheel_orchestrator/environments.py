"""environments.py — 各子项目 venv 和环境变量封装。

为了避免跨项目 venv 冲突（KT=3.12 / hermes-agent=3.11 / 系统=3.10），
每个 Task 显式声明 python_executable，不用默认 sys.executable。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class PythonEnv:
    """Python 可执行文件路径 + 启动时额外注入的环境变量。"""
    executable: str
    extra_env: Dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None  # 执行 cd 的目录，None = 不切换


# --- Hermes 根路径（WSL/Linux 运行时读取 /root/.hermes）---
def _default_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "/root/.hermes")).resolve()


def _as_str(p: Path) -> str:
    return str(p)


# ===== 子项目 venv 定义（在部署的 WSL 环境中解析）=====

def _home() -> Path:
    return _default_home()


def SYS_PY() -> PythonEnv:
    """系统 python3：health-check、KN baseline、router check、skill-eval。"""
    return PythonEnv(executable="/usr/bin/python3")


def HERMES_AGENT_PY() -> PythonEnv:
    """hermes-agent venv 3.11：memory-cleanup、skillopt runner。"""
    return PythonEnv(executable=_as_str(_home() / "hermes-agent" / "venv" / "bin" / "python"))


def KT_PY() -> PythonEnv:
    """knowledge-tree-builder venv 3.12：KT consolidate、kvector。"""
    kt_root = _home() / "scripts" / "knowledge-tree-builder"
    py = kt_root / "venv" / "bin" / "python"
    return PythonEnv(
        executable=_as_str(py),
        cwd=_as_str(kt_root),
        extra_env={
            "PYTHONPATH": _as_str(kt_root / "src"),
        },
    )


def DREAM_PY() -> PythonEnv:
    """dream-synth：系统 python，但 PYTHONPATH + cwd 指向项目。"""
    dream_root = _home() / "scripts" / "dream-synth"
    return PythonEnv(
        executable="/usr/bin/python3",
        cwd=_as_str(dream_root / "scripts"),
        extra_env={
            "PYTHONPATH": _as_str(dream_root),
        },
    )


def KN_ENV() -> PythonEnv:
    """KN baseline / router-health：KN_PLUGIN_DIR cwd + sys py。"""
    return PythonEnv(
        executable="/usr/bin/python3",
        cwd=_as_str(_home() / "plugins" / "knowledge-navigation"),
    )


def MEMORY_ENV() -> PythonEnv:
    """memory-cleanup：cwd 到项目目录。"""
    mc_root = _home() / "scripts" / "memory-cleanup"
    return PythonEnv(
        executable=_as_str(_home() / "hermes-agent" / "venv" / "bin" / "python"),
        cwd=_as_str(mc_root),
    )


def SKILLOPT_ENV() -> PythonEnv:
    """skillopt-runner：hermes-agent/venv/bin/python + cwd=skillopt 项目。"""
    so_root = _home() / "scripts" / "skillopt-runner"
    return PythonEnv(
        executable=_as_str(_home() / "hermes-agent" / "venv" / "bin" / "python"),
        cwd=_as_str(so_root),
    )


def DAILY_LEARN_ENV() -> PythonEnv:
    """daily-learn：系统 python。"""
    return PythonEnv(executable="/usr/bin/python3")


def HEALTH_ENV() -> PythonEnv:
    """health-check：系统 python，cwd=scripts 根。"""
    return PythonEnv(
        executable="/usr/bin/python3",
        cwd=_as_str(_home() / "scripts"),
    )


def build_cmd(pyenv: PythonEnv, args: List[str]) -> List[str]:
    """构建最终执行命令（不含 env — env 单独传给 subprocess）。"""
    return [pyenv.executable, *args]


def merged_env(pyenv: PythonEnv) -> Dict[str, str]:
    """合并 os.environ + pyenv.extra_env（pyenv 覆盖系统值）。"""
    env: Dict[str, str] = dict(os.environ)
    if pyenv.extra_env:
        env.update(pyenv.extra_env)
    return env
