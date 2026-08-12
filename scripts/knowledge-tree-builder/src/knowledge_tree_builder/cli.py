"""CLI 入口 — typer 实现

知识提取管线的命令行入口，所有命令实现已拆分到 commands/ 子包。

拆分结构：
  - commands/_utils.py     — 共享辅助函数
  - commands/basic.py      — 基础命令（init_db, find, backfill_k_vectors, redistribute, review）
  - commands/crud.py       — CRUD 命令（add, tree, ingest, edit, remove, merge, move）
  - commands/complex.py    — 复杂命令（consolidate）
  - commands/run.py        — run 命令（知识提取管线）
  - commands/deprecated.py — 废弃命令（run_old, cluster, validate, extract, write, name, report）
"""

from __future__ import annotations

import logging
import os
import sys
import typer

from knowledge_tree_builder.commands._utils import JSONFormatter, setup_logging
from knowledge_tree_builder.commands.basic import (
    cmd_init_db,
    cmd_find,
    cmd_backfill_k_vectors,
    cmd_redistribute,
    cmd_review,
)
from knowledge_tree_builder.commands.crud import (
    cmd_add,
    cmd_tree,
    cmd_ingest,
    cmd_edit,
    cmd_remove,
    cmd_merge,
    cmd_move,
)
from knowledge_tree_builder.commands.complex import cmd_consolidate
from knowledge_tree_builder.commands.lineage import cmd_lineage_show, cmd_lineage_export
from knowledge_tree_builder.commands.check_freshness import cmd_check_freshness
from knowledge_tree_builder.commands.run import _run_pipeline

# ── 统一反馈账本（F-1）：跨飞轮事件追加 ──────────────
def _add_hermes_common_to_path() -> bool:
    """将统一共享库 hermes_common 的父目录注入 sys.path。

    查找顺序：① 开发态仓库 libs/hermes_common；② 生产部署 /root/.hermes/lib。
    """
    # 1) 开发态：从 __file__ 向上定位仓库根（含 libs/ 的目录）
    d = os.path.dirname(os.path.abspath(__file__))
    root = None
    for _ in range(12):
        if os.path.isdir(os.path.join(d, "libs")):
            root = d
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    if root is not None:
        pkg_parent = os.path.join(root, "libs", "hermes_common")
        if os.path.isfile(os.path.join(pkg_parent, "hermes_common", "__init__.py")):
            if pkg_parent not in sys.path:
                sys.path.insert(0, pkg_parent)
            return True
    # 2) 生产部署
    prod = "/root/.hermes/lib"
    if os.path.isfile(os.path.join(prod, "hermes_common", "__init__.py")) and prod not in sys.path:
        sys.path.insert(0, prod)
        return True
    return False


_add_hermes_common_to_path()
try:
    from hermes_common.ledger import append_ledger_event
except Exception:  # noqa: BLE001
    def append_ledger_event(*_a, **_k):  # type: ignore
        return False
from knowledge_tree_builder.core.cache_manager import CacheManager
from knowledge_tree_builder.commands.deprecated import (
    cmd_run_old,
    cmd_cluster,
    cmd_validate,
    cmd_extract,
    cmd_write,
    cmd_name,
    cmd_report,
)

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="knowledge-tree-builder",
    help="知识分域建树管线 — 从文章自动构建二叉树知识树",
    add_completion=False,
)


