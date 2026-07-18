#!/usr/bin/env python3
"""long_memory_governance.py — 归档并压缩 Hindsight 超长记忆。

默认只处理 P0 超长记录（>50,000 字符）：
1. 将原文 JSONL 归档到 /root/.hermes/archives/hindsight-long-memories/YYYYMMDD.jsonl
2. 用可检索的压缩文本替换 memory_units.text（保留头部/尾部摘要 + 原始 hash/归档路径）
3. 重新计算并更新 embedding

设计目标：修复数据质量，不直接删除唯一记忆；让质量报告中的最大单条长度告警形成闭环。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 允许脚本直接从源码树或部署树运行
PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from clustering_analysis.adapters.database import DatabaseAdapter
from clustering_analysis.cli import _load_embedding_config
from clustering_analysis.config import load_config
from clustering_analysis.core.embeddings import batch_embed

DEFAULT_THRESHOLD = int(os.environ.get("LONG_MEMORY_THRESHOLD", "50000"))
DEFAULT_LIMIT = int(os.environ.get("LONG_MEMORY_LIMIT", "20"))
DEFAULT_ARCHIVE_DIR = os.environ.get(
    "LONG_MEMORY_ARCHIVE_DIR",
    "/root/.hermes/archives/hindsight-long-memories",
)
DEFAULT_HEAD_CHARS = int(os.environ.get("LONG_MEMORY_HEAD_CHARS", "2600"))
DEFAULT_TAIL_CHARS = int(os.environ.get("LONG_MEMORY_TAIL_CHARS", "1000"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_replacement(
    *,
    original_text: str,
    original_len: int,
    original_hash: str,
    archive_path: str,
    head_chars: int,
    tail_chars: int,
) -> str:
    head = original_text[:head_chars].strip()
    tail = original_text[-tail_chars:].strip() if tail_chars > 0 else ""
    parts = [
        "[长记忆治理]",
        f"原始长度: {original_len} 字符",
        f"原始SHA256: {original_hash}",
        f"归档路径: {archive_path}",
        f"治理时间: {_now_iso()}",
        "说明: 原文过长，已归档；当前文本保留首尾关键信息用于检索，避免 embedding/rerank 超限。",
        "",
        "[原文开头摘要]",
        head,
    ]
    if tail and tail != head:
        parts.extend(["", "[原文结尾摘要]", tail])
    return "\n".join(parts).strip()


def _connect() -> DatabaseAdapter:
    db_url = os.environ.get("CLUSTERING_DB_URL")
    if not db_url:
        raise SystemExit("CLUSTERING_DB_URL is required")
    return DatabaseAdapter(db_url)


def fetch_long_memories(adapter: DatabaseAdapter, threshold: int, limit: int) -> list[tuple[Any, str, str, int, Any]]:
    with adapter.conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, bank_id, text, length(text) AS text_len, created_at
            FROM memory_units
            WHERE text IS NOT NULL
              AND length(text) > %s
              AND text NOT LIKE '[长记忆治理]%%'
            ORDER BY length(text) DESC
            LIMIT %s
            """,
            (threshold, limit),
        )
        return cur.fetchall()


def archive_rows(rows: list[tuple[Any, str, str, int, Any]], archive_dir: Path) -> Path | None:
    if not rows:
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{datetime.now().strftime('%Y%m%d')}.jsonl"
    with archive_path.open("a", encoding="utf-8") as f:
        for unit_id, bank_id, text, text_len, created_at in rows:
            record = {
                "archived_at": _now_iso(),
                "id": str(unit_id),
                "bank_id": bank_id,
                "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
                "original_len": int(text_len),
                "sha256": _sha256(text),
                "text": text,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return archive_path


def update_rows(
    adapter: DatabaseAdapter,
    rows: list[tuple[Any, str, str, int, Any]],
    archive_path: Path,
    *,
    head_chars: int,
    tail_chars: int,
    config_path: str,
) -> int:
    replacements: list[tuple[str, str]] = []
    for unit_id, _bank_id, text, text_len, _created_at in rows:
        replacement = _make_replacement(
            original_text=text,
            original_len=int(text_len),
            original_hash=_sha256(text),
            archive_path=str(archive_path),
            head_chars=head_chars,
            tail_chars=tail_chars,
        )
        replacements.append((replacement, str(unit_id)))

    if not replacements:
        return 0

    with adapter.conn.cursor() as cur:
        import psycopg2.extras

        psycopg2.extras.execute_values(
            cur,
            "UPDATE memory_units AS mu SET text = v.text FROM (VALUES %s) AS v(text, id) WHERE mu.id = v.id::uuid",
            replacements,
            template="(%s, %s)",
        )
        updated_text = cur.rowcount
    adapter.conn.commit()

    # 重新计算压缩后文本 embedding
    config = load_config(config_path)
    embed_base_url, embed_model, embed_key = _load_embedding_config(config)
    texts = [item[0] for item in replacements]
    ids = [item[1] for item in replacements]
    embeddings = batch_embed(texts, base_url=embed_base_url, model=embed_model, api_key=embed_key)
    if embeddings:
        adapter.batch_update_embeddings([(emb, uid) for uid, emb in zip(ids, embeddings)])
    else:
        print("  ⚠️  embedding 更新失败：batch_embed returned None", file=sys.stderr)

    return updated_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive and compact overlong Hindsight memory_units")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD, help="处理阈值，默认 50000 字符")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="单次最多处理条数，默认 20")
    parser.add_argument("--archive-dir", default=DEFAULT_ARCHIVE_DIR)
    parser.add_argument("--head-chars", type=int, default=DEFAULT_HEAD_CHARS)
    parser.add_argument("--tail-chars", type=int, default=DEFAULT_TAIL_CHARS)
    parser.add_argument("--config", default=str(PROJECT_DIR / "config" / "default.yaml"))
    parser.add_argument("--apply", action="store_true", help="实际归档并替换；默认 dry-run")
    args = parser.parse_args()

    adapter = _connect()
    try:
        rows = fetch_long_memories(adapter, args.threshold, args.limit)
        print(f"  阈值: >{args.threshold} 字符，候选: {len(rows)} 条（limit={args.limit}）")
        if rows:
            for unit_id, _bank_id, _text, text_len, created_at in rows[:10]:
                print(f"  - {str(unit_id)[:8]}... length={text_len} created_at={created_at}")
        if not rows:
            print("  ✅ 无需处理")
            return 0
        if not args.apply:
            print("  DRY-RUN：未修改数据库；加 --apply 执行归档+压缩")
            return 0
        archive_path = archive_rows(rows, Path(args.archive_dir))
        if archive_path is None:
            raise RuntimeError("archive_rows returned None unexpectedly")
        updated = update_rows(
            adapter,
            rows,
            archive_path,
            head_chars=args.head_chars,
            tail_chars=args.tail_chars,
            config_path=args.config,
        )
        print(f"  ✅ 已归档: {len(rows)} 条 → {archive_path}")
        print(f"  ✅ 已压缩替换: {updated} 条")
        return 0
    finally:
        adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
