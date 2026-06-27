"""填充 knowledge_tree_edges — 构建知识点之间的关联边。

⚠️  已废弃：本脚本功能已迁移到 consolidation.py 的 build_kp_edges() 方法。
建议使用新 API：
  - knowledge-tree-builder consolidate run --build-edges
  - 或直接调用 ConsolidationEngine().build_kp_edges(db_adapter)

保留此文件仅供兼容性参考，未来版本将移除。

三种建边策略（参考 SAG 的 co-occurrence + vector bridge）：
  1. 同源共现：同一 source_id 的知识点两两建边（同源天然关联）
  2. 向量桥接：跨 subject 的 k_vector cosine > 0.85 建边
  3. 同科高相似：同一 subject 下 cosine > 0.95 建边

用法:
  python3 -m knowledge_tree_builder.scripts.build_edges [--dry-run] [--threshold-vector 0.85] [--only-source]
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from itertools import combinations
from typing import Any

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_edges")

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5434,
    "user": "postgres",
    "password": "postgres",
    "dbname": "hindsight",
}


def get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(**DB_CONFIG)


def existing_edge_pairs(conn) -> set[tuple[int, int]]:
    """查询已有边，避免重复插入。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT from_node_id, to_node_id FROM knowledge_tree_edges WHERE relation_type = 'related'"
    )
    existing = set()
    for row in cur.fetchall():
        existing.add((int(row[0]), int(row[1])))
        existing.add((int(row[1]), int(row[0])))  # 对称
    return existing


def upsert_edge(cur, from_id: int, to_id: int, count: int = 1):
    """UPSERT 一条边。"""
    if from_id == to_id:
        return
    try:
        cur.execute(
            """
            INSERT INTO knowledge_tree_edges (from_node_id, to_node_id, relation_type, cooccurrence_count)
            VALUES (%s, %s, 'related', %s)
            ON CONFLICT (from_node_id, to_node_id, relation_type)
            DO UPDATE SET cooccurrence_count = knowledge_tree_edges.cooccurrence_count + EXCLUDED.cooccurrence_count,
                          updated_at = now()
            """,
            (from_id, to_id, count),
        )
    except Exception as e:
        logger.warning("建边失败 (%d, %d): %s", from_id, to_id, e)


# ── 策略 1: 同源共现 ──


