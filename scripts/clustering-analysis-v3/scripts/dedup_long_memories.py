#!/usr/bin/env python3
"""
dedup_long_memories.py — Hindsight 超长记忆清理（前缀重复检测 + Consolidation 覆盖清除）

用于清理 >10K 的重复积累记忆，分两阶段执行：
  Phase 1: >50K 记录（约 14 条）
  Phase 2: 10K-50K 记录（约 156 条）

用法：
    # 预览（默认 dry-run）
    python3 scripts/dedup_long_memories.py
    # 实际执行
    python3 scripts/dedup_long_memories.py --apply

环境变量：
    CLUSTERING_DB_URL  PostgreSQL 连接字符串（必填）
"""

import argparse
import os
import sys
from typing import Any


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


def fetch_long_memories(conn, min_length: int, cutoff_date: str = "2026-06-01") -> list[dict[str, Any]]:
    """查询超长记忆记录。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, text, created_at, consolidated_at, source_memory_ids
            FROM memory_units
            WHERE bank_id = 'hermes'
              AND length(text) > %s
              AND created_at < %s
            ORDER BY length(text) DESC
            """,
            (min_length, cutoff_date),
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        result.append({
            "id": row[0],
            "text": row[1] or "",
            "created_at": row[2],
            "consolidated_at": row[3],
            "source_memory_ids": row[4] or [],
        })
    return result


def detect_prefix_repeat(text: str, prefix_len: int = 300, repeat_threshold: int = 3) -> str | None:
    """检测前 prefix_len 字符在全文中的重复次数。

    如果重复次数 >= repeat_threshold，返回截断后的唯一版本（仅保留首次出现）。
    否则返回 None。
    """
    if len(text) <= prefix_len:
        return None
    prefix = text[:prefix_len]
    # 统计 prefix 在全文中的出现次数
    count = text.count(prefix)
    if count >= repeat_threshold:
        # 找到第二次出现的位置
        second_start = text.find(prefix, prefix_len)
        if second_start != -1:
            # 仅保留到第二次出现之前
            truncated = text[:second_start].rstrip()
            return truncated
    return None


def is_consolidation_covered(conn, memory_id: str, text: str) -> bool:
    """判断记忆是否被 consolidation 完全覆盖。

    查询引用此 memory_id 的父记忆（该父记忆的 source_memory_ids 包含此 memory_id），
    如果父记忆的 text 完全包含当前 text，则返回 True。
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT text FROM memory_units
            WHERE source_memory_ids @> ARRAY[%s::uuid]
            LIMIT 1
            """,
            (memory_id,),
        )
        row = cur.fetchone()
    if row is None:
        return False
    parent_text = row[0] or ""
    # 规范化空白比较
    text_norm = " ".join(text.split())
    pt_norm = " ".join(parent_text.split())
    # 如果当前文本是父文本的子串，说明被完全覆盖
    if text_norm in pt_norm:
        return True
    # Jaccard 相似度 > 0.9 也视为覆盖
    set1 = set(text_norm.split())
    set2 = set(pt_norm.split())
    if not set1:
        return False
    jaccard = len(set1 & set2) / len(set1 | set2)
    return jaccard > 0.9


def cascade_delete(conn, memory_id: str, dry_run: bool) -> dict[str, int]:
    """级联删除关联表记录。返回各表删除行数。"""
    counts = {"unit_entities": 0, "memory_links": 0}

    tables = [
        ("unit_entities", "unit_id"),
        ("memory_links", "from_unit_id"),
        ("memory_links", "to_unit_id"),
    ]

    for table, column in tables:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = %s", (memory_id,))
            cnt = cur.fetchone()[0]
            if cnt > 0 and not dry_run:
                cur.execute(f"DELETE FROM {table} WHERE {column} = %s", (memory_id,))
            if cnt > 0:
                if table == "unit_entities":
                    counts["unit_entities"] += cnt
                elif table == "memory_links":
                    counts["memory_links"] += cnt
                print(f"    [{'DRY-RUN' if dry_run else '删除'}] {table}.{column} = {memory_id}: {cnt} 行")

    return counts


