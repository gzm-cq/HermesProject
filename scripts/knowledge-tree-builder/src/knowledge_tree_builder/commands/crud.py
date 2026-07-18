"""CRUD 命令模块。

包含知识树的增删改查命令：add, tree, ingest, edit, remove, merge, move。
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import typer

from knowledge_tree_builder.config import load_config
from knowledge_tree_builder.adapters.database import DatabaseAdapter
from knowledge_tree_builder.core.admission import filter_knowledge_points
from knowledge_tree_builder.core.incremental import dedup_before_insert, detect_conflict
from knowledge_tree_builder.core.embeddings import batch_embed, cosine_similarity
from knowledge_tree_builder.core.extractor import extract_knowledge_points
from knowledge_tree_builder.core.clustering import build_tree
from knowledge_tree_builder.models import EMBEDDING_DIM


def cmd_add(text: str, title: str, config_path: str, dry_run: bool, verbose: bool) -> None:
    """添加一条新知识点。

    执行 LLM 提取 → 准入过滤 → 增量去重 → 矛盾检测。
    有 DB 连接时尝试定位到已知科目。
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    print(f"\n📝 新知识点: {text[:80]}{'...' if len(text) > 80 else ''}")

    # Step 1.5: 准入过滤
    passed = filter_knowledge_points([text])
    if not passed:
        print("   ❌ 未通过准入过滤（可能是模糊概括或太短）")
        raise typer.Exit(1)
    print("   ✅ 通过准入过滤")

    # Step 5: 增量去重 + 矛盾检测（有 DB 时）
    try:
        cfg = load_config(config_path)
        db_url = cfg.get("db_url") or cfg.get("database", {}).get("url")
        if not db_url:
            raise KeyError("db_url")
        db = DatabaseAdapter(db_url)
        leaf_nodes = db.get_leaf_nodes()
        print(f"   📊 知识树现有 {len(leaf_nodes)} 个叶子节点")

        embed_fn = partial(
            batch_embed,
            base_url=cfg.get("embed_base_url", ""),
            api_key=cfg.get("embed_api_key", ""),
        )

        embeddings = embed_fn([text])
        if embeddings:
            # 去重检查
            dup_id = dedup_before_insert(
                text, leaf_nodes, embed_fn, cosine_similarity,
            )
            if dup_id:
                print(f"   🔗 与节点 ID={dup_id} 重复，跳过插入")
                if not dry_run:
                    db.update_source_ids(dup_id, 0)
                    db.log_use("cli_add", [dup_id], text)
                raise typer.Exit(0)

            # 矛盾检测
            conflicts = detect_conflict(
                text, leaf_nodes, embed_fn, cosine_similarity,
                db_adapter=db if not dry_run else None,
            )
            if conflicts:
                print(f"   ⚠️  检测到 {len(conflicts)} 个潜在矛盾（已存入 review_queue）")

        if not dry_run:
            point_id = db.insert_node(
                name=text[:128], node_type="knowledge_point",
                source_ids=[0],
            )
            db.conn.commit()
            db.log_use("cli_add", [point_id], text)
            print(f"   ✅ 已写入知识点 ID={point_id}")
        else:
            print("   🔍 dry-run 模式，未实际写入")

        db.close()

    except Exception as e:
        print(f"   ℹ️  跳过 DB 操作: {e}")


def cmd_tree(max_depth: int, config_path: str) -> None:
    """查看知识树结构（可视化科目层级）。"""
    cfg = load_config(config_path)
    db_url = cfg.get("db_url", "")
    if not db_url:
        print("   ❌ 未配置 db_url")
        raise typer.Exit(1)

    adapter = DatabaseAdapter(db_url)
    try:
        cursor = adapter.cursor
        cursor.execute(
            "WITH RECURSIVE tree_path AS ("
            "  SELECT id, parent_id, name, node_type, 0 AS depth, "
            "         ARRAY[name::varchar] AS path"
            "  FROM knowledge_tree WHERE parent_id IS NULL"
            "  UNION ALL"
            "  SELECT kt.id, kt.parent_id, kt.name, kt.node_type, "
            "         tp.depth + 1, tp.path || kt.name"
            "  FROM knowledge_tree kt"
            "  JOIN tree_path tp ON kt.parent_id = tp.id"
            "  WHERE tp.depth < %s"
            ")"
            "SELECT id, name, node_type, depth, path FROM tree_path "
            "ORDER BY path"
            , (max_depth,))
        rows = cursor.fetchall()
        if not rows:
            print("   🌲 知识树为空")
            return
        for row in rows:
            indent = "  " * row[3]
            icon = "📂" if row[2] == "subject" else "📄"
            print(f"{indent}{icon} {row[1]} (ID={row[0]})")
    except Exception as e:
        print(f"   ❌ 查询失败: {e}")
        raise typer.Exit(1)
    finally:
        adapter.close()