def build_source_edges(conn, existing: set, dry_run: bool = False, max_per_group: int = 50) -> int:
    """同 source_id 的知识点两两建边。

    Args:
        max_per_group: 每组最多取前N个KPs建边，防止单source组合爆炸。
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, source_ids FROM knowledge_tree
        WHERE node_type = 'knowledge_point'
          AND source_ids IS NOT NULL
          AND array_length(source_ids, 1) > 0
        ORDER BY id
        """
    )
    # 按 source_id 分组
    source_groups: dict[int, list[int]] = {}
    for row in cur.fetchall():
        kp_id = int(row[0])
        sources = row[1] or []
        for src in sources:
            if src is not None:
                source_groups.setdefault(int(src), []).append(kp_id)

    edge_count = 0
    for src_id, kp_ids in source_groups.items():
        if len(kp_ids) < 2:
            continue
        # 限制每组建边数：取前 max_per_group 个 KPs
        limited = kp_ids[:max_per_group]
        for a, b in combinations(limited, 2):
            if (a, b) not in existing:
                if not dry_run:
                    upsert_edge(cur, a, b)
                edge_count += 1
                existing.add((a, b))
                existing.add((b, a))
        if len(kp_ids) > max_per_group:
            logger.debug("source %d: %d KPs → 截断到 %d (%d 边)", src_id, len(kp_ids), max_per_group, len(limited) * (len(limited) - 1) // 2)

    if not dry_run:
        conn.commit()
    return edge_count


# ── 策略 2: 跨 subject 向量桥接 ──


def build_vector_edges(conn, existing: set, threshold: float = 0.85, dry_run: bool = False) -> int:
    """跨 subject 的 k_vector cosine > threshold 建边。

    每科的 k_vector 取 centroid，跨科 cosine 匹配，
    高于阈值的从科级降级到知识点级做精确匹配。
    """
    cur = conn.cursor()

    # 1. 按 subject 计算 k_vector centroid（逐个维度均值）
    # pgvector 的 avg 不能直接对 vector[] 做，改用 Python 侧计算
    cur.execute(
        """
        SELECT parent_id, count(*) as cnt
        FROM knowledge_tree
        WHERE node_type = 'knowledge_point' AND k_vector IS NOT NULL
        GROUP BY parent_id
        HAVING count(*) >= 3
        """
    )
    subjects_with_kps = [(int(r[0]), int(r[1])) for r in cur.fetchall()]
    logger.info("参与向量桥接的 subject: %d", len(subjects_with_kps))

    # 批量取所有 KPs 的向量（pgvector 输出为 text 再 Python 解析）
    cur.execute(
        "SELECT id, parent_id, k_vector::text FROM knowledge_tree WHERE node_type='knowledge_point' AND k_vector IS NOT NULL"
    )
    kp_data: list[dict[str, Any]] = []
    for row in cur.fetchall():
        vec_text = row[2]
        if not vec_text:
            continue
        # pgvector::text 输出格式: [0.1,0.2,0.3,...]
        vec_text = vec_text.strip("[]")
        parts = vec_text.split(",")
        try:
            vec = [float(p) for p in parts if p.strip()]
        except ValueError:
            continue
        kp_data.append({
            "id": int(row[0]),
            "parent_id": int(row[1]) if row[1] is not None else None,
            "vector": vec,
        })
    logger.info("带 k_vector 的 KPs: %d", len(kp_data))

    # 按 parent_id 分组
    kp_by_subject: dict[int, list[dict[str, Any]]] = {}
    for kp in kp_data:
        pid = kp["parent_id"]
        if pid is not None:
            kp_by_subject.setdefault(pid, []).append(kp)

    # 计算每个 subject 的 centroid
    subject_centroids: dict[int, list[float]] = {}
    for pid, kps in kp_by_subject.items():
        if len(kps) < 3:
            continue
        dim = len(kps[0]["vector"])
        centroid = [0.0] * dim
        for kp in kps:
            for d in range(dim):
                centroid[d] += kp["vector"][d]
        centroid = [c / len(kps) for c in centroid]
        subject_centroids[pid] = centroid

    # 2. 逐对检查跨 subject centroid 相似度
    edge_count = 0
    checked = 0
    pid_list = list(subject_centroids.keys())
    for i in range(len(pid_list)):
        for j in range(i + 1, len(pid_list)):
            pid_a, pid_b = pid_list[i], pid_list[j]
            checked += 1
            if checked % 1000 == 0:
                logger.info("向量桥接进度: %d 对 checked, %d 边", checked, edge_count)
                conn.commit()

            ca = subject_centroids[pid_a]
            cb = subject_centroids[pid_b]
            # centroid cosine
            dot_c = sum(x * y for x, y in zip(ca, cb))
            nc_a = math.sqrt(sum(x * x for x in ca)) or 1.0
            nc_b = math.sqrt(sum(x * x for x in cb)) or 1.0
            centroid_sim = dot_c / (nc_a * nc_b)
            if centroid_sim < 0.80:
                continue  # centroid 都不接近，没必要逐 KP 比较

            # centroid 接近 → 采样精确匹配
            sample_a = kp_by_subject.get(pid_a, [])[:10]
            sample_b = kp_by_subject.get(pid_b, [])[:10]
            for ka_info in sample_a:
                va = ka_info["vector"]
                for kb_info in sample_b:
                    ka_id, kb_id = ka_info["id"], kb_info["id"]
                    if (ka_id, kb_id) in existing:
                        continue
                    vb = kb_info["vector"]
                    dot = sum(x * y for x, y in zip(va, vb))
                    na = math.sqrt(sum(x * x for x in va)) or 1.0
                    nb = math.sqrt(sum(x * x for x in vb)) or 1.0
                    sim = dot / (na * nb)
                    if sim > threshold:
                        if not dry_run:
                            upsert_edge(cur, ka_id, kb_id, count=max(1, int(sim * 10)))
                        edge_count += 1
                        existing.add((ka_id, kb_id))
                        existing.add((kb_id, ka_id))

    if not dry_run:
        conn.commit()
    return edge_count


# ── 策略 3: 同科高相似度 ──


def build_same_subject_edges(conn, existing: set, threshold: float = 0.95, dry_run: bool = False) -> int:
    """同一 subject 下 cosine > threshold 的 KPs 建边。"""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, parent_id FROM knowledge_tree
        WHERE node_type = 'knowledge_point' AND k_vector IS NOT NULL
        ORDER BY parent_id, id
        """
    )
    kp_by_subject: dict[int, list[int]] = {}
    all_kps: dict[int, list[float]] = {}
    for row in cur.fetchall():
        kp_id = int(row[0])
        pid = int(row[1]) if row[1] is not None else None
        if pid is not None:
            kp_by_subject.setdefault(pid, []).append(kp_id)

    # 批量查 k_vector
    cur.execute(
        "SELECT id, k_vector::text FROM knowledge_tree WHERE node_type='knowledge_point' AND k_vector IS NOT NULL"
    )
    all_kps: dict[int, list[float]] = {}
    for row in cur.fetchall():
        kp_id = int(row[0])
        vec_text = row[1]
        if not vec_text:
            continue
        vec_text = vec_text.strip("[]")
        parts = vec_text.split(",")
        try:
            all_kps[kp_id] = [float(p) for p in parts if p.strip()]
        except ValueError:
            continue

    edge_count = 0

    for pid, kp_ids in kp_by_subject.items():
        if len(kp_ids) < 2:
            continue
        for i, ka in enumerate(kp_ids[:-1]):
            va = all_kps.get(ka)
            if va is None:
                continue
            for kb in kp_ids[i + 1:]:
                if (ka, kb) in existing:
                    continue
                vb = all_kps.get(kb)
                if vb is None:
                    continue
                dot = sum(x * y for x, y in zip(va, vb))
                na = math.sqrt(sum(x * x for x in va)) or 1.0
                nb = math.sqrt(sum(x * x for x in vb)) or 1.0
                sim = dot / (na * nb)
                if sim > threshold:
                    if not dry_run:
                        upsert_edge(cur, ka, kb, count=max(1, int(sim * 10)))
                    edge_count += 1
                    existing.add((ka, kb))
                    existing.add((kb, ka))

    if not dry_run:
        conn.commit()
    return edge_count


def main():
    parser = argparse.ArgumentParser(
        description="填充 knowledge_tree_edges 关联边（已废弃，建议使用 consolidation.py）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计和预览，不实际写入数据库"
    )
    parser.add_argument(
        "--threshold-vector",
        type=float,
        default=0.85,
        help="跨 subject 向量桥接的 cosine 相似度阈值，超过此值才建边（默认: 0.85）"
    )
    parser.add_argument(
        "--threshold-same",
        type=float,
        default=0.95,
        help="同科目下知识点的 cosine 相似度阈值，超过此值才建边（默认: 0.95）"
    )
    parser.add_argument(
        "--only-source",
        action="store_true",
        help="仅执行同源共现策略，跳过向量桥接和同科高相似策略"
    )
    args = parser.parse_args()

    conn = get_conn()
    existing = existing_edge_pairs(conn)
    logger.info("已有边: %d", len(existing) // 2)

    total_edges = 0

    # 策略 1: 同源共现
    t0 = time.time()
    n = build_source_edges(conn, existing, dry_run=args.dry_run)
    total_edges += n
    logger.info("同源共现: %d 边 (%d KPs), 耗时 %.1fs", n, len(existing) // 2, time.time() - t0)

    if not args.only_source:
        # 策略 2: 跨 subject 向量桥接
        t0 = time.time()
        n = build_vector_edges(conn, existing, threshold=args.threshold_vector, dry_run=args.dry_run)
        total_edges += n
        logger.info("向量桥接: %d 边, 耗时 %.1fs", n, time.time() - t0)

        # 策略 3: 同科高相似度
        t0 = time.time()
        n = build_same_subject_edges(conn, existing, threshold=args.threshold_same, dry_run=args.dry_run)
        total_edges += n
        logger.info("同科高相似: %d 边, 耗时 %.1fs", n, time.time() - t0)

    logger.info("总计: %d 新边 (dry_run=%s)", total_edges, args.dry_run)
    conn.close()


if __name__ == "__main__":
    main()