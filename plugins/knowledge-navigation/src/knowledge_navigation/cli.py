"""CLI 入口 —— 知识导航插件测试工具。"""

import logging

import typer

from knowledge_navigation.adapters.hindsight import HindsightClient
from knowledge_navigation.config import CONFIG, setup_logging
from knowledge_navigation.core.filtering import (
    calculate_score_stats,
    extract_rerank_scores,
    filter_by_score,
    format_context_lines,
)

app = typer.Typer(
    name="knowledge-navigation",
    help="知识导航插件测试工具",
    add_completion=False,
)


def _test_recall(query: str) -> None:
    """测试 recall 功能。"""
    typer.echo(f"正在测试 recall: '{query}'")

    client = HindsightClient()
    try:
        result = client.recall(query)
        if result:
            typer.echo("Recall 成功")
            typer.echo(f"返回结果数: {len(result.get('results', []))}")

            trace = result.get("trace", {})
            reranked = trace.get("reranked", [])
            typer.echo(f"reranked 结果数: {len(reranked)}")

            results = result.get("results", [])[:3]
            for i, r in enumerate(results, 1):
                text = str(r.get("text", "")).strip()[:100]
                typer.echo(f"{i}. [{r.get('id', 'unknown')}] {text}...")
        else:
            typer.echo("Recall 失败或返回空结果", err=True)
    except Exception as e:
        typer.echo(f"测试异常: {e}", err=True)
    finally:
        client.close()


@app.command()
def run(
    query: str | None = typer.Argument(None, help="要测试的查询文本"),
    list_hooks: bool = typer.Option(False, "--list-hooks", help="列出支持的钩子"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
) -> None:
    """执行 recall 测试或列出支持的钩子。"""
    if verbose:
        logging.getLogger("knowledge_navigation").setLevel(logging.DEBUG)
        setup_logging()

    if list_hooks:
        typer.echo("支持的钩子:")
        typer.echo("- pre_llm_call: 在 LLM 调用前自动 recall Hindsight 记忆")
        return

    if query:
        _test_recall(query)
    else:
        typer.echo("用法: knowledge-navigation <query>")
        typer.echo("   或: knowledge-navigation --list-hooks")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
