"""flywheel-orchestrator CLI。

用法:
  list                                列出 11 个任务
  run      <task_id> [--home PATH]    实际运行任务（返回 exit code）
  dry-run  <task_id|all> [--home PATH]  打印将执行的命令，不实际运行

说明:
  - cron_init / cron_finish 的 state 文件写入仍由 cron_common.sh 负责。
    orchestrator 只负责统一"调用什么"，不替代 cron-common 的生命周期管理。
  - jobs.json 的 script 字段必须是脚本文件路径（相对 ~/.hermes/scripts/），
    不支持直接写 PYTHONPATH=... python3 -m 命令行的格式。
    如需使用 orchestrator 统一调度，应创建 wrapper 脚本调用
    `python3 -m flywheel_orchestrator.cli run <task_id>`。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path
from typing import List

# 避免 tasks.py 注册 import 时依赖不存在 — 先尝试，报错给用户可见的提示
try:
    from . import tasks as _tasks  # noqa: F401  — 触发注册副作用
    from .tasks import AgentTask, BaseTask, PythonTask, ShellTask, all_tasks, get
except Exception as exc:  # pragma: no cover - 真实环境里不会触发
    print(f"[orchestrator] 初始化失败: {exc}", file=sys.stderr)
    raise


# ============================================================
# 运行时变量替换
# ============================================================
def _apply_runtime_substitutions(task: BaseTask) -> None:
    """在 run/dry-run 前把"运行时才能确定"的参数重写到 task。"""
    if isinstance(task, PythonTask) and task.task_id == "knowledge-navigation-baseline":
        # --since: UTC 当天 00:00 ISO
        today_utc00 = _dt.datetime.now(_dt.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0).isoformat()
        for step in task.steps:
            try:
                idx = step.args.index("--since")
            except ValueError:
                continue
            if idx + 1 < len(step.args):
                step.args[idx + 1] = today_utc00


# ============================================================
# 子命令实现
# ============================================================
def cmd_list(home: Path) -> int:
    tasks = all_tasks()
    order = sorted(tasks.keys(), key=lambda k: (
        tasks[k].flywheel_group, tasks[k].task_id,
    ))
    print(f"{'TASK ID':40s}  {'FLYWHEEL':8s}  {'SCHEDULE':16s}  KIND    DESCRIPTION")
    print("-" * 130)
    for tid in order:
        t = tasks[tid]
        print(f"{tid:40s}  {t.flywheel_group:8s}  {t.schedule:16s}  "
              f"{t.kind:7s} {t.description}")
    print(f"\n合计: {len(tasks)} 个任务")
    return 0


def cmd_run(task_id: str, home: Path) -> int:
    task = get(task_id)
    if task is None:
        print(f"[orchestrator] 未知任务: {task_id}。执行 'list' 查看所有任务。",
              file=sys.stderr)
        return 2
    _apply_runtime_substitutions(task)
    if isinstance(task, AgentTask):
        return task.run()
    return task.run()


def cmd_dry_run(target: str, home: Path) -> int:
    if target == "all":
        ids = sorted(all_tasks().keys())
    else:
        if get(target) is None:
            print(f"[orchestrator] 未知任务: {target}", file=sys.stderr)
            return 2
        ids = [target]

    for i, tid in enumerate(ids, start=1):
        task = get(tid)
        assert task is not None
        _apply_runtime_substitutions(task)
        print(f"\n{'='*70}")
        print(f"[{i}/{len(ids)}] {task.task_id}  ({task.flywheel_group}, {task.schedule})")
        print(f"    {task.description}")
        print(f"    kind: {task.kind}")
        print(f"    command:")
        desc = task.describe_run()
        for line in desc.splitlines():
            print(f"      {line}")
    return 0


# ============================================================
# argparse
# ============================================================
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m flywheel_orchestrator.cli",
        description="Flywheel 11 cronjob 统一编排入口",
    )
    p.add_argument("--home", type=str,
                   default=os.environ.get("HERMES_HOME", "/root/.hermes"),
                   help="Hermes 根目录（默认 $HERMES_HOME 或 /root/.hermes）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="列出所有任务")
    sp.set_defaults(func=lambda a: cmd_list(Path(a.home).resolve()))

    sp = sub.add_parser("run", help="实际运行任务")
    sp.add_argument("task_id")
    sp.set_defaults(func=lambda a: cmd_run(a.task_id, Path(a.home).resolve()))

    sp = sub.add_parser("dry-run", help="打印将执行的命令（不传 all 打印全部）")
    sp.add_argument("target", help="task_id 或 all")
    sp.set_defaults(func=lambda a: cmd_dry_run(a.target, Path(a.home).resolve()))

    return p


def main(argv: List[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
