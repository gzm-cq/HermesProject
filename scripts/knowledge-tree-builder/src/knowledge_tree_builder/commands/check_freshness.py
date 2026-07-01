"""新鲜度检查命令 — 检测 text 更新后需要重新 embedding 的节点"""

from __future__ import annotations

from functools import partial
from typing import Any

import typer

from knowledge_tree_builder.config import load_config
from knowledge_tree_builder.adapters.database import DatabaseAdapter
from knowledge_tree_builder.core.freshness import (
    check_freshness,
    compute_text_hash,
    batch_update_text_hash,
    ensure_last_text_hash_column,
)
from knowledge_tree_builder.core.embeddings import batch_embed, cosine_similarity


def cmd_check_freshness(
    config_path: str,
    dry_run: bool,
    db_url: str,
    embed_base_url: str,
    embed_model: str,
    embed_api_key: str,
) -> None:
    """检查知识树中 text 发生变化需要重新 embedding 的节点。

    - 查询所有 knowledge_point 节点，比对 text hash
    - 如果有变化节点，报告数量并（可选）触发重新 embedding
    - 输出报告：多少节点新鲜、多少节点需要更新
    """
    config = load_config(config_path)
    if not db_url:
        db_url = config.get("db_url", "")
    if not db_url:
        print("   ❌ 未配置 db_url")
        raise typer.Exit(1)

    try:
        adapter = DatabaseAdapter(db_url)
    except Exception as e:
        print(f"   ❌ 数据库连接失败: {e}")
        raise typer.Exit(1)

    try:
        print("\n🔍 Embedding 新鲜度检查...")

        # 确保 last_text_hash 列存在
        ensure_last_text_hash_column(adapter)

        # 检查新鲜度
        stale_nodes = check_freshness(adapter)
        total_stale = len(stale_nodes)

        if total_stale == 0:
            print("   ✅ 所有节点都是新鲜的，无需更新")
            return

        print(f"   ⚠️  发现 {total_stale} 个节点 text 已变化，需要重新 embedding")

        if dry_run:
            print("\n   [Dry-run] 仅报告，不执行更新")
            for node_id, text in stale_nodes[:10]:
                print(f"      ID={node_id}: {text[:50]}...")
            if total_stale > 10:
                print(f"      ... 还有 {total_stale - 10} 个节点")
            return

        # 执行重新 embedding
        print(f"\n   📝 正在重新计算 {total_stale} 个节点的 embedding...")
        texts = [text for _, text in stale_nodes]
        node_ids = [node_id for node_id, _ in stale_nodes]

        embed_fn = partial(
            batch_embed,
            base_url=embed_base_url or config.get("embed_base_url", "https://api.siliconflow.cn/v1"),
            model=embed_model or config.get("embed_model", "BAAI/bge-m3"),
            api_key=embed_api_key or config.get("embed_api_key", ""),
            batch_size=config.get("embed_batch_size", 20),
        )

        embeddings = embed_fn(texts)
        if embeddings is None:
            print("   ❌ Embedding 计算失败")
            raise typer.Exit(1)

        updated = 0
        hash_updates: list[tuple[int, str]] = []
        for node_id, text, embedding in zip(node_ids, texts, embeddings):
            try:
                adapter.update_k_vector(node_id, embedding)
                text_hash = compute_text_hash(text)
                hash_updates.append((node_id, text_hash))
                updated += 1
            except Exception as e:
                print(f"   ⚠️  更新节点 {node_id} 失败: {e}")

        if hash_updates:
            batch_update_text_hash(adapter, hash_updates)

        print(f"   ✅ 成功更新 {updated}/{total_stale} 个节点")

    finally:
        adapter.close()


__all__ = ["cmd_check_freshness"]