def cmd_ingest(file_path: str, config_path: str, dry_run: bool, verbose: bool) -> None:
    """从文件批量提取知识点 → 准入过滤 → 建树报告。

    相当于单文件版的 extract + cluster + report。
    """
    fpath = Path(file_path)
    if not fpath.exists():
        print(f"   ❌ 文件不存在: {file_path}")
        raise typer.Exit(1)

    text = fpath.read_text(encoding="utf-8")
    title = fpath.stem
    print(f"📄 {fpath.name} ({len(text)} 字符)")

    # 登记来源文章
    cfg = load_config(config_path)

    try:
        db = DatabaseAdapter(cfg.get("db_url", ""))
        article_id = db.insert_article(str(fpath), title)
        print(f"   📝 来源文章已登记 (ID={article_id})")
        db.close()
    except Exception as e:
        print(f"   ℹ️ 来源登记跳过: {e}")

    # Step 1: 提取
    print("📝 Step 1: LLM 提取知识点...")
    points = extract_knowledge_points(
        text, article_title=title,
        api_url=cfg.get("llm_api_url", ""),
        api_key=cfg.get("llm_api_key", ""),
        model=cfg.get("llm_model", "s-deepseek-v4-flash"),
    )
    print(f"   提取到 {len(points)} 条原始知识点")
    if verbose:
        for p in points:
            print(f"     - {p}")

    # Step 1.5: 准入
    print("🔍 Step 1.5: 准入过滤...")
    valid = filter_knowledge_points(points)
    print(f"   通过 {len(valid)}/{len(points)} 条")

    if not valid:
        print("   ❌ 无有效知识点")
        raise typer.Exit(1)

    # Step 2: 聚类 + 建树报告
    print("🌲 Step 2: 聚类报告...")
    embeddings = batch_embed(
        valid,
        base_url=cfg.get("embed_base_url", ""),
        api_key=cfg.get("embed_api_key", ""),
        model=cfg.get("embed_model", "BAAI/bge-m3"),
        batch_size=cfg.get("embed_batch_size", 20),
    )
    if not embeddings:
        print("   ❌ Embedding 失败")
        raise typer.Exit(1)

    result = build_tree(valid, np.array(embeddings))
    report = result.get("report", {})
    print("\n📊 建树报告:")
    for key, value in report.items():
        if value is not None:
            print(f"   {key}: {value}")

    if result.get("noise"):
        print(f"\n⚠️  噪声点 ({len(result['noise'])} 条):")
        for n in result["noise"][:5]:
            print(f"     - {n}")


def cmd_edit(node_id: int, name: str, text: str, config_path: str) -> None:
    """修正知识点文本或名称。"""
    cfg = load_config(config_path)
    db_url = cfg.get("db_url", "")
    if not db_url:
        print("   ❌ 未配置 db_url")
        raise typer.Exit(1)

    adapter = DatabaseAdapter(db_url)
    try:
        cursor = adapter.cursor

        cursor.execute(
            "SELECT id, name FROM knowledge_tree WHERE id = %s", (node_id,),
        )
        node = cursor.fetchone()
        if not node:
            print(f"   ❌ 节点 ID={node_id} 不存在")
            return

        if name:
            cursor.execute(
                "UPDATE knowledge_tree SET name = %s, updated_at = NOW() "
                "WHERE id = %s", (name, node_id),
            )
            print(f"   ✅ 名称已更新: {node[1]} → {name}")

        if text:
            cursor.execute(
                "UPDATE knowledge_point_texts SET text = %s "
                "WHERE tree_node_id = %s",
                (text, node_id),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO knowledge_point_texts (tree_node_id, text) "
                    "VALUES (%s, %s)", (node_id, text),
                )
            print(f"   ✅ 原文已更新")

        adapter.conn.commit()
    except Exception as e:
        print(f"   ❌ 更新失败: {e}")
    finally:
        adapter.close()


