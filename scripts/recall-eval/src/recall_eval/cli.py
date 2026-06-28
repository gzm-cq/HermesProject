"""CLI 入口 — typer 实现，Recall 质量评估。"""

import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from recall_eval.adapters.llm_client import LLMClient
from recall_eval.config import AppConfig, load_config, setup_logging
from recall_eval.core.dataset import EvalDataset
from recall_eval.core.runner import EvalReport, EvalRunner, print_report

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="recall-eval",
    help="Recall 质量评估框架 — RAGAS faithfulness 评估 Hindsight/知识树召回质量",
    add_completion=False,
)


@app.command()
def run(
    config_path: str = typer.Option(
        "config/default.yaml", "--config", help="配置文件路径（YAML）"
    ),
    dataset_path: Optional[str] = typer.Option(
        None, "--dataset", help="数据集路径（覆盖配置文件中的 dataset_path）"
    ),
    output_path: Optional[str] = typer.Option(
        None, "--output", help="报告输出路径（覆盖配置文件中的 output_path）"
    ),
    log_level: Optional[str] = typer.Option(
        None, "--log-level", help="日志级别（DEBUG/INFO/WARNING）"
    ),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出结果"),
    use_llm: bool = typer.Option(
        False, "--llm", help="使用 LLM 进行评估（默认使用启发式规则）"
    ),
    category: Optional[str] = typer.Option(
        None, "--category", help="只评估指定类别的查询"
    ),
) -> None:
    """运行 Recall 质量评估。"""
    yaml_data = load_config(config_path)
    cfg = AppConfig.from_env(yaml_data)

    if log_level:
        cfg.log_level = log_level.upper()
    if json_output:
        cfg.output_mode = "json"
    if dataset_path:
        cfg.dataset_path = dataset_path
    if output_path:
        cfg.output_path = output_path

    setup_logging(cfg.log_level)

    dataset = EvalDataset.load(cfg.dataset_path)
    if not dataset.queries:
        typer.echo(f"错误: 数据集为空或加载失败: {cfg.dataset_path}", err=True)
        raise typer.Exit(1)

    if category:
        filtered = dataset.filter_by_category(category)
        if not filtered:
            available = ", ".join(dataset.categories())
            typer.echo(
                f"错误: 未找到类别 '{category}'，可用类别: {available}", err=True
            )
            raise typer.Exit(1)
        dataset.queries = filtered
        logger.info("筛选类别 '%s': %d 条查询", category, len(filtered))

    llm_client = LLMClient(cfg) if use_llm else None

    runner = EvalRunner(config=cfg, llm_client=llm_client)
    report = runner.run(dataset)

    ts = time.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(cfg.output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = output_dir / f"eval-report-{ts}.json"
    report.save(str(report_file))

    if cfg.output_mode == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_report(report)
        typer.echo(f"📄 详细报告已保存: {report_file}")


@app.command()
def report(
    report_path: str = typer.Argument(..., help="报告文件路径（JSON）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看已生成的评估报告。"""
    path = Path(report_path)
    if not path.exists():
        typer.echo(f"错误: 报告文件不存在: {report_path}", err=True)
        raise typer.Exit(1)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if json_output:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    report = EvalReport(
        dataset_name=data.get("dataset_name", ""),
        total_queries=data.get("total_queries", 0),
        successful_queries=data.get("successful_queries", 0),
        failed_queries=data.get("failed_queries", 0),
        avg_faithfulness=data.get("avg_faithfulness", 0.0),
        avg_relevance=data.get("avg_relevance", 0.0),
        avg_coverage=data.get("avg_coverage", 0.0),
        overall_score=data.get("overall_score", 0.0),
        category_scores=data.get("category_scores", {}),
        timestamp=data.get("timestamp", ""),
        duration_seconds=data.get("duration_seconds", 0.0),
        tokens=data.get("tokens", {}),
    )
    print_report(report)


@app.command(name="list")
def list_datasets(
    config_path: str = typer.Option(
        "config/default.yaml", "--config", help="配置文件路径（YAML）"
    ),
    dataset_path: Optional[str] = typer.Option(
        None, "--dataset", help="数据集路径（覆盖配置）"
    ),
) -> None:
    """列出数据集中的查询。"""
    yaml_data = load_config(config_path)
    cfg = AppConfig.from_env(yaml_data)
    if dataset_path:
        cfg.dataset_path = dataset_path

    dataset = EvalDataset.load(cfg.dataset_path)
    if not dataset.queries:
        typer.echo(f"数据集为空或加载失败: {cfg.dataset_path}")
        raise typer.Exit(1)

    print(f"\n数据集: {dataset.name}")
    print(f"总查询数: {len(dataset)}")

    categories = dataset.categories()
    if categories:
        print(f"\n类别分布:")
        for cat in categories:
            count = len(dataset.filter_by_category(cat))
            print(f"  {cat}: {count} 条")

    print(f"\n查询列表:")
    print(f"{'ID':<20} {'类别':<15} 查询")
    print(f"{'─' * 20} {'─' * 15} {'─' * 40}")
    for q in dataset.queries:
        cat = q.category or "uncategorized"
        query_display = q.query[:50] + "..." if len(q.query) > 50 else q.query
        print(f"{q.query_id:<20} {cat:<15} {query_display}")
    print()


def main() -> None:
    """CLI 主入口。"""
    try:
        app()
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断", file=sys.stderr)
        raise typer.Exit(1)


if __name__ == "__main__":
    main()