def phase_process(conn, memories: list[dict[str, Any]], phase_name: str, dry_run: bool) -> dict[str, Any]:
    """处理一个阶段（Phase 1 或 Phase 2）。返回统计摘要。"""
    stats = {
        "phase": phase_name,
        "total": len(memories),
        "prefix_truncated": 0,
        "consolidation_deleted": 0,
        "errors": 0,
    }

    print(f"\n{'='*60}")
    print(f"Phase: {phase_name} ({len(memories)} 条)")
    print(f"{'='*60}")

    for i, mem in enumerate(memories):
        mem_id = mem["id"]
        text = mem["text"]
        text_len = len(text)
        print(f"\n[{i+1}/{len(memories)}] ID: {mem_id} (长度: {text_len})")

        # 使用 savepoint 隔离每条记录的写操作，避免部分失败导致全量回滚
        sp_name = f"sp_{i}"
        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(f"SAVEPOINT {sp_name}")

        try:
            # --- 检测 1: Consolidation 覆盖 ---
            if mem["consolidated_at"] and is_consolidation_covered(conn, mem_id, text):
                print(f"  ⚡ Consolidation 完全覆盖，准备删除")
                if dry_run:
                    print(f"  [DRY-RUN] 将删除整条记忆 + 级联关联表")
                else:
                    cascade_delete(conn, mem_id, dry_run=False)
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM memory_units WHERE id = %s", (mem_id,))
                    with conn.cursor() as cur:
                        cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                    print(f"  ✅ 已删除记忆 + 级联关联表")
                stats["consolidation_deleted"] += 1
                continue

            # --- 检测 2: 前缀重复 ---
            truncated = detect_prefix_repeat(text)
            if truncated:
                new_len = len(truncated)
                print(f"  ⚡ 前缀重复检测命中 (前300字重复≥3次)")
                print(f"    原始长度: {text_len} → 截断后: {new_len} (减少 {text_len - new_len} 字符)")
                if dry_run:
                    print(f"  [DRY-RUN] 将截断文本")
                else:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE memory_units SET text = %s WHERE id = %s",
                            (truncated, mem_id),
                        )
                    with conn.cursor() as cur:
                        cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                    print(f"  ✅ 已截断文本")
                stats["prefix_truncated"] += 1
            else:
                print(f"  ✅ 未发现问题，跳过")
                if not dry_run:
                    with conn.cursor() as cur:
                        cur.execute(f"RELEASE SAVEPOINT {sp_name}")

        except Exception as e:
            print(f"  ❌ 错误: {e}", file=sys.stderr)
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            stats["errors"] += 1

    if not dry_run:
        conn.commit()

    return stats


def print_summary(all_stats: list[dict[str, Any]], dry_run: bool):
    """打印最终统计摘要。"""
    print(f"\n{'='*60}")
    mode = "DRY-RUN (预览)" if dry_run else "APPLY (已执行)"
    print(f"  摘要 — {mode}")
    print(f"{'='*60}")

    total = sum(s["total"] for s in all_stats)
    prefix_truncated = sum(s["prefix_truncated"] for s in all_stats)
    consolidation_deleted = sum(s["consolidation_deleted"] for s in all_stats)
    errors = sum(s["errors"] for s in all_stats)

    for s in all_stats:
        print(f"  {s['phase']}: 总计 {s['total']}, "
              f"前缀截断 {s['prefix_truncated']}, "
              f"覆盖删除 {s['consolidation_deleted']}, "
              f"错误 {s['errors']}")

    print(f"  {'='*40}")
    print(f"  合计: {total} 条处理, "
          f"前缀截断 {prefix_truncated}, "
          f"覆盖删除 {consolidation_deleted}, "
          f"错误 {errors}")

    if not dry_run:
        print(f"\n  💾 已提交到数据库")
    else:
        print(f"\n  👆 以上为预览，加 --apply 执行")


def main():
    parser = argparse.ArgumentParser(
        description="Hindsight 超长记忆清理（前缀重复检测 + Consolidation 覆盖清除）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入数据库（默认仅 dry-run 预览）",
    )
    parser.add_argument(
        "--cutoff-date",
        default="2026-06-01",
        help="只处理 created_at 早于此日期的记录（默认 2026-06-01）",
    )
    args = parser.parse_args()

    dry_run = not args.apply

    conn = get_connection()
    try:
        # Phase 1: >50K
        print("正在查询 Phase 1 (>50K)...")
        phase1_memories = fetch_long_memories(conn, min_length=50000, cutoff_date=args.cutoff_date)
        print(f"  找到 {len(phase1_memories)} 条")

        # Phase 2: 10K-50K
        print("正在查询 Phase 2 (10K-50K)...")
        all_10k = fetch_long_memories(conn, min_length=10000, cutoff_date=args.cutoff_date)
        # 排除已在 Phase 1 中的
        phase1_ids = {m["id"] for m in phase1_memories}
        phase2_memories = [m for m in all_10k if m["id"] not in phase1_ids]
        print(f"  找到 {len(phase2_memories)} 条")

        all_stats = []

        if phase1_memories:
            stats1 = phase_process(conn, phase1_memories, "Phase 1 (>50K)", dry_run)
            all_stats.append(stats1)

        if phase2_memories:
            stats2 = phase_process(conn, phase2_memories, "Phase 2 (10K-50K)", dry_run)
            all_stats.append(stats2)

        if not phase1_memories and not phase2_memories:
            print("\n✅ 没有需要处理的超长记忆")
        else:
            print_summary(all_stats, dry_run)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