@app.command()
def init_db(
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """初始化 PG 表结构（首次部署运行）"""
    cmd_init_db(config_path)


@app.command()
def find(
    query: str = typer.Argument(..., help="搜索关键词"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    limit: int = typer.Option(10, "--limit", "-n", help="最大返回数"),
) -> None:
    """搜索知识树（不走 Hindsight，直接查树）。"""
    cmd_find(query, config_path, limit)


@app.command()
def backfill_k_vectors(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不写入 DB"),
    batch_size: int = typer.Option(20, "--batch-size", help="embedding 批量大小"),
    db_url: str = typer.Option("", help="PG 连接串（默认从 KT_DB_URL 读取）"),
    embed_base_url: str = typer.Option(
        "https://api.siliconflow.cn/v1", "--embed-base-url",
        envvar="KT_EMBED_BASE_URL",
    ),
    embed_model: str = typer.Option(
        "BAAI/bge-m3", "--embed-model",
        envvar="KT_EMBED_MODEL",
    ),
    embed_api_key: str = typer.Option(
        "", "--embed-api-key",
        envvar="HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY",
    ),
) -> None:
    """批量回填 k_vector（遍历 k_vector IS NULL 的叶子节点，计算 embedding 后写入）。"""
    cmd_backfill_k_vectors(dry_run, batch_size, db_url, embed_base_url, embed_model, embed_api_key)


@app.command()
def redistribute(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览迁移计划，不动 DB"),
    db_url: str = typer.Option("", help="PG 连接串（默认从 KT_DB_URL 读取）"),
    llm_api_url: str = typer.Option(
        "http://127.0.0.1:4142/v1/chat/completions", "--llm-api-url",
        envvar="LITELLM_API_URL",
    ),
    llm_api_key: str = typer.Option("", "--llm-api-key", envvar="LITELLM_MASTER_KEY"),
    llm_model: str = typer.Option("s-deepseek-v4-flash", "--llm-model", envvar="KT_LLM_MODEL"),
    embed_base_url: str = typer.Option(
        "https://api.siliconflow.cn/v1", "--embed-base-url",
        envvar="KT_EMBED_BASE_URL",
    ),
    embed_model: str = typer.Option(
        "BAAI/bge-m3", "--embed-model",
        envvar="KT_EMBED_MODEL",
    ),
    embed_api_key: str = typer.Option(
        "", "--embed-api-key",
        envvar="HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY",
    ),
) -> None:
    """重新分类 general/root 下的知识点到正确领域（3 级漏斗）。"""
    cmd_redistribute(dry_run, db_url, llm_api_url, llm_api_key, llm_model, embed_base_url, embed_model, embed_api_key)


@app.command()
def review(
    action: str = typer.Argument(..., help="操作: list | accept | reject"),
    review_id: int = typer.Option(0, "--id", help="审查项 ID（accept/reject 时需要）"),
    review_type: str = typer.Option("", "--type", help="筛选类型（list 时可选）"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """审查队列操作：列出/接受/拒绝审查项。"""
    cmd_review(action, review_id, review_type, config_path)


@app.command()
def add(
    text: str = typer.Argument(..., help="新知识点文本"),
    title: str = typer.Option("", "--title", "-t", help="来源标题（可选）"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    dry_run: bool = typer.Option(True, "--dry-run", help="仅预览，不写入 PG"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
) -> None:
    """添加一条新知识点。

    执行 LLM 提取 → 准入过滤 → 增量去重 → 矛盾检测。
    有 DB 连接时尝试定位到已知科目。
    """
    cmd_add(text, title, config_path, dry_run, verbose)


@app.command()
def ingest(
    file_path: str = typer.Argument(..., help="输入文件路径（.md / .txt）"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    dry_run: bool = typer.Option(True, "--dry-run", help="仅预览，不写入 PG"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
) -> None:
    """从文件批量提取知识点 → 准入过滤 → 建树报告。

    相当于单文件版的 extract + cluster + report。
    """
    cmd_ingest(file_path, config_path, dry_run, verbose)


@app.command()
def tree(
    max_depth: int = typer.Option(3, "--depth", "-d", help="最大显示深度"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """查看知识树结构（可视化科目层级）。

    以树形结构展示知识库中的科目层级，支持控制显示深度和节点统计信息。

    Args:
        max_depth: 最大显示深度，默认3层（可设置1-10）
        config_path: 配置文件路径，默认 'config/default.yaml'

    Returns:
        None

    Example:
        # 默认3层深度
        knowledge-tree-builder tree

        # 显示5层深度
        knowledge-tree-builder tree --depth 5

        # 仅显示根节点
        knowledge-tree-builder tree --depth 1

    Note:
        - 输出格式：节点ID | 节点名称 | 子节点数 | 知识点数
        - 深度过大时输出较长，建议控制depth≤5
        - 树结构基于 parent_id 递归生成
    """
    cmd_tree(max_depth, config_path)


@app.command()
def move(
    node_id: int = typer.Argument(..., help="知识点 ID"),
    to: int = typer.Option(..., "--to", help="目标父科目 ID"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    dry_run: bool = typer.Option(True, "--dry-run", help="仅预览"),
) -> None:
    """移动知识点到另一个科目下（自动更新 K 向量）。"""
    cmd_move(node_id, to, config_path, dry_run)


@app.command()
def edit(
    node_id: int = typer.Argument(..., help="知识点 ID"),
    name: str = typer.Option("", "--name", "-n", help="新名称"),
    text: str = typer.Option("", "--text", "-t", help="新知识点原文"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """修正知识点文本或名称。"""
    cmd_edit(node_id, name, text, config_path)


@app.command()
def remove(
    node_id: int = typer.Argument(..., help="知识点 ID"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    force: bool = typer.Option(False, "--force", "-f", help="强制删除（含子节点）"),
) -> None:
    """删除知识点（错误或过时）。"""
    cmd_remove(node_id, config_path, force)


@app.command()
def merge(
    keep_id: int = typer.Argument(..., help="保留的知识点 ID"),
    remove_id: int = typer.Argument(..., help="被合并的知识点 ID"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    dry_run: bool = typer.Option(True, "--dry-run", help="仅预览"),
) -> None:
    """合并两个重复的知识点（合并 source_ids 后删除一个）。"""
    cmd_merge(keep_id, remove_id, config_path, dry_run)


@app.command()
def run(
    input_dir: str = typer.Option("references", "--input-dir", help="输入文章目录"),
    phase: str = typer.Option("all", "--phase", help="运行阶段: scan|analyze|split|admit|place|all"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览不写 PG，传 --dry-run 开启预览模式"),
    merged: bool = typer.Option(False, "--merged", help="阶段1+2 合并为单次 LLM 调用（更快，跳过 claims_count 校验）"),
    concurrent: int = typer.Option(1, "--concurrent", "-j", help="并行提取线程数（默认 1=串行）"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
) -> None:
    """新管线：scan → analyze → split → admit → place。

    执行完整的知识提取管线，从精读笔记/技术文章中自动提取知识点并建树入库。

    Args:
        input_dir: 输入文章目录路径，默认 'references'
        phase: 运行阶段选择，支持 'scan'|'analyze'|'split'|'admit'|'place'|'all'
        config_path: 配置文件路径，默认 'config/default.yaml'
        dry_run: 预览模式，仅显示提取结果不写入数据库
        merged: 合并阶段1+2为单次LLM调用，更快但跳过claims_count校验
        concurrent: 并行提取线程数，默认1（串行），建议2-4
        verbose: 详细输出模式，显示更多中间信息

    Returns:
        None

    Example:
        # 全量串行提取
        knowledge-tree-builder run

        # 并行提取（4线程）
        knowledge-tree-builder run --concurrent 4

        # 预览模式
        knowledge-tree-builder run --dry-run

        # 仅运行分析阶段
        knowledge-tree-builder run --phase analyze

    Note:
        - 管线阶段顺序：scan→analyze→split→admit→place
        - dry_run模式下不写入数据库，可安全测试
        - concurrent>1时需确保数据库连接池支持并发
        - merged模式适合批量提取，跳过校验以提升速度
    """
    _run_pipeline(
        input_dir=input_dir,
        phase=phase,
        config_path=config_path,
        dry_run=dry_run,
        merged=merged,
        concurrent=concurrent,
        verbose=verbose,
    )
    # F-1 统一反馈账本：记录知识树构建触发（跨循环关联；精确条目数由管线内部统计，后续可回填）
    append_ledger_event("kt_build", {
        "phase": phase,
        "input_dir": input_dir,
        "dry_run": dry_run,
    })


@app.command()
def consolidate(
    action: str = typer.Argument("run", help="操作: run | process-timeouts"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，传 --dry-run 开启预览模式"),
    merge_domains: bool = typer.Option(True, "--merge-domains", help="启用碎片 domain 合并（默认开启，--no-merge-domains 关闭）"),
    min_domain_nodes: int = typer.Option(5, "--min-domain-nodes", help="domain 合并阈值"),
    domain_merge_threshold: float = typer.Option(0.6, "--domain-merge-threshold", help="余弦相似度阈值"),
    build_edges: bool = typer.Option(True, "--build-edges", help="构建 KP 级关联边（默认开启）"),
) -> None:
    """纠错回路：更新 confidence + 处理超时审查项 + 碎片 domain 合并。

    - run: 从使用日志更新所有知识的 confidence
    - process-timeouts: 处理 review_queue 中超时的审查项

    domain 合并(--merge-domains):
      子节点 < min-domain-nodes 的碎片 domain 合并到最近的大 domain。
      整合在 consolidate 中，无需独立命令。
    """
    cmd_consolidate(action, config_path, dry_run, merge_domains, min_domain_nodes, domain_merge_threshold, build_edges)


@app.command()
def check_freshness(
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅报告，不执行更新"),
    db_url: str = typer.Option("", help="PG 连接串（默认从 KT_DB_URL 读取）"),
    embed_base_url: str = typer.Option(
        "https://api.siliconflow.cn/v1", "--embed-base-url",
        envvar="KT_EMBED_BASE_URL",
    ),
    embed_model: str = typer.Option(
        "BAAI/bge-m3", "--embed-model",
        envvar="KT_EMBED_MODEL",
    ),
    embed_api_key: str = typer.Option(
        "", "--embed-api-key",
        envvar="HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY",
    ),
) -> None:
    """检查知识树中 text 发生变化需要重新 embedding 的节点。

    查询所有 knowledge_point 节点，比对 text hash，找出 text 已变化需要重新 embedding 的节点。
    默认仅报告（dry-run），传入 --dry-run=false 执行实际更新。
    """
    cmd_check_freshness(config_path, dry_run, db_url, embed_base_url, embed_model, embed_api_key)


cache_app = typer.Typer(
    name="cache",
    help="缓存管理 — 查看和清理知识提取管线缓存",
    add_completion=False,
)
app.add_typer(cache_app, name="cache")


@cache_app.command("ls")
def cache_ls(
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """列出所有缓存文件。"""
    from knowledge_tree_builder.config import load_config
    config_dict = load_config(config_path)
    cache_manager = CacheManager(
        cache_dir=config_dict.get("cache_dir", ".kb_cache/"),
        enable_unified_cache=config_dict.get("enable_unified_cache", True),
    )
    if not cache_manager.enable_unified_cache:
        print("统一缓存管理未启用（KT_ENABLE_UNIFIED_CACHE=false）")
        return
    caches = cache_manager.list_caches()
    if not caches:
        print("暂无缓存文件")
        return
    from datetime import datetime
    print(f"{'文件名':<30} {'大小':>10} {'修改时间'}")
    print("-" * 60)
    for c in caches:
        size_str = _format_size(c.size)
        mtime_str = datetime.fromtimestamp(c.mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{c.name:<30} {size_str:>10} {mtime_str}")


@cache_app.command("clear")
def cache_clear(
    name: str = typer.Option("", "--name", help="缓存文件名（省略则清除全部）"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """清除缓存文件。"""
    from knowledge_tree_builder.config import load_config
    config_dict = load_config(config_path)
    cache_manager = CacheManager(
        cache_dir=config_dict.get("cache_dir", ".kb_cache/"),
        enable_unified_cache=config_dict.get("enable_unified_cache", True),
    )
    if not cache_manager.enable_unified_cache:
        print("统一缓存管理未启用（KT_ENABLE_UNIFIED_CACHE=false）")
        return
    deleted = cache_manager.clear_cache(name if name else None)
    if deleted > 0:
        print(f"已清除 {deleted} 个缓存文件")
    else:
        print("无缓存文件可清除")


@cache_app.command("size")
def cache_size(
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """显示缓存总大小。"""
    from knowledge_tree_builder.config import load_config
    config_dict = load_config(config_path)
    cache_manager = CacheManager(
        cache_dir=config_dict.get("cache_dir", ".kb_cache/"),
        enable_unified_cache=config_dict.get("enable_unified_cache", True),
    )
    if not cache_manager.enable_unified_cache:
        print("统一缓存管理未启用（KT_ENABLE_UNIFIED_CACHE=false）")
        return
    total = cache_manager.get_cache_size()
    print(f"缓存总大小: {_format_size(total)}")


def _format_size(size: int) -> str:
    """格式化文件大小显示。"""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.2f}GB"


lineage_app = typer.Typer(
    name="lineage",
    help="数据血缘管理 — 查看和导出知识节点的血缘信息",
    add_completion=False,
)
app.add_typer(lineage_app, name="lineage")


@lineage_app.command("show")
def lineage_show(
    node_id: str = typer.Argument(..., help="知识点ID"),
    input_dir: str = typer.Option("references", "--input-dir", help="输入目录（用于定位血缘文件）"),
    detail: bool = typer.Option(False, "--detail", "-d", help="显示详细信息（包含原文片段）"),
) -> None:
    """查看某个知识点的血缘信息。"""
    cmd_lineage_show(node_id, input_dir, detail)


@lineage_app.command("export")
def lineage_export(
    input_dir: str = typer.Option("references", "--input-dir", help="输入目录（用于定位血缘文件）"),
    output: str = typer.Option("", "--output", "-o", help="输出文件路径（默认打印到控制台）"),
    detail: bool = typer.Option(False, "--detail", "-d", help="导出详细信息（包含原文）"),
) -> None:
    """导出全量血缘记录。"""
    cmd_lineage_export(input_dir, output, detail)


@app.command(hidden=True)
def run_old(
    input_dir: str = typer.Option("references", "--input-dir", help="[废弃] 输入文章目录"),
    output_dir: str = typer.Option("output", "--output-dir", help="[废弃] 中间产物输出目录"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    dry_run: bool = typer.Option(False, "--dry-run", help="[废弃] 仅预览"),
    cluster_method: str = typer.Option("hdbscan", "--method", help="[废弃] 聚类方法"),
) -> None:
    """[已废弃] 旧管线入口 — 请使用新管线: knowledge-tree-builder run"""
    cmd_run_old(input_dir, output_dir, config_path, dry_run, cluster_method)


@app.command(hidden=True)
def cluster(
    input_dir: str = typer.Option("references", "--input-dir", help="[废弃] 输入文章目录"),
    output_dir: str = typer.Option("output", "--output-dir", help="[废弃] 输出目录"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    cluster_method: str = typer.Option("hdbscan", "--method", help="[废弃] 聚类方法"),
    dry_run: bool = typer.Option(False, "--dry-run", help="[废弃] 仅预览"),
) -> None:
    """[已废弃] HDBSCAN 聚类建树 — 请使用新管线: knowledge-tree-builder run"""
    cmd_cluster(input_dir, output_dir, config_path, cluster_method, dry_run)


@app.command(hidden=True)
def validate(
    input_json: str = typer.Option(..., "--input", "-i", help="[废弃] cluster 产出的 JSON 文件路径"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
    output: str = typer.Option("", "--output", "-o", help="[废弃] 输出 JSON 文件路径"),
) -> None:
    """[已废弃] LLM 结构校验 — 请使用新管线: knowledge-tree-builder run --merged"""
    cmd_validate(input_json, config_path, output)


@app.command(hidden=True)
def extract(
    input_dir: str = typer.Option("references", "--input-dir", help="[废弃] 输入文章目录"),
    output_dir: str = typer.Option("", "--output", "-o", help="[废弃] 输出 JSON 文件路径"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """[已废弃] 从文件中提取知识点 — 请使用新管线: knowledge-tree-builder run --merged"""
    cmd_extract(input_dir, output_dir, config_path, dry_run=False)


@app.command(hidden=True)
def write(
    input_json: str = typer.Option(..., "--input", "-i", help="[废弃] 输入 JSON 文件路径"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """[已废弃] 写入 PG — 请使用新管线: knowledge-tree-builder run"""
    cmd_write(input_json, config_path)


@app.command(hidden=True)
def name(
    input_json: str = typer.Option(..., "--input", "-i", help="[废弃] cluster 产出的 JSON 文件路径"),
    output_dir: str = typer.Option("output", "--output-dir", help="[废弃] 输出目录"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """[已废弃] LLM 节点命名 — 请使用新管线: knowledge-tree-builder run"""
    cmd_name(input_json, output_dir, config_path)


@app.command(hidden=True)
def report(
    input_json: str = typer.Option(..., "--input", "-i", help="[废弃] cluster 产出的 JSON 文件路径"),
) -> None:
    """[已废弃] 查看建树报告"""
    cmd_report(input_json)


def main() -> None:
    """CLI 主入口"""
    app()


if __name__ == "__main__":
    main()