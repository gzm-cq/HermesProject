"""AI报告生成系统 — 命令行接口（typer）

遵循Hermes工程标准规范，统一使用typer框架。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer

from ai_report.config import get_config
from ai_report.core.exceptions import ReportAgentError, handle_error

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="ai-report",
    help="AI报告生成系统 - 基于Hermes工具集",
    add_completion=False,
)


def _setup_logging(level: str = "INFO") -> None:
    """配置日志输出"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def generate(
    topic: str = typer.Argument(..., help="报告主题"),
    report_type: str | None = typer.Option(None, "--type", "-t", help="报告类型"),
    language: str | None = typer.Option(None, "--language", "-l", help="语言"),
    output: str | None = typer.Option(None, "--output", "-o", help="输出目录"),
    show: bool = typer.Option(False, "--show", help="显示报告内容"),
    skip_eval: bool = typer.Option(False, "--skip-eval", help="跳过评估"),
    skip_quality: bool = typer.Option(False, "--skip-quality", help="跳过质量检查"),
    log_level: str = typer.Option("INFO", "--log-level", help="日志级别"),
) -> None:
    """生成报告"""
    _setup_logging(log_level)
    logger.info("开始生成报告: topic='%s'", topic)

    try:
        from ai_report.core.orchestrator import ReportWorkflowOrchestrator

        output_dir = Path(output) if output else None

        orchestrator = ReportWorkflowOrchestrator()
        result = orchestrator.run(
            topic=topic,
            report_type=report_type,
            language=language,
            skip_evaluation=skip_eval,
            skip_quality=skip_quality,
            output_dir=output_dir,
        )

        if not result["success"]:
            logger.error("报告生成失败")
            for err in result.get("errors", []):
                logger.error("  - %s", err)
            raise typer.Exit(1)

        report = result["report"]
        plan = result["plan"]
        evaluation = result.get("evaluation")

        # 输出摘要
        print(f"\n{'=' * 50}")
        print(f"  ✅ 报告生成成功!")
        print(f"  📄 主题: {plan.title}")
        print(f"  📊 类型: {plan.report_type}")
        print(f"  📝 字数: {report.total_words}")
        print(f"  📐 章节: {len(report.sections)}")
        print(f"  ⏱ 耗时: {result['elapsed_display']}")

        if evaluation:
            print(f"  ⭐ 评分: {evaluation.overall_score:.4f} ({evaluation.quality_grade})")
            if evaluation.optimization_tasks:
                print(f"  💡 优化建议: {len(evaluation.optimization_tasks)}项")

        if result.get("output_path"):
            print(f"  💾 保存到: {result['output_path']}")

        print(f"{'=' * 50}\n")

        if show:
            print(report.full_content)

    except Exception as e:
        logger.exception("报告生成异常")
        print(f"❌ 错误: {e}")
        raise typer.Exit(1)


@app.command()
def plan(
    topic: str = typer.Argument(..., help="报告主题"),
    report_type: str | None = typer.Option(None, "--type", "-t", help="报告类型"),
    json_output: bool = typer.Option(False, "--json", help="JSON格式输出"),
    log_level: str = typer.Option("INFO", "--log-level", help="日志级别"),
) -> None:
    """预览报告计划"""
    _setup_logging(log_level)

    try:
        from ai_report.core.planner import HermesReportPlanner

        planner = HermesReportPlanner()
        plan_obj = planner.create_plan(
            topic=topic,
            report_type=report_type,
        )

        print(f"\n{plan_obj.preview()}\n")

        if json_output:
            print(json.dumps(plan_obj.to_dict(), indent=2, ensure_ascii=False))

    except Exception as e:
        logger.exception("规划失败")
        print(f"❌ 错误: {e}")
        raise typer.Exit(1)


@app.command()
def types(
    log_level: str = typer.Option("INFO", "--log-level", help="日志级别"),
) -> None:
    """列出报告类型"""
    _setup_logging(log_level)
    from ai_report.core.planner import HermesReportPlanner

    planner = HermesReportPlanner()
    template_types = planner.list_templates()

    print(f"\n可用报告类型 ({len(template_types)}种):\n")
    for rt in template_types:
        print(f"  - {rt}")
    print()


@app.command()
def config_cmd(
    log_level: str = typer.Option("INFO", "--log-level", help="日志级别"),
) -> None:
    """查看当前配置"""
    _setup_logging(log_level)
    config = get_config()
    print(f"\n当前配置:\n")
    print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
    print()


def main() -> None:
    """CLI入口"""
    app()


if __name__ == "__main__":
    main()
