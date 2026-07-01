"""P0 Benchmark CLI — typer 实现。"""

import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from p0_benchmark.config import AppConfig, load_config, setup_logging

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="p0-benchmark",
    help="P0 优化 Benchmark 测试框架 — 验证 P0-1/2/3 的性能提升和准确率",
    add_completion=False,
)


@app.command()
def all(
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    output_path: Optional[str] = typer.Option(None, "--output", help="报告输出路径"),
    log_level: Optional[str] = typer.Option(None, "--log-level", help="日志级别"),
) -> None:
    """运行所有 P0 Benchmark 测试。"""
    yaml_data = load_config(config_path)
    cfg = AppConfig.from_yaml(yaml_data)

    if log_level:
        cfg.log_level = log_level.upper()
    if output_path:
        cfg.output_path = output_path

    setup_logging(cfg.log_level)

    typer.echo("🚀 开始 P0 Benchmark 测试...")
    typer.echo("")

    results = {}

    # P0-1: Skill Matcher Benchmark
    typer.echo("📊 P0-1: Skill Matcher Benchmark")
    typer.echo("-" * 40)
    p0_1_result = run_p0_1(cfg)
    results["p0_1_skill_matcher"] = p0_1_result
    typer.echo("")

    # P0-2: pgvector 去重 Benchmark
    typer.echo("📊 P0-2: pgvector 去重 Benchmark")
    typer.echo("-" * 40)
    p0_2_result = run_p0_2(cfg)
    results["p0_2_dedup"] = p0_2_result
    typer.echo("")

    # P0-3: LLM 合并调用 Benchmark
    typer.echo("📊 P0-3: LLM 合并调用 Benchmark")
    typer.echo("-" * 40)
    p0_3_result = run_p0_3(cfg)
    results["p0_3_llm_merged"] = p0_3_result
    typer.echo("")

    # 保存报告
    ts = time.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(cfg.output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = output_dir / f"p0-benchmark-report-{ts}.json"

    report = {
        "timestamp": ts,
        "config": {
            "skill_benchmark_queries": cfg.skill_benchmark_queries,
            "dedup_benchmark_sizes": cfg.dedup_benchmark_sizes,
            "llm_benchmark_article_count": cfg.llm_benchmark_article_count,
        },
        "results": results,
    }

    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    typer.echo("=" * 50)
    typer.echo("📄 报告已保存: " + str(report_file))
    typer.echo("")

    # 打印汇总
    print_summary(results)


@app.command()
def p0_1(
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    output_path: Optional[str] = typer.Option(None, "--output", help="报告输出路径"),
    queries: int = typer.Option(100, "--queries", help="测试 query 数量"),
) -> None:
    """P0-1: Skill Matcher 关键词预筛选 Benchmark。"""
    yaml_data = load_config(config_path)
    cfg = AppConfig.from_yaml(yaml_data)
    cfg.skill_benchmark_queries = queries

    if output_path:
        cfg.output_path = output_path

    setup_logging(cfg.log_level)
    result = run_p0_1(cfg)
    print_p0_1_result(result)


@app.command()
def p0_2(
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    output_path: Optional[str] = typer.Option(None, "--output", help="报告输出路径"),
    sizes: str = typer.Option("1000,5000,10000", "--sizes", help="知识库规模（逗号分隔）"),
) -> None:
    """P0-2: pgvector 去重 Benchmark。"""
    yaml_data = load_config(config_path)
    cfg = AppConfig.from_yaml(yaml_data)
    cfg.dedup_benchmark_sizes = [int(s.strip()) for s in sizes.split(",")]

    if output_path:
        cfg.output_path = output_path

    setup_logging(cfg.log_level)
    result = run_p0_2(cfg)
    print_p0_2_result(result)


@app.command()
def p0_3(
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    output_path: Optional[str] = typer.Option(None, "--output", help="报告输出路径"),
    article_count: int = typer.Option(50, "--articles", help="测试文章数量"),
) -> None:
    """P0-3: LLM 合并调用 Benchmark。"""
    yaml_data = load_config(config_path)
    cfg = AppConfig.from_yaml(yaml_data)
    cfg.llm_benchmark_article_count = article_count

    if output_path:
        cfg.output_path = output_path

    setup_logging(cfg.log_level)
    result = run_p0_3(cfg)
    print_p0_3_result(result)


# ── Benchmark 实现 ──


def run_p0_1(cfg: AppConfig) -> dict:
    """P0-1: Skill Matcher 关键词预筛选 Benchmark。"""
    from p0_benchmark.core.skill_benchmark import run_skill_matcher_benchmark
    return run_skill_matcher_benchmark(
        num_queries=cfg.skill_benchmark_queries,
        prescreen_top_k=cfg.skill_benchmark_prescreen_top_k,
        accuracy_threshold=cfg.skill_benchmark_accuracy_threshold,
        random_seed=cfg.seed,
    )


def run_p0_2(cfg: AppConfig) -> dict:
    """P0-2: pgvector 去重 Benchmark。"""
    from p0_benchmark.core.dedup_benchmark import run_dedup_benchmark
    return run_dedup_benchmark(
        sizes=cfg.dedup_benchmark_sizes,
        threshold=cfg.dedup_benchmark_threshold,
        repeat=cfg.dedup_benchmark_repeat,
        db_url=getattr(cfg, "dedup_benchmark_db_url", ""),
        random_seed=cfg.seed,
    )


def run_p0_3(cfg: AppConfig) -> dict:
    """P0-3: LLM 合并调用 Benchmark。"""
    from p0_benchmark.core.llm_benchmark import run_llm_merged_benchmark
    return run_llm_merged_benchmark(
        article_count=cfg.llm_benchmark_article_count,
        model=cfg.llm_benchmark_model,
        api_url=cfg.llm_benchmark_api_url,
        random_seed=cfg.seed,
    )


# ── 输出格式 ──


def print_p0_1_result(result: dict) -> None:
    """打印 P0-1 结果。"""
    typer.echo("")
    typer.echo("📊 P0-1 Skill Matcher Benchmark 结果")
    typer.echo("-" * 40)
    typer.echo(f"  测试 query 数: {result.get('total_queries', 0)}")
    typer.echo(f"  平均延迟（开启）: {result.get('avg_latency_with', 0):.3f}s")
    typer.echo(f"  平均延迟（关闭）: {result.get('avg_latency_without', 0):.3f}s")
    typer.echo(f"  延迟降低: {result.get('latency_reduction_pct', 0):.1f}%")
    typer.echo(f"  Token 节省: {result.get('token_savings_pct', 0):.1f}%")
    typer.echo(f"  准确率: {result.get('accuracy', 0):.1%}")
    typer.echo(f"  验收通过: {'✅' if result.get('passed', False) else '❌'}")
    typer.echo("")


def print_p0_2_result(result: dict) -> None:
    """打印 P0-2 结果。"""
    typer.echo("")
    typer.echo("📊 P0-2 pgvector 去重 Benchmark 结果")
    typer.echo("-" * 40)
    for size_result in result.get("results_by_size", []):
        typer.echo(f"  知识库规模: {size_result['size']}")
        typer.echo(f"    内存扫描耗时: {size_result['memory_time_ms']:.1f}ms")
        typer.echo(f"    pgvector 耗时: {size_result['pgvector_time_ms']:.1f}ms")
        typer.echo(f"    加速比: {size_result['speedup']:.1f}x")
        typer.echo(f"    一致性: {size_result['consistency']:.1%}")
        typer.echo(f"    验收通过: {'✅' if size_result['passed'] else '❌'}")
        typer.echo("")
    typer.echo(f"  总体验收通过: {'✅' if result.get('all_passed', False) else '❌'}")


def print_p0_3_result(result: dict) -> None:
    """打印 P0-3 结果。"""
    typer.echo("")
    typer.echo("📊 P0-3 LLM 合并调用 Benchmark 结果")
    typer.echo("-" * 40)
    typer.echo(f"  文章数: {result.get('article_count', 0)}")
    typer.echo(f"  LLM 调用数（开启）: {result.get('llm_calls_with', 0)}")
    typer.echo(f"  LLM 调用数（关闭）: {result.get('llm_calls_without', 0)}")
    typer.echo(f"  调用减少: {result.get('reduction_pct', 0):.1f}%")
    typer.echo(f"  知识点数量差异: {result.get('kp_diff_pct', 0):.1f}%")
    typer.echo(f"  知识点类型分布差异: {result.get('type_dist_diff', 0):.1f}%")
    typer.echo(f"  验收通过: {'✅' if result.get('passed', False) else '❌'}")
    typer.echo("")


def print_summary(results: dict) -> None:
    """打印汇总结果。"""
    typer.echo("📊 P0 Benchmark 汇总")
    typer.echo("=" * 50)

    p0_1 = results.get("p0_1_skill_matcher", {})
    p0_2 = results.get("p0_2_dedup", {})
    p0_3 = results.get("p0_3_llm_merged", {})

    typer.echo("")
    typer.echo(f"P0-1 Skill Matcher: {'✅ 通过' if p0_1.get('passed') else '❌ 未通过'} ({p0_1.get('latency_reduction_pct', 0):.1f}% 延迟降低)")
    typer.echo(f"P0-2 pgvector 去重: {'✅ 通过' if p0_2.get('all_passed') else '❌ 未通过'}")
    typer.echo(f"P0-3 LLM 合并: {'✅ 通过' if p0_3.get('passed') else '❌ 未通过'} ({p0_3.get('reduction_pct', 0):.1f}% 调用减少)")

    all_passed = p0_1.get("passed") and p0_2.get("all_passed") and p0_3.get("passed")
    typer.echo("")
    typer.echo(f"总体结果: {'✅ 所有验收标准通过' if all_passed else '⚠️ 部分验收标准未通过'}")


def main() -> None:
    """CLI 主入口。"""
    try:
        app()
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断", file=sys.stderr)
        raise typer.Exit(1)


if __name__ == "__main__":
    main()