def cmd_remove(node_id: int, config_path: str, force: bool) -> None:
    """删除知识点（错误或过时）。"""
    cfg = load_config(config_path)
    db_url = cfg.get("db_url", "")
    if not db_url:
        print("   ❌ 未配置 db_url")
        raise typer.Exit(1)

    adapter = DatabaseAdapter(db_url)
    try:
        cursor = adapter.cursor

        cursor.execute(
            "SELECT id, name, node_type FROM knowledge_tree WHERE id = %s",
            (node_id,),
        )
        node = cursor.fetchone()
        if not node:
            print(f"   ❌ 节点 ID={node_id} 不存在")
            return

        # 检查是否有子节点
        cursor.execute(
            "SELECT COUNT(*) FROM knowledge_tree WHERE parent_id = %s",
            (node_id,),
        )
        child_count = cursor.fetchone()[0]

        if child_count > 0 and not force:
            print(f"   ⚠️  节点有 {child_count} 个子节点，使用 --force 强制删除")
            return

        cursor.execute("DELETE FROM knowledge_point_texts WHERE tree_node_id = %s", (node_id,))
        if force:
            # 递归删除所有后代节点（CTE）
            cursor.execute(
                "WITH RECURSIVE descendants AS ("
                "  SELECT id FROM knowledge_tree WHERE parent_id = %s"
                "  UNION ALL"
                "  SELECT kt.id FROM knowledge_tree kt"
                "  JOIN descendants d ON kt.parent_id = d.id"
                ")"
                "DELETE FROM knowledge_tree WHERE id IN (SELECT id FROM descendants)",
                (node_id,),
            )
        cursor.execute("DELETE FROM knowledge_tree WHERE id = %s", (node_id,))
        adapter.conn.commit()
        print(f"   ✅ 已删除: {node[1]} (ID={node[0]})")
    except Exception as e:
        print(f"   ❌ 删除失败: {e}")
    finally:
        adapter.close()


def cmd_merge(keep_id: int, remove_id: int, config_path: str, dry_run: bool) -> None:
    """合并两个重复的知识点（合并 source_ids 后删除一个）。"""
    cfg = load_config(config_path)
    db_url = cfg.get("db_url", "")
    if not db_url:
        print("   ❌ 未配置 db_url")
        raise typer.Exit(1)

    adapter = DatabaseAdapter(db_url)
    try:
        cursor = adapter.cursor

        cursor.execute(
            "SELECT id, name FROM knowledge_tree WHERE id IN (%s, %s)",
            (keep_id, remove_id),
        )
        rows = cursor.fetchall()
        if len(rows) < 2:
            print(f"   ❌ 一个或两个节点不存在")
            return

        nodes = {r[0]: r[1] for r in rows}
        print(f"   📦 保留: {nodes[keep_id]} (ID={keep_id})")
        print(f"   🗑️  合并: {nodes[remove_id]} (ID={remove_id})")

        if not dry_run:
            # 合并 source_ids
            cursor.execute(
                "UPDATE knowledge_tree SET source_ids = "
                "ARRAY(SELECT DISTINCT unnest("
                "  (SELECT source_ids FROM knowledge_tree WHERE id = %s) || "
                "  (SELECT source_ids FROM knowledge_tree WHERE id = %s)"
                ")) WHERE id = %s",
                (keep_id, remove_id, keep_id),
            )
            # 移动 knowledge_point_texts
            cursor.execute(
                "UPDATE knowledge_point_texts SET tree_node_id = %s "
                "WHERE tree_node_id = %s", (keep_id, remove_id),
            )
            # 删除被合并的节点
            cursor.execute("DELETE FROM knowledge_tree WHERE id = %s", (remove_id,))
            adapter.conn.commit()
            print(f"   ✅ 已合并")
        else:
            print(f"   🔍 Dry-run，未实际执行")

    except Exception as e:
        print(f"   ❌ 合并失败: {e}")
    finally:
        adapter.close()


