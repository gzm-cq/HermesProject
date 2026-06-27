"""基础命令模块。

包含最简单的 CLI 命令实现：init_db, find, backfill_k_vectors, redistribute, review。
"""

from __future__ import annotations

import os
from typing import Any

import typer

from knowledge_tree_builder.config import AppConfig, load_config
from knowledge_tree_builder.adapters.database import DatabaseAdapter


def cmd_init_db(config_path: str) -> None:
    """初始化 PG 表结构（首次部署运行）"""
    config = load_config(config_path)
    cfg = AppConfig.from_dict(config)

    if not cfg.db_url:
        print("   ❌ db_url 未配置（设置 KT_DB_URL 环境变量）")
        raise typer.Exit(1)

    print("🗄️  创建知识树相关表...")
    adapter = DatabaseAdapter(cfg.db_url)
    try:
        adapter.create_tables()
        print("   ✅ 表结构创建完成")
    except Exception as e:
        print(f"   ❌ 创建失败: {e}")
        raise
    finally:
        adapter.close()


def cmd_find(query: str, config_path: str, limit: int) -> None:
    """搜索知识树（不走 Hindsight，直接查树）。"""
    cfg = load_config(config_path)
    db_url = cfg.get("db_url", "")
    if not db_url:
        print("   ❌ 未配置 db_url")
        raise typer.Exit(1)

    adapter = DatabaseAdapter(db_url)
    try:
        cursor = adapter.cursor
        cursor.execute(
            "SELECT kt.id, kt.name, kt.node_type, kpt.text "
            "FROM knowledge_tree kt "
            "LEFT JOIN knowledge_point_texts kpt ON kt.id = kpt.tree_node_id "
            "WHERE kt.name ILIKE %s OR kpt.text ILIKE %s "
            "LIMIT %s",
            (f"%{query}%", f"%{query}%", limit),
        )
        rows = cursor.fetchall()
        if not rows:
            print(f"   🔍 未找到匹配: {query}")
            return
        print(f"🔍 找到 {len(rows)} 条结果：")
        for row in rows:
            icon = "📂" if row[2] == "subject" else "📄"
            text = row[3][:80] if row[3] else ""
            print(f"   {icon} ID={row[0]} {row[1]} | {text}")
    except Exception as e:
        print(f"   ❌ 搜索失败: {e}")
        raise typer.Exit(1)
    finally:
        adapter.close()


def cmd_backfill_k_vectors(
    dry_run: bool,
    batch_size: int,
    db_url: str,
    embed_base_url: str,
    embed_model: str,
    embed_api_key: str,
) -> None:
    """批量回填 k_vector（遍历 k_vector IS NULL 的叶子节点，计算 embedding 后写入）。"""
    from knowledge_tree_builder.scripts.backfill_k_vectors import backfill_k_vectors as _backfill

    if not db_url:
        db_url = os.environ.get("KT_DB_URL", "")
        if not db_url:
            print("❌ 请设置 KT_DB_URL 环境变量或传入 --db-url")
            raise typer.Exit(1)

    adapter = DatabaseAdapter(db_url)
    try:
        stats = _backfill(
            adapter,
            dry_run=dry_run,
            batch_size=batch_size,
            embed_base_url=embed_base_url,
            embed_model=embed_model,
            embed_api_key=embed_api_key,
        )
        if dry_run:
            print(f"   📊 预览: {stats['total']} 个节点待回填")
        else:
            print(f"   ✅ 回填完成: {stats['filled']}/{stats['total']} (errors={stats['errors']})")
    finally:
        adapter.close()


def cmd_redistribute(
    dry_run: bool,
    db_url: str,
    llm_api_url: str,
    llm_api_key: str,
    llm_model: str,
    embed_base_url: str,
    embed_model: str,
    embed_api_key: str,
) -> None:
    """重新分类 general/root 下的知识点到正确领域（3 级漏斗）。"""
    from knowledge_tree_builder.scripts.redistribute_general import redistribute_general

    if not db_url:
        db_url = os.environ.get("KT_DB_URL", "")
        if not db_url:
            print("❌ 请设置 KT_DB_URL 环境变量或传入 --db-url")
            raise typer.Exit(1)

    adapter = DatabaseAdapter(db_url)
    try:
        stats = redistribute_general(
            adapter,
            dry_run=dry_run,
            llm_api_url=llm_api_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            embed_base_url=embed_base_url,
            embed_model=embed_model,
            embed_api_key=embed_api_key,
        )
        if dry_run:
            print(f"   📊 预览: {stats['total']} 条待迁移")
        else:
            print(f"   ✅ 迁移完成: 已迁移 {stats['migrated']}/{stats['total']} 条 "
                  f"(errors={stats['errors']})")
    finally:
        adapter.close()


def cmd_review(action: str, review_id: int, review_type: str, config_path: str) -> None:
    """审查队列操作：列出/接受/拒绝审查项。"""
    from knowledge_tree_builder.consolidate.review import (
        list_reviews as _list,
        accept_review as _accept,
        reject_review as _reject,
    )

    config = load_config(config_path)
    db_url = config.get("db_url", "")
    if not db_url:
        print("   ❌ 未配置 db_url")
        raise typer.Exit(1)

    try:
        adapter = DatabaseAdapter(db_url)

        if action == "list":
            items = _list(adapter, review_type=review_type or None)
            if not items:
                print("   审查队列为空")
                return
            print(f"\n📋 审查队列 ({len(items)} 条):")
            for item in items:
                icon = {
                    "contradiction": "⚡",
                    "orphan": "📭",
                    "move_suggestion": "🔄",
                    "consistency_warning": "⚠️",
                    "incomplete_split": "✂️",
                    "obsolete": "🗑️",
                }.get(item.get("type", ""), "📌")
                print(f"   {icon} ID={item['id']} [{item['type']}] {item.get('new_text', '')[:60]}")
                print(f"       状态: {item.get('status', 'pending')} | 来源: {item.get('existing_text', '')[:40]}")

        elif action == "accept":
            if review_id <= 0:
                print("   ❌ 请指定 --id")
                return
            if _accept(review_id, adapter):
                print(f"   ✅ 已接受审查项 ID={review_id}")
            else:
                print(f"   ❌ 接受失败 ID={review_id}")

        elif action == "reject":
            if review_id <= 0:
                print("   ❌ 请指定 --id")
                return
            if _reject(review_id, adapter):
                print(f"   ✅ 已拒绝审查项 ID={review_id}")
            else:
                print(f"   ❌ 拒绝失败 ID={review_id}")

        else:
            print(f"   ❌ 未知操作: {action}，可选 list/accept/reject")

        adapter.close()

    except Exception as e:
        print(f"   ❌ 操作失败: {e}")


__all__ = [
    "cmd_init_db",
    "cmd_find",
    "cmd_backfill_k_vectors",
    "cmd_redistribute",
    "cmd_review",
]