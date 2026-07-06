#!/usr/bin/env python3
"""
知识树节点 k_vector 回填工具 — 一次性补全所有类型

用法：
  python3 backfill_k_vector.py          # 干跑：统计缺失
  python3 backfill_k_vector.py --apply  # 实际写入
"""

import argparse
import logging
import os
from typing import Any

import httpx
import numpy as np
import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def get_db():
    """返回 PG 连接，优先用 KT_DB_URL（与知识树其他脚本一致）。"""
    dsn = os.environ.get("KT_DB_URL") or os.environ.get("HERMES_DSN")
    if not dsn:
        raise RuntimeError("KT_DB_URL 未设置，请先 source /root/.hermes/.env")
    return psycopg2.connect(dsn)


def _parse_k_vector(v) -> list[float] | None:
    """将 pgvector 返回值解析为 Python float list。"""
    if v is None:
        return None
    # pgvector register_vector 可能返回 list、str 或 buffer
    if isinstance(v, list):
        return [float(x) for x in v]
    if isinstance(v, str):
        v = v.strip("[]").strip()
        if not v:
            return None
        parts = v.split(",")
        return [float(x.strip()) for x in parts if x.strip()]
    # buffer / memoryview fallback
    try:
        import struct
        return [struct.unpack("f", v[i:i+4])[0] for i in range(0, len(v), 4)]
    except Exception:
        return None


def get_embedding(text: str) -> list[float] | None:
    """调用 SiliconFlow embedding API 获取向量（不走 LiteLLM，LiteLLM 无 embedding 模型）。"""
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key:
        logger.warning("  ❌ SILICONFLOW_API_KEY 未设置")
        return None
    try:
        resp = httpx.post(
            "https://api.siliconflow.cn/v1/embeddings",
            json={"model": "BAAI/bge-m3", "input": text},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]
    except Exception as e:
        logger.warning("  ❌ embedding API 调用失败: %s", e)
        return None


def get_child_vectors_recursive(cur, node_id: int, max_depth: int = 3) -> list[list[float]]:
    """递归获取子孙节点的 k_vector，最多向下查 max_depth 层。"""
    collected = []
    cur.execute(
        "SELECT id, node_type, k_vector FROM knowledge_tree "
        "WHERE parent_id = %s AND k_vector IS NOT NULL ORDER BY id",
        (node_id,),
    )
    for row in cur.fetchall():
        v = _parse_k_vector(row["k_vector"])
        if v is not None:
            collected.append(v)
        if max_depth > 0 and row["node_type"] == "subject":
            collected.extend(get_child_vectors_recursive(cur, row["id"], max_depth - 1))
    return collected


def backfill_all(args: argparse.Namespace) -> dict[str, int]:
    """一次性补全所有缺失 k_vector（knowledge_point + subject）。"""
    stats: dict[str, int] = {"kp_found": 0, "subj_found": 0,
                              "kp_filled": 0, "subj_filled": 0,
                              "failed": 0, "skipped": 0}

    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 查所有缺失 k_vector 的节点
        cur.execute(
            "SELECT id, name, node_type, parent_id FROM knowledge_tree "
            "WHERE k_vector IS NULL ORDER BY node_type, id"
        )
        rows = cur.fetchall()
        kp_rows = [r for r in rows if r["node_type"] == "knowledge_point"]
        subj_rows = [r for r in rows if r["node_type"] == "subject"]
        stats["kp_found"] = len(kp_rows)
        stats["subj_found"] = len(subj_rows)

        logger.info("需要补 k_vector 的 knowledge_point: %d", len(kp_rows))
        logger.info("需要补 k_vector 的 subject:       %d", len(subj_rows))
        if not rows:
            logger.info("✅ 所有节点 k_vector 已完整")
            return stats

        if not args.apply:
            logger.info("🔍 干跑模式，加 --apply 才写入")
            return stats

        # 补 knowledge_point — embedding API
        for row in kp_rows:
            text = row.get("name") or ""
            if not text:
                stats["skipped"] += 1
                continue
            vec = get_embedding(text[:4096])
            if vec is None:
                stats["failed"] += 1
                continue
            cur.execute(
                "UPDATE knowledge_tree SET k_vector = %s::vector, updated_at = NOW() WHERE id = %s",
                ("[" + ",".join(str(v) for v in vec) + "]", row["id"]),
            )
            stats["kp_filled"] += 1

        # 补 subject — 子孙节点向量平均
        for row in subj_rows:
            child_vecs = get_child_vectors_recursive(cur, row["id"], max_depth=3)
            if not child_vecs:
                # 没有子孙向量 → 用自身 name 做 embedding 兜底
                logger.info("  subject %s (%s): 无子节点向量，用自身 name embed 兜底",
                            row["id"], row["name"])
                name = row.get("name") or ""
                if not name:
                    stats["skipped"] += 1
                    continue
                vec = get_embedding(name[:4096])
                if vec is None:
                    stats["failed"] += 1
                    continue
                cur.execute(
                    "UPDATE knowledge_tree SET k_vector = %s::vector, updated_at = NOW() WHERE id = %s",
                    ("[" + ",".join(str(v) for v in vec) + "]", row["id"]),
                )
                stats["subj_filled"] += 1
                continue

            avg = np.mean(child_vecs, axis=0).tolist()
            cur.execute(
                "UPDATE knowledge_tree SET k_vector = %s::vector, updated_at = NOW() WHERE id = %s",
                ("[" + ",".join(str(v) for v in avg) + "]", row["id"]),
            )
            stats["subj_filled"] += 1

        conn.commit()

    finally:
        conn.close()

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="知识树节点 k_vector 回填 — 一次性补全所有类型"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入。不加则只查询统计。",
    )
    args = parser.parse_args()

    stats = backfill_all(args)

    logger.info("")
    logger.info("=== 汇总 ===")
    logger.info("  knowledge_point: 发现 %d, 已补 %d", stats["kp_found"], stats["kp_filled"])
    logger.info("  subject:         发现 %d, 已补 %d", stats["subj_found"], stats["subj_filled"])
    logger.info("  失败:     %d", stats["failed"])
    logger.info("  跳过:     %d", stats["skipped"])

    if not args.apply:
        logger.info("")
        logger.info("🔍 干跑模式。加 --apply 执行写入。")


if __name__ == "__main__":
    main()