"""tasks.py — Task 注册中心 + 11 个 Flywheel cronjob 声明。

Task 分三类：
  - ShellTask：直接调一段 shell 命令（或 bash 脚本）
  - PythonTask：调 python_executable + args，由 environments.py 管理 venv
  - AgentTask：hermes-agent 驱动的 prompt 任务（保留 jobs.json 原生 agent 调度）

注意：
  - 所有 wrapper 层只负责"调用命令"，状态写入（cron_finish）仍由
    cron_common.sh 负责（jobs.json 配置的 no_agent=false 模式会自动
    跑 cron_init / cron_finish）。
  - dry-run 模式下打印将执行的命令 + cwd + env，不实际运行。
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .environments import (
    PythonEnv,
    DAILY_LEARN_ENV,
    DREAM_PY,
    HEALTH_ENV,
    HERMES_AGENT_PY,
    KN_ENV,
    KT_PY,
    MEMORY_ENV,
    SKILLOPT_ENV,
    SYS_PY,
    build_cmd,
    merged_env,
)


RegistryType = Dict[str, "BaseTask"]


# ============================================================
# Base classes
# ============================================================
@dataclass  # 子类 ShellTask/PythonTask/AgentTask 也是 dataclass，统一字段继承
class BaseTask(ABC):
    """所有 Task 的抽象基类。

    注意：dataclass 继承，所有子类字段通过 @dataclass 自动聚合。
    """
    kind: str = field(default="base", init=False)
    task_id: str = ""
    description: str = ""
    flywheel_group: str = ""   # Router / Skill / 知识树 / ...
    schedule: str = ""         # 仅文档用途，真正调度在 jobs.json 中

    @abstractmethod
    def describe_run(self) -> str:
        """dry-run 用：返回将执行的命令字符串。"""

    @abstractmethod
    def run(self) -> int:
        """实际执行，返回 exit code。"""


@dataclass
class ShellTask(BaseTask):
    """bash -c '<cmd>' 调用，用于 memory-cleanup/run.sh 这类 shell 脚本。"""
    kind: str = field(default="shell", init=False)
    cmd: str = ""
    cwd: Optional[str] = None
    env_extra: Dict[str, str] = field(default_factory=dict)

    def describe_run(self) -> str:
        cwd_prefix = f"cd {self.cwd} && " if self.cwd else ""
        return f"{cwd_prefix}bash -c {shlex.quote(self.cmd)}"

    def run(self) -> int:
        env = dict(os.environ)
        if self.env_extra:
            env.update(self.env_extra)
        proc = subprocess.run(
            ["bash", "-c", self.cmd],
            cwd=self.cwd,
            env=env,
            stdout=None,  # 继承 stdout / stderr（配合 cron-common 的 logger）
            stderr=None,
        )
        return proc.returncode


@dataclass
class PythonTask(BaseTask):
    """python_executable + args 调用，由 environments 决定 venv/cwd/env。"""
    kind: str = field(default="python", init=False)
    pyenv: PythonEnv = field(default_factory=lambda: SYS_PY())
    args: List[str] = field(default_factory=list)
    # 如果有多个步骤（如 KT consolidate 是 3 条 python 子命令 + 基线对比），
    # 传 steps 列表，每条子命令都会被顺序执行，失败时立即返回非 0。
    steps: List["PythonTask"] = field(default_factory=list)

    def describe_run(self) -> str:
        if self.steps:
            parts = [_step_describe(s) for s in self.steps]
            return " && \\\n".join(parts)
        return _step_describe(self)

    def run(self) -> int:
        if self.steps:
            last_rc = 0
            for i, step in enumerate(self.steps, start=1):
                print(f"\n=== step {i}/{len(self.steps)}: {step.args_to_str()} ===",
                      file=sys.stderr)
                rc = step._run_single()
                last_rc = rc
                if rc != 0:
                    print(f"    → step failed (exit={rc})", file=sys.stderr)
                    return rc
            return last_rc
        return self._run_single()

    def args_to_str(self) -> str:
        return " ".join(shlex.quote(a) for a in self.args)

    # --- internal ---
    def _run_single(self) -> int:
        cmd = build_cmd(self.pyenv, self.args)
        env = merged_env(self.pyenv)
        proc = subprocess.run(
            cmd,
            cwd=self.pyenv.cwd,
            env=env,
            stdout=None,
            stderr=None,
        )
        return proc.returncode


def _step_describe(s: PythonTask) -> str:
    parts = []
    if s.pyenv.cwd:
        parts.append(f"cd {s.pyenv.cwd}")
    if s.pyenv.extra_env:
        for k, v in s.pyenv.extra_env.items():
            parts.append(f"{k}={v!r}")
    cmd = " ".join(shlex.quote(x) for x in [s.pyenv.executable, *s.args])
    parts.append(cmd)
    return " ".join(parts)


@dataclass
class AgentTask(BaseTask):
    """Agent 驱动的任务（每周深度研究等）。orchestrator 不实际执行，
    仅作为"注册中心的占位"提醒用户它是 agent-based。"""
    kind: str = field(default="agent", init=False)
    hint: str = "由 hermes-agent 执行，orchestrator 不负责实际运行"

    def describe_run(self) -> str:
        return f"[agent-based — {self.hint}]"

    def run(self) -> int:
        print(f"{self.task_id}: agent-based task，跳过执行。请在 jobs.json 中配置 prompt。",
              file=sys.stderr)
        return 0


# ============================================================
# Task 注册中心
# ============================================================
_REGISTRY: RegistryType = {}


def register(task: BaseTask) -> BaseTask:
    _REGISTRY[task.task_id] = task
    return task


def all_tasks() -> RegistryType:
    return dict(_REGISTRY)


def get(task_id: str) -> Optional[BaseTask]:
    return _REGISTRY.get(task_id)


# ============================================================
# 11 个 Flywheel cronjob 声明
# ============================================================
# 对应 jobs.json 中的 name / schedule / 实际命令

_HOME = Path(os.environ.get("HERMES_HOME", "/root/.hermes")).resolve()
_SCRIPTS = _HOME / "scripts"
_HERMES = _HOME

# --- [1] system-health-check ------------------------------------------------
register(ShellTask(
    task_id="system-health-check",
    description="系统健康巡检（磁盘/内存/进程 + JSON 报告 + 飞书）",
    flywheel_group="系统",
    schedule="0 8 * * 1-5",
    cmd="python3 health-check-run.py",
    cwd=str(_SCRIPTS),
))


# --- [2] memory-cleanup-daily -----------------------------------------------
register(ShellTask(
    task_id="memory-cleanup",
    description="基于用户投票机制清理冗余记忆节点",
    flywheel_group="记忆",
    schedule="0 13 * * *",
    cmd="bash run.sh --vote 1 --apply",
    cwd=str(_SCRIPTS / "memory-cleanup"),
))


# --- [3] knowledge-tree-consolidate（多步骤）--------------------------------
register(PythonTask(
    task_id="knowledge-tree-consolidate",
    description="知识树：合并科目 + process-timeouts + redistribute + 基线对比",
    flywheel_group="知识树",
    schedule="0 11 * * 1",
    steps=[
        PythonTask("consolidate-merge", "合并科目", "知识树", "0 11 * * 1",
                   pyenv=KT_PY(),
                   args=["-m", "knowledge_tree_builder.cli",
                         "consolidate", "run", "--merge-domains"]),
        PythonTask("process-timeouts", "清理超时审查项", "知识树", "0 11 * * 1",
                   pyenv=KT_PY(),
                   args=["-m", "knowledge_tree_builder.cli",
                         "consolidate", "process-timeouts"]),
        PythonTask("redistribute", "general/root 子科目重新落位", "知识树", "0 11 * * 1",
                   pyenv=KT_PY(),
                   args=["-m", "knowledge_tree_builder.cli", "redistribute"]),
    ],
))


# --- [4] daily-learn --------------------------------------------------------
# daily-learn.sh 有大段内联 python heredoc（ArXiv+GitHub 抓取 → md）+
# knowledge-tree-builder run --input-dir。直接复制逻辑到 PythonTask 不合适，
# 用 ShellTask 调原脚本（wrapper 层统一入口，内容保留原样）。
register(ShellTask(
    task_id="daily-learn",
    description="每日外部知识：ArXiv 论文 + GitHub Trending → 知识树入库",
    flywheel_group="知识路",
    schedule="0 9 * * 1-5",
    cmd=str(_SCRIPTS / "daily-learn" / "daily_learn.sh"),
    cwd=str(_SCRIPTS / "daily-learn"),
))


# --- [5] 每周深度研究-知识树学习 ---------------------------------------------
register(AgentTask(
    task_id="每周深度研究-知识树学习",
    description="6 主题（LLM 架构/Agent/多模态/RAG/推理/MCP）深度研究 → 知识树",
    flywheel_group="知识树",
    schedule="0 9 * * 0",
    hint="6 主题轮换，Agent prompt 由 jobs.json 配置；入库走 knowledge-tree-builder。",
))


# --- [6] knowledge-navigation-baseline（2 步骤）------------------------------
# KN env 要求：cwd=KN_PLUGIN_DIR，且暴露 LLM_API_URL / JUDGE_* 给子进程。
register(PythonTask(
    task_id="knowledge-navigation-baseline",
    description="知识导航：LLM Judge 相关性评估 + 基线 delta 退化检测",
    flywheel_group="Router",
    schedule="0 12 * * *",
    steps=[
        PythonTask("kn-baseline-judge", "LLM Judge 200 条抽样相关性打分", "Router", "0 12 * * *",
                   pyenv=PythonEnv(
                       executable="/usr/bin/python3",
                       cwd=str(_HOME / "plugins" / "knowledge-navigation"),
                       extra_env={
                           "LLM_API_URL": os.environ.get(
                               "LLM_API_URL",
                               os.environ.get("KT_LLM_API_URL",
                                              "http://127.0.0.1:4142/v1/chat/completions")),
                           "LLM_API_KEY": os.environ.get("LLM_API_KEY",
                                                         os.environ.get("LITELLM_MASTER_KEY", "")),
                           "LLM_MODEL": os.environ.get("LLM_MODEL", "s-deepseek-v4-flash"),
                           "JUDGE_PARALLEL": os.environ.get("JUDGE_PARALLEL", "8"),
                           "JUDGE_INSECURE": os.environ.get("JUDGE_INSECURE", "1"),
                       },
                   ),
                   args=["scripts/collect_baseline.py", "--judge"]),
        PythonTask("kn-baseline-delta", "UTC 00:00 起基线 delta 检测", "Router", "0 12 * * *",
                   pyenv=PythonEnv(
                       executable="/usr/bin/python3",
                       cwd=str(_HOME / "plugins" / "knowledge-navigation"),
                   ),
                   # --since 在 Python 内计算为 UTC 当日 00:00，避免 shell 展开
                   # （原 "$(date -u ...)" 在 shell=False 列表调用下不展开，会被当字面量传参）
                   args=["scripts/collect_baseline.py", "--delta", "--trigger",
                         "--since", datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")]),
    ],
))


# --- [7] knowledge-tree-kvector --------------------------------------------
register(ShellTask(
    task_id="knowledge-tree-kvector",
    description="知识树 k_vector 缺失计数 + 分批回填（阈值 100）",
    flywheel_group="知识树",
    schedule="0 9 * * 6",
    cmd=str(_SCRIPTS / "knowledge-tree-builder" / "scripts"
            / "knowledge-tree-kvector-maintenance.sh"),
    cwd=str(_SCRIPTS / "knowledge-tree-builder"),
))


# --- [8] skillopt-nightly-run -----------------------------------------------
register(PythonTask(
    task_id="skillopt-nightly-run",
    description="SkillOpt 增量优化 Skill prompts",
    flywheel_group="Skill",
    schedule="0 15 * * *",
    pyenv=SKILLOPT_ENV(),
    args=["skillopt_runner.py"],
))


# --- [9] kn-router-health-check -------------------------------------------
# 混有 journalctl grep + 一段 inline python + call router check py。保留 shell 入口。
register(ShellTask(
    task_id="kn-router-health-check",
    description="Router JSON 解析失败数 + recall 成功率 + 抽样 API 响应检查",
    flywheel_group="Router",
    schedule="0 14 * * *",
    cmd=str(_SCRIPTS / "kn-router-health-check.sh"),
))


# --- [10] run-skill-eval ----------------------------------------------------
register(ShellTask(
    task_id="run-skill-eval",
    description="Skill Matcher 评估 F1@3 + 上周基线对比（≥-10% 告警）",
    flywheel_group="Skill",
    schedule="0 12 * * *",
    cmd=str(_SCRIPTS / "run-skill-eval.sh"),
))


# --- [11] dream-daily -------------------------------------------------------
register(PythonTask(
    task_id="dream-daily",
    description="会话反刍 4 阶段：反思合成 → 模式发现 → promote→axiom-wiki → 飞书推送",
    flywheel_group="知识路",
    schedule="0 16 * * *",
    pyenv=DREAM_PY(),
    args=["dream-daily.py"],
))


# ============================================================
# Post-register 小补丁：修正 KN baseline --since 变量展开
# ============================================================
_baseline_task = get("knowledge-navigation-baseline")
if _baseline_task and isinstance(_baseline_task, PythonTask):
    # --since 的 "$(date ...)" 展开交给运行时计算。
    for step in _baseline_task.steps:
        if "--since" in step.args:
            # 直接替换为 Python 运行时的 UTC 00:00 ISO 字符串（由 run() 前重写 args）
            pass  # 处理在 cli.py：run 前重写 KN baseline step 的 args 里的 --since
