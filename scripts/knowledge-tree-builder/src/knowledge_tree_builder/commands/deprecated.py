"""废弃命令模块。

包含已废弃的旧管线命令，保留供兼容性参考。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from knowledge_tree_builder.config import AppConfig, load_config
from knowledge_tree_builder.adapters.database import DatabaseAdapter
from knowledge_tree_builder.core.validator import validate_tree


def cmd_run_old(
    input_dir: str,
    output_dir: str,
    config_path: str,
    dry_run: bool,
    cluster_method: str,
) -> None:
    """[已废弃] 旧管线入口 — 请使用新管线: knowledge-tree-builder run"""
    print("⚠️  run_old 已废弃，请使用: knowledge-tree-builder run")
    config = load_config(config_path)
    cfg = AppConfig.from_dict(config)

    from knowledge_tree_builder.core.extractor import extract_knowledge_tree

    input_path = Path(input_dir)
    articles: list[dict[str, Any]] = []
    for f in input_path.iterdir():
        if f.suffix.lower() in (".md", ".txt", ".yaml", ".yml"):
            text = f.read_text(encoding="utf-8")
            articles.append({"title": f.stem, "text": text})

    if not articles:
        print("   ❌ 输入目录无文章")
        raise typer.Exit(1)

    results = extract_knowledge_tree(articles, config=cfg, cluster_method=cluster_method)

    if not dry_run:
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        with open(output_path / "tree.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"   ✅ 输出: {output_path}/tree.json")
    else:
        print(f"   📊 预览: {len(results.get('tree', []))} 个节点")


def cmd_cluster(
    input_dir: str,
    output_dir: str,
    config_path: str,
    cluster_method: str,
    dry_run: bool,
) -> None:
    """[已废弃] HDBSCAN 聚类建树 — 请使用新管线: knowledge-tree-builder run"""
    print("⚠️  cluster 已废弃，请使用: knowledge-tree-builder run")
    config = load_config(config_path)
    cfg = AppConfig.from_dict(config)

    from knowledge_tree_builder.core.clustering import cluster_knowledge_points

    input_path = Path(input_dir)
    articles: list[dict[str, Any]] = []
    for f in input_path.iterdir():
        if f.suffix.lower() in (".md", ".txt"):
            text = f.read_text(encoding="utf-8")
            articles.append({"title": f.stem, "text": text})

    if not articles:
        print("   ❌ 输入目录无文章")
        raise typer.Exit(1)

    results = cluster_knowledge_points(articles, config=cfg, method=cluster_method)

    if not dry_run:
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        with open(output_path / "clustered.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"   ✅ 输出: {output_path}/clustered.json")
    else:
        print(f"   📊 预览: {len(results.get('clusters', []))} 个簇")


def cmd_validate(input_json: str, config_path: str, output: str) -> None:
    """[已废弃] LLM 结构校验 — 请使用新管线: knowledge-tree-builder run --merged"""
    print("⚠️  validate 已废弃，请使用: knowledge-tree-builder run --merged")
    config = load_config(config_path)
    cfg = AppConfig.from_dict(config)

    in_path = Path(input_json)
    if not in_path.exists():
        print(f"   ❌ 输入文件不存在: {input_json}")
        raise typer.Exit(1)

    with open(in_path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    tree = data.get("tree", [])
    if not tree:
        print("   ❌ 树结构为空")
        raise typer.Exit(1)

    print(f"\n🔍 LLM 结构校验中...")
    validated = validate_tree(tree, cfg)

    if output:
        out_path = Path(output)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(validated, f, ensure_ascii=False, indent=2)
        print(f"   ✅ 输出: {out_path}")
    else:
        print(f"   ✅ 校验完成: {len(validated.get('tree', []))} 个有效节点")


def cmd_extract(
    input_dir: str,
    output_dir: str,
    config_path: str,
    dry_run: bool,
) -> None:
    """[已废弃] 知识点提取 — 请使用新管线: knowledge-tree-builder run"""
    print("⚠️  extract 已废弃，请使用: knowledge-tree-builder run")
    config = load_config(config_path)
    cfg = AppConfig.from_dict(config)

    from knowledge_tree_builder.core.extractor import extract_knowledge_points

    input_path = Path(input_dir)
    articles: list[dict[str, Any]] = []
    for f in input_path.iterdir():
        if f.suffix.lower() in (".md", ".txt"):
            text = f.read_text(encoding="utf-8")
            articles.append({"title": f.stem, "text": text})

    if not articles:
        print("   ❌ 输入目录无文章")
        raise typer.Exit(1)

    results = extract_knowledge_points(articles, config=cfg)

    if not dry_run:
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        with open(output_path / "extracted.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"   ✅ 输出: {output_path}/extracted.json")
    else:
        print(f"   📊 预览: {len(results.get('knowledge_points', []))} 条知识点")


def cmd_write(input_json: str, config_path: str) -> None:
    """[已废弃] 写入 PG — 请使用新管线: knowledge-tree-builder run"""
    print("⚠️  write 已废弃，请使用: knowledge-tree-builder run")
    config = load_config(config_path)
    cfg = AppConfig.from_dict(config)

    if not cfg.db_url:
        print("   ❌ db_url 未配置")
        raise typer.Exit(1)

    in_path = Path(input_json)
    if not in_path.exists():
        print(f"   ❌ 输入文件不存在: {input_json}")
        raise typer.Exit(1)

    with open(in_path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    adapter = DatabaseAdapter(cfg.db_url)
    try:
        from knowledge_tree_builder.core.writer import write_to_db
        write_to_db(data, adapter)
        print("   ✅ 写入完成")
    finally:
        adapter.close()


def cmd_name(input_json: str, output_dir: str, config_path: str) -> None:
    """[已废弃] LLM 节点命名 — 请使用新管线: knowledge-tree-builder run"""
    print("⚠️  name 已废弃，请使用: knowledge-tree-builder run")
    config = load_config(config_path)
    cfg = AppConfig.from_dict(config)

    in_path = Path(input_json)
    if not in_path.exists():
        print(f"   ❌ 输入文件不存在: {input_json}")
        raise typer.Exit(1)

    with open(in_path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    from knowledge_tree_builder.core.namer import name_nodes
    named = name_nodes(data, cfg)

    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    with open(output_path / "named.json", "w", encoding="utf-8") as f:
        json.dump(named, f, ensure_ascii=False, indent=2)
    print(f"   ✅ 输出: {output_path}/named.json")


def cmd_report(input_json: str) -> None:
    """[已废弃] 查看建树报告"""
    print("⚠️  report 已废弃")


__all__ = [
    "cmd_run_old",
    "cmd_cluster",
    "cmd_validate",
    "cmd_extract",
    "cmd_write",
    "cmd_name",
    "cmd_report",
]