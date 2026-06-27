"""CLI 入口 — typer 命令行界面

用法：
    kanban-reflect --task-id <id> --trace <path> [--goal <goal>]
    kanban-reflect --task-id <id> --trace <path> --dry-run
"""

import json
import logging
from typing import Optional

import typer

from kanban_reflection.config import KanbanReflectionConfig
from kanban_reflection.core.reflector import (
    reflect_on_failure,
    read_trace_lines,
)

app = typer.Typer(
    name="kanban-reflect",
    help="Kanban 反思回路 — 分析失败任务并输出结构化反思结果",
    add_completion=False,
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s | %(message)s",
    )


@app.command()
def analyze(
    task_id: str = typer.Option(..., "--task-id", "-t", help="Kanban 任务 ID"),
    trace_path: str = typer.Option(
        ..., "--trace", "-f", help="trace.log 文件路径"
    ),
    task_goal: str = typer.Option(
        "", "--goal", "-g", help="任务目标描述（可选）"
    ),
    max_lines: int = typer.Option(
        5, "--max-lines", "-n", help="读取最近 N 轮消息"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="仅预览 trace 内容，不调用 LLM"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="详细日志输出"
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="输出 JSON 文件路径（可选）"
    ),
) -> None:
    """分析 Kanban 任务失败原因，输出结构化反思结果"""
    _setup_logging(verbose)
    config = KanbanReflectionConfig.from_env()

    # 读取 trace
    trace_lines = read_trace_lines(trace_path, task_id, max_lines=max_lines)

    if not trace_lines:
        typer.echo(f"⚠️ 在 {trace_path} 中未找到任务 {task_id} 的 trace 记录")
        raise typer.Exit(code=1)

    typer.echo(f"📋 任务 ID: {task_id}")
    typer.echo(f"📊 读取到 {len(trace_lines)} 条 trace 记录")

    if dry_run:
        typer.echo("\n🔍 Dry-Run 模式 — 预览 trace 内容：")
        for i, line in enumerate(trace_lines, 1):
            typer.echo(f"  [{i}] {json.dumps(line, ensure_ascii=False)[:200]}...")
        raise typer.Exit(code=0)

    # 执行反思
    typer.echo("🤖 正在分析失败原因...")
    result = reflect_on_failure(
        task_id=task_id,
        task_goal=task_goal,
        trace_lines=trace_lines,
        config=config,
    )

    # 输出结果
    typer.echo("\n✅ 反思完成：")
    typer.echo(f"  🔴 失败原因: {result.failure_reason}")
    typer.echo(f"  🏷️  错误类型: {result.failure_type}")
    typer.echo(f"  💡 优化建议: {result.suggestion}")
    typer.echo(f"  📊 置信度:   {result.confidence:.2f}")

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        typer.echo(f"\n💾 结果已保存至: {output}")


@app.command()
def list_types() -> None:
    """列出支持的失败类型"""
    config = KanbanReflectionConfig.from_env()
    typer.echo("支持的失败类型（SEAL 6 类）：")
    for ftype in config.failure_types:
        typer.echo(f"  - {ftype}")
