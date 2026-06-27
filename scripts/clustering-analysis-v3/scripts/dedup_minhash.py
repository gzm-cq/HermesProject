#!/usr/bin/env python3
"""
dedup_minhash.py — Hindsight MinHash LSH 跨条目去重

检测数据库中 Jaccard 相似度 > 0.8 的记忆对，标记或删除重复项。

依赖：
    pip install datasketch    # 可选；未安装时自动降级为 Jaccard 比较

用法：
    # dry-run 预览（默认）
    python3 scripts/dedup_minhash.py
    python3 scripts/dedup_minhash.py --threshold 0.85
    python3 scripts/dedup_minhash.py --limit 5000
    # 实际标记
    python3 scripts/dedup_minhash.py --apply

环境变量：
    CLUSTERING_DB_URL  PostgreSQL 连接字符串（必填）
"""

import argparse
import os
import sys
import time
from typing import Any

# ===== 可选依赖：datasketch =====
HAS_DATASKETCH = False
try:
    from datasketch import MinHash, MinHashLSH
    HAS_DATASKETCH = True
except ImportError:
    pass


def get_connection():
    """从 CLUSTERING_DB_URL 环境变量获取数据库连接。"""
    db_url = os.environ.get("CLUSTERING_DB_URL")
    if not db_url:
        print("错误: 未设置 CLUSTERING_DB_URL 环境变量", file=sys.stderr)
        sys.exit(1)
    try:
        import psycopg2
        return psycopg2.connect(db_url)
    except ImportError:
        print("错误: 需要 psycopg2 库", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: 无法连接数据库 — {e}", file=sys.stderr)
        sys.exit(1)


def fetch_memories(conn, limit: int | None = None) -> list[dict[str, Any]]:
    """获取 hermes bank 中所有待检测的记忆。"""
    with conn.cursor() as cur:
        query = """
            SELECT id, text, created_at
            FROM memory_units
            WHERE bank_id = 'hermes'
            ORDER BY created_at DESC
        """
        if limit:
            query += f" LIMIT {limit}"
        cur.execute(query)
        rows = cur.fetchall()

    result = []
    for row in rows:
        text = row[1] or ""
        if len(text.split()) < 5:  # 跳过过短记录
            continue
        result.append({
            "id": str(row[0]),
            "text": text,
            "created_at": row[2],
        })
    return result


def generate_shingles(text: str, k: int = 2) -> set[str]:
    """生成 k-gram shingle 集合。"""
    words = text.split()
    if len(words) < k:
        return {text}
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def jaccard_similarity(set1: set, set2: set) -> float:
    """计算 Jaccard 相似度。"""
    union = len(set1 | set2)
    if union == 0:
        return 0.0
    return len(set1 & set2) / union


def find_duplicates_jaccard(
    memories: list[dict[str, Any]],
    threshold: float = 0.8,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """使用 Jaccard 比较（降级方案）查找相似对。"""
    pairs = []
    shingle_cache: dict[str, set] = {}

    n = len(memories)
    for i in range(n):
        m1 = memories[i]
        if m1["id"] not in shingle_cache:
            shingle_cache[m1["id"]] = generate_shingles(m1["text"])
        s1 = shingle_cache[m1["id"]]

        for j in range(i + 1, n):
            m2 = memories[j]
            if m2["id"] not in shingle_cache:
                shingle_cache[m2["id"]] = generate_shingles(m2["text"])
            s2 = shingle_cache[m2["id"]]

            sim = jaccard_similarity(s1, s2)
            if sim >= threshold:
                pairs.append((m1, m2, sim))

    return pairs


def find_duplicates_minhash(
    memories: list[dict[str, Any]],
    threshold: float = 0.8,
    num_perm: int = 128,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """使用 MinHash LSH（datasketch）查找相似对。"""
    # 构建 LSH index
    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    minhash_map: dict[str, tuple[MinHash, dict[str, Any]]] = {}

    print(f"  计算 {len(memories)} 条记忆的 MinHash signatures...")
    for i, mem in enumerate(memories):
        if (i + 1) % 1000 == 0:
            print(f"    进度: {i + 1}/{len(memories)}")
        mh = MinHash(num_perm=num_perm)
        shingles = generate_shingles(mem["text"])
        for s in shingles:
            mh.update(s.encode("utf-8"))
        minhash_map[mem["id"]] = (mh, mem)
        lsh.insert(mem["id"], mh)

    # 查找候选对
    print(f"  LSH 分桶查找中...")
    seen_pairs: set[tuple[str, str]] = set()
    pairs = []

    for mem_id, (mh, mem) in minhash_map.items():
        candidates = lsh.query(mh)
        for cand_id in candidates:
            if cand_id == mem_id:
                continue
            # 确保每对只处理一次
            pair_key = tuple(sorted([mem_id, cand_id]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            cand_mh, cand_mem = minhash_map[cand_id]
            sim = mh.jaccard(cand_mh)
            if sim >= threshold:
                pairs.append((mem, cand_mem, sim))

    return pairs


def resolve_winner(pair: tuple[dict[str, Any], dict[str, Any], float]) -> tuple[dict[str, Any], dict[str, Any]]:
    """确定保留哪个（取最长+最新）。返回 (保留, 删除)。"""
    m1, m2, sim = pair
    # 优先保留长度更长的
    len1, len2 = len(m1["text"]), len(m2["text"])
    if len1 > len2:
        return m1, m2
    elif len2 > len1:
        return m2, m1
    # 等长则保留更新的
    if m1["created_at"] >= m2["created_at"]:
        return m1, m2
    return m2, m1


def delete_loser(conn, loser_id: str) -> bool:
    """删除输家记忆 + 级联清理关联表（memory_links, unit_entities）。"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM memory_links WHERE from_unit_id = %s OR to_unit_id = %s", (loser_id, loser_id))
        cur.execute("DELETE FROM unit_entities WHERE unit_id = %s", (loser_id,))
        cur.execute("DELETE FROM memory_units WHERE id = %s", (loser_id,))
        conn.commit()
        return cur.rowcount > 0


def print_similarity_distribution(pairs: list[tuple[dict[str, Any], dict[str, Any], float]]):
    """打印相似度分布。"""
    if not pairs:
        return

    buckets = {"0.80-0.85": 0, "0.85-0.90": 0, "0.90-0.95": 0, "0.95-1.00": 0}
    for _, _, sim in pairs:
        if sim < 0.85:
            buckets["0.80-0.85"] += 1
        elif sim < 0.90:
            buckets["0.85-0.90"] += 1
        elif sim < 0.95:
            buckets["0.90-0.95"] += 1
        else:
            buckets["0.95-1.00"] += 1

    print(f"\n  相似度分布:")
    for bucket, count in buckets.items():
        bar = "█" * (count * 40 // len(pairs) if count else 1) if count else ""
        print(f"    {bucket}: {count:>4} 条  {bar}")


def main():
    parser = argparse.ArgumentParser(
        description="Hindsight MinHash LSH 跨条目去重",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际删除重复（默认仅 dry-run 预览）",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="Jaccard 相似度阈值（默认 0.8）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制检测的记忆条数（默认全部）",
    )
    parser.add_argument(
        "--num-perm",
        type=int,
        default=128,
        help="MinHash permutation 数量（默认 128）",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    threshold = args.threshold
    num_perm = args.num_perm
    limit = args.limit

    # 打印检测模式
    if HAS_DATASKETCH:
        print(f"🔧 使用 MinHash LSH (num_perm={num_perm}, threshold={threshold})")
    else:
        print("⚠️  未安装 datasketch，降级为简单 Jaccard 比较")
        print("   (pip install datasketch 可提速 10-100x)")
    print(f"{'📋 DRY-RUN 模式' if dry_run else '💾 APPLY 模式'}")

    conn = get_connection()
    try:
        print(f"\n正在加载记忆...")
        memories = fetch_memories(conn, limit=limit)
        print(f"  加载 {len(memories)} 条有效记忆（bank=hermes）")

        t0 = time.time()

        if HAS_DATASKETCH:
            pairs = find_duplicates_minhash(memories, threshold=threshold, num_perm=num_perm)
        else:
            pairs = find_duplicates_jaccard(memories, threshold=threshold)

        elapsed = time.time() - t0
        print(f"\n  检测耗时: {elapsed:.1f} 秒")

        if not pairs:
            print(f"\n✅ 未发现相似度 >= {threshold} 的重复对")
            return

        # 排序：相似度从高到低
        pairs.sort(key=lambda x: x[2], reverse=True)

        print(f"\n{'='*60}")
        print(f"  发现 {len(pairs)} 对相似记忆 (threshold={threshold})")
        print(f"{'='*60}")

        print_similarity_distribution(pairs)

        print(f"\n  去重决策:")
        print(f"  {'保留 ID':<38} {'删除 ID':<38} {'相似度':<8}")
        print(f"  {'-'*38} {'-'*38} {'-'*8}")

        # 去重决策：确定保留/删除
        marked_for_deletion: dict[str, tuple[str, float]] = {}  # delete_id -> (keep_id, sim)

        for m1, m2, sim in pairs:
            keep, delete = resolve_winner((m1, m2, sim))
            keep_id_short = keep["id"][:8] + "..."
            delete_id_short = delete["id"][:8] + "..."
            print(f"  {keep_id_short:<38} {delete_id_short:<38} {sim:<8.4f}")
            if delete["id"] not in marked_for_deletion:
                marked_for_deletion[delete["id"]] = (keep["id"], sim)

        # 执行删除
        if marked_for_deletion:
            print(f"\n  (共 {len(pairs)} 对，确定删除 {len(marked_for_deletion)} 条唯一记忆)")

            if not dry_run:
                deleted_count = 0
                for delete_id, (keep_id, sim) in marked_for_deletion.items():
                    if delete_loser(conn, delete_id):
                        deleted_count += 1
                # 清理孤儿实体（unit_entities 被删后不再被引用的实体）
                with conn.cursor() as cur:
                    cur.execute("""
                        DELETE FROM entities e
                        WHERE e.bank_id = 'hermes'
                          AND NOT EXISTS (
                            SELECT 1 FROM unit_entities ue WHERE ue.entity_id = e.id
                          )
                    """)
                    orphan_count = cur.rowcount
                conn.commit()
                if orphan_count:
                    print(f"  🧹 已清理 {orphan_count} 个孤儿实体")
                print(f"  ✅ 已级联删除: {deleted_count} 条（含 memory_links + unit_entities + 孤儿实体）")
            else:
                print(f"  📋 [DRY-RUN] 预览完成，加 --apply 执行删除")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
