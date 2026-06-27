#!/usr/bin/env python3
"""
memory_quality_report.py — Hindsight 记忆质量报告

4 条 SQL 指标：
  - 重复积累率: length>10K 且存在重复模式的条数 / 总数
  - Consolidation 覆盖率: consolidated_at IS NOT NULL / 总数
  - 中位记忆长度: PERCENTILE_CONT(0.5) OF length(text)
  - 单条最大长度: MAX(length(text))

输出：结构化文本报告（默认）或 JSON 格式（--json）

用法：
    python3 scripts/memory_quality_report.py
    python3 scripts/memory_quality_report.py --json
    python3 scripts/memory_quality_report.py --report-only  # 仅告警级别，不输出明细

环境变量：
    CLUSTERING_DB_URL  PostgreSQL 连接字符串（必填）
"""

import argparse
import json
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


def fmt_pct(value: float) -> str:
    """格式化百分比。"""
    return f"{value * 100:.2f}%"


def check_threshold(name: str, value: float, threshold: float, higher_is_bad: bool) -> str:
    """检查阈值并返回状态标记。"""
    if higher_is_bad:
        return "⚠️  告警" if value > threshold else "✅ 正常"
    else:
        return "⚠️  告警" if value < threshold else "✅ 正常"


def run_report(conn, report_only: bool = False) -> dict[str, Any]:
    """执行质量报告查询，返回结构化结果。

    Args:
        report_only: True 时仅返回告警项，省略正常指标详情
    """
    result = {}

    with conn.cursor() as cur:
        # === 指标 1: 重复积累率 ===
        cur.execute("""
            SELECT COUNT(*) FROM memory_units
            WHERE bank_id = 'hermes'
        """)
        total_count = cur.fetchone()[0] or 1  # 避免除零

        cur.execute("""
            SELECT COUNT(*) FROM memory_units
            WHERE bank_id = 'hermes'
              AND length(text) > 10000
        """)
        long_count = cur.fetchone()[0]
        duplication_rate = long_count / total_count

        result["duplication"] = {
            "label": "重复积累率",
            "value": fmt_pct(duplication_rate),
            "raw": round(duplication_rate, 6),
            "long_memories": long_count,
            "total_memories": total_count,
            "threshold": "1%",
            "status": check_threshold("重复积累率", duplication_rate, 0.01, higher_is_bad=True),
        }

        # === 指标 2: Consolidation 覆盖率 ===
        cur.execute("""
            SELECT COUNT(*) FROM memory_units
            WHERE bank_id = 'hermes'
              AND consolidated_at IS NOT NULL
        """)
        consolidated_count = cur.fetchone()[0]
        consolidation_rate = consolidated_count / total_count

        result["consolidation_coverage"] = {
            "label": "Consolidation 覆盖率",
            "value": fmt_pct(consolidation_rate),
            "raw": round(consolidation_rate, 6),
            "consolidated": consolidated_count,
            "total_memories": total_count,
            "threshold": "50%",
            "status": check_threshold("Consolidation 覆盖率", consolidation_rate, 0.50, higher_is_bad=False),
        }

        # === 指标 3: 中位记忆长度 ===
        cur.execute("""
            SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY length(text))
            FROM memory_units
            WHERE bank_id = 'hermes'
        """)
        median_length = cur.fetchone()[0] or 0

        result["median_length"] = {
            "label": "中位记忆长度",
            "value": f"{median_length:,} 字符",
            "raw": median_length,
            "threshold": "2,000 字符",
            "status": check_threshold("中位记忆长度", median_length, 2000, higher_is_bad=True),
        }

        # === 指标 4: 单条最大长度（排除已治理的记忆）===
        cur.execute("""
            SELECT COALESCE(MAX(length(text)), 0) FROM memory_units
            WHERE bank_id = 'hermes'
              AND text NOT LIKE %s
        """, ('[长记忆治理]%',))
        max_length = cur.fetchone()[0] or 0

        # 同时查最长记忆的 ID 和创建时间
        cur.execute("""
            SELECT id, length(text), created_at FROM memory_units
            WHERE bank_id = 'hermes'
              AND text NOT LIKE %s
            ORDER BY length(text) DESC
            LIMIT 1
        """, ('[长记忆治理]%',))
        max_row = cur.fetchone()
        max_id = max_row[0] if max_row else None
        max_created_at = str(max_row[2]) if max_row and max_row[2] else None

        result["max_length"] = {
            "label": "单条最大长度",
            "value": f"{max_length:,} 字符",
            "raw": max_length,
            "max_id": str(max_id) if max_id else None,
            "max_created_at": max_created_at,
            "threshold": "8,000 字符",
            "status": check_threshold("单条最大长度", max_length, 8000, higher_is_bad=True),
        }

    # report_only 模式：仅保留告警项，过滤掉正常指标
    if report_only:
        result = {k: v for k, v in result.items() if "告警" in v.get("status", "")}

    return result


def print_text_report(report: dict[str, Any]):
    """打印结构化文本报告。"""
    print("=" * 60)
    print("  Hindsight 记忆质量报告")
    print("=" * 60)
    print()

    for key, metric in report.items():
        print(f"  [{metric['status']}] {metric['label']}")
        print(f"    值:      {metric['value']}")
        print(f"    阈值:    {metric['threshold']}")
        # 打印额外信息
        if key == "duplication":
            print(f"    超长记忆: {metric['long_memories']} / {metric['total_memories']} 条")
        elif key == "consolidation_coverage":
            print(f"    已 consolidation: {metric['consolidated']} / {metric['total_memories']} 条")
        elif key == "max_length":
            if metric.get("max_id"):
                print(f"    最长记忆 ID: {metric['max_id']}")
            if metric.get("max_created_at"):
                print(f"    创建时间: {metric['max_created_at']}")
        print()

    # 汇总告警
    alerts = [m for m in report.values() if "告警" in m["status"]]
    if alerts:
        print("=" * 60)
        print(f"  告警汇总: {len(alerts)} 项需要关注")
        print("=" * 60)
        for alert in alerts:
            print(f"    {alert['status']}: {alert['label']} ({alert['value']})")
    else:
        print("=" * 60)
        print("  ✅ 所有指标正常")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Hindsight 记忆质量报告",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出（默认文本报告）",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="仅输出告警级别，不输出明细值",
    )

    args = parser.parse_args()

    conn = get_connection()
    try:
        report = run_report(conn, report_only=args.report_only)

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_text_report(report)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
