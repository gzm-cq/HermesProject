"""数据血缘命令模块。

包含 lineage 相关的 CLI 命令实现：
- cmd_lineage_show: 查看某个知识点的血缘
- cmd_lineage_export: 导出全量血缘
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer

from knowledge_tree_builder.core.lineage import LineageTracker


def _find_lineage_file(input_dir: str) -> str:
    """查找血缘文件。

    Args:
        input_dir: 输入目录名或路径

    Returns:
        血缘文件路径
    """
    dir_name = Path(input_dir).name
    candidate = f".kb_lineage_{dir_name}.json"
    if os.path.exists(candidate):
        return candidate
    if os.path.exists(input_dir):
        candidate2 = str(Path(input_dir).parent / f".kb_lineage_{dir_name}.json")
        if os.path.exists(candidate2):
            return candidate2
    return candidate


def cmd_lineage_show(
    node_id: str,
    input_dir: str = "references",
    detail: bool = False,
) -> None:
    """查看某个知识点的血缘信息。

    Args:
        node_id: 知识点ID
        input_dir: 输入目录（用于定位血缘文件）
        detail: 是否显示详细信息
    """
    lineage_file = _find_lineage_file(input_dir)
    if not os.path.exists(lineage_file):
        print(f"   ❌ 血缘文件不存在: {lineage_file}")
        print("   提示: 请先运行管线并启用数据血缘 (--enable-data-lineage)")
        raise typer.Exit(1)

    tracker = LineageTracker.load_from_file(lineage_file, detail_level="full" if detail else "basic")
    record = tracker.get_record(node_id)

    if record is None:
        print(f"   ❌ 未找到节点血缘记录: {node_id}")
        print(f"   提示: 共 {tracker.count()} 条记录，可使用 lineage export 查看全部")
        raise typer.Exit(1)

    print(f"\n📊 血缘记录 - {node_id}")
    print(f"{'=' * 50}")
    print(f"   来源文章: {record.source_article}")
    print(f"   提取方式: {record.extraction_method}")
    print(f"   版本号:   {record.version}")
    print(f"   创建时间: {record.created_at}")
    print(f"   更新时间: {record.updated_at}")
    print(f"\n   处理步骤 ({len(record.processing_steps)} 步):")
    for i, step in enumerate(record.processing_steps, 1):
        print(f"      {i}. {step}")

    if detail and record.source_text:
        print(f"\n   原文片段:")
        print(f"      {record.source_text[:200]}{'...' if len(record.source_text) > 200 else ''}")

    if record.metadata:
        print(f"\n   元数据:")
        print(json.dumps(record.metadata, ensure_ascii=False, indent=6))

    print()


def cmd_lineage_export(
    input_dir: str = "references",
    output: str = "",
    detail: bool = False,
) -> None:
    """导出全量血缘记录。

    Args:
        input_dir: 输入目录（用于定位血缘文件）
        output: 输出文件路径（默认打印到控制台）
        detail: 是否导出详细信息（包含原文）
    """
    lineage_file = _find_lineage_file(input_dir)
    if not os.path.exists(lineage_file):
        print(f"   ❌ 血缘文件不存在: {lineage_file}")
        print("   提示: 请先运行管线并启用数据血缘 (--enable-data-lineage)")
        raise typer.Exit(1)

    tracker = LineageTracker.load_from_file(lineage_file, detail_level="full" if detail else "basic")
    records = tracker.all_records()

    if not records:
        print("   ⚠️ 无血缘记录")
        return

    if output:
        tracker.save_to_file(output)
        print(f"   ✅ 已导出 {len(records)} 条记录到: {output}")
    else:
        print(f"\n📊 全量血缘记录 ({len(records)} 条)")
        print(f"{'=' * 50}")
        for i, record in enumerate(records, 1):
            steps = " → ".join(record.processing_steps)
            print(f"   {i}. [{record.node_id}]")
            print(f"      来源: {record.source_article}")
            print(f"      步骤: {steps}")
            print(f"      版本: v{record.version}")
            if detail and record.source_text:
                text_preview = record.source_text[:60] + ("..." if len(record.source_text) > 60 else "")
                print(f"      原文: {text_preview}")
            print()


__all__ = [
    "cmd_lineage_show",
    "cmd_lineage_export",
]