def cmd_move(node_id: int, to: int, config_path: str, dry_run: bool) -> None:
    """移动知识点到另一个科目下（自动更新 K 向量）。"""
    cfg = load_config(config_path)
    db_url = cfg.get("db_url", "")
    if not db_url:
        print("   ❌ 未配置 db_url")
        raise typer.Exit(1)

    adapter = DatabaseAdapter(db_url)
    try:
        cursor = adapter.cursor

        # 查原节点
        cursor.execute(
            "SELECT id, name, parent_id FROM knowledge_tree WHERE id = %s",
            (node_id,),
        )
        node = cursor.fetchone()
        if not node:
            print(f"   ❌ 节点 ID={node_id} 不存在")
            return

        # 查目标科目
        cursor.execute(
            "SELECT id, name FROM knowledge_tree WHERE id = %s", (to,),
        )
        target = cursor.fetchone()
        if not target:
            print(f"   ❌ 目标 ID={to} 不存在")
            return

        old_parent = node[2]
        print(f"   📦 节点: {node[1]} (ID={node[0]})")
        print(f"   📍 当前位置: {'根' if old_parent is None else f'ID={old_parent}'}")
        print(f"   🎯 目标位置: {target[1]} (ID={target[0]})")

        # 获取节点的 k_vector
        cursor.execute(
            "SELECT k_vector FROM knowledge_tree WHERE id = %s", (node_id,),
        )
        node_kv = cursor.fetchone()
        node_k_vector = node_kv[0] if node_kv and node_kv[0] else None

        # k_vector 为 NULL 时，尝试从文本计算
        if node_k_vector is None:
            cursor.execute(
                "SELECT name FROM knowledge_tree WHERE id = %s", (node_id,),
            )
            name_row = cursor.fetchone()
            if name_row and name_row[0]:
                emb = batch_embed(
                    [name_row[0]],
                    base_url=cfg.get("embed_base_url", ""),
                    api_key=cfg.get("embed_api_key", ""),
                    model=cfg.get("embed_model", "BAAI/bge-m3"),
                    batch_size=cfg.get("embed_batch_size", 20),
                )
                if emb:
                    node_k_vector = emb[0]

        if not dry_run:
            cursor.execute(
                "UPDATE knowledge_tree SET parent_id = %s, updated_at = NOW() "
                "WHERE id = %s", (to, node_id),
            )

            # 更新旧父科的 K 向量（移除该节点的贡献）
            if old_parent is not None and node_k_vector is not None:
                cursor.execute(
                    "SELECT k_vector, placement_count FROM knowledge_tree WHERE id = %s",
                    (old_parent,),
                )
                old_row = cursor.fetchone()
                if old_row and old_row[0] and old_row[1] > 1:
                    old_k = np.array(old_row[0], dtype=np.float32)
                    node_k = np.array(node_k_vector, dtype=np.float32)
                    new_count = old_row[1] - 1
                    new_k = (old_k * old_row[1] - node_k) / new_count
                    cursor.execute(
                        "UPDATE knowledge_tree SET k_vector = %s, placement_count = %s "
                        "WHERE id = %s",
                        (new_k.tolist(), new_count, old_parent),
                    )

            # 更新新父科的 K 向量（加入该节点的贡献）
            if node_k_vector is not None:
                cursor.execute(
                    "SELECT k_vector, placement_count FROM knowledge_tree WHERE id = %s",
                    (to,),
                )
                new_row = cursor.fetchone()
                if new_row:
                    new_k = np.array(new_row[0], dtype=np.float32) if new_row[0] else np.zeros(EMBEDDING_DIM, dtype=np.float32)
                    node_k = np.array(node_k_vector, dtype=np.float32)
                    new_count = new_row[1] + 1
                    alpha = min(1.0 / new_count, 0.1)
                    updated_k = (1 - alpha) * new_k + alpha * node_k
                    cursor.execute(
                        "UPDATE knowledge_tree SET k_vector = %s, placement_count = %s "
                        "WHERE id = %s",
                        (updated_k.tolist(), new_count, to),
                    )

            adapter.conn.commit()
            print(f"   ✅ 已移动（K 向量已更新）")
        else:
            print(f"   🔍 Dry-run，未实际执行")

    except Exception as e:
        print(f"   ❌ 移动失败: {e}")
    finally:
        adapter.close()


__all__ = [
    "cmd_add",
    "cmd_tree",
    "cmd_ingest",
    "cmd_edit",
    "cmd_remove",
    "cmd_merge",
    "cmd_move",
]