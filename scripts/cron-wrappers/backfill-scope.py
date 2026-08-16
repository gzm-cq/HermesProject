#!/usr/bin/env python3
"""backfill-scope.py — 存量 memory_units entity 挂靠 backfill。

对无实体关联的记忆，用 LLM 提取具体技术/工具/框架名，
然后用 Python 端 trigram 模糊匹配已有 entities 表，写入 unit_entities。

用法：
    python3 backfill-scope.py --dry-run  # 预览
    python3 backfill-scope.py --apply    # 写入 DB
"""
from __future__ import annotations

import argparse, json, os, re, sys, time, uuid as uuid_mod
from typing import Any

import httpx, numpy as np

# ── Config ──
DB_URL = os.environ.get("HINDSIGHT_DB_URL",
                         "postgresql://postgres:postgres@127.0.0.1:5434/hindsight")
LLM_API_URL = os.environ.get("LLM_API_URL", "http://127.0.0.1:4142/v1/chat/completions")
LLM_API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("LITELLM_MASTER_KEY", ""))
LLM_MODEL = os.environ.get("LLM_MODEL", "s-deepseek-v4-flash")
BATCH_SIZE = 20
MAX_RETRIES = 3
RETRY_INTERVAL_SEC = 3
WRITE_BATCH_SIZE = 500

# ── Helpers ──

def _parse_pg_vector(val: Any) -> np.ndarray | None:
    """Parse PostgreSQL vector value to numpy array."""
    if val is None: return None
    if isinstance(val, (list, tuple, np.ndarray)):
        return np.array(val, dtype=np.float32)
    if isinstance(val, str):
        s = val.strip().lstrip('[').rstrip(']')
        return np.array([float(x) for x in s.split(',')], dtype=np.float32)
    return None

def _trigram_sim(a: str, b: str) -> float:
    """Trigram-based similarity between two strings."""
    a = a.lower().strip(); b = b.lower().strip()
    if len(a) < 2 or len(b) < 2: return 0.0
    ta = set(a[i:i+3] for i in range(len(a)-2))
    tb = set(b[i:i+3] for i in range(len(b)-2))
    if not ta or not tb: return 0.0
    inter = ta & tb
    return 2.0 * len(inter) / (len(ta) + len(tb))

# ── DB ──

class DB:
    def __init__(self, url: str):
        import psycopg2
        self.conn = psycopg2.connect(url)
        self.conn.autocommit = False

    def __enter__(self): return self
    def __exit__(self, *a): self.conn.close()

    def fetch_orphans(self, bank: str = "hermes") -> list[dict]:
        """Fetch memory_units with NO unit_entities linkage."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT mu.id, mu.text, mu.embedding
            FROM memory_units mu
            LEFT JOIN unit_entities ue ON ue.unit_id = mu.id
            WHERE ue.unit_id IS NULL AND mu.bank_id = %s AND mu.embedding IS NOT NULL
        """, (bank,))
        rows = cur.fetchall(); cur.close()
        return [{"id":r[0], "text":r[1] or "", "embedding":_parse_pg_vector(r[2])} for r in rows]

    def fetch_entities(self, bank: str = "hermes") -> list[dict]:
        """Fetch all entities with mention_count."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT e.id, e.canonical_name, e.mention_count
            FROM entities e WHERE e.bank_id = %s ORDER BY e.mention_count DESC
        """, (bank,))
        rows = cur.fetchall(); cur.close()
        return [{"id":str(r[0]), "canonical_name":r[1], "mention_count":r[2]} for r in rows]

    def upsert_entity(self, name: str, bank: str = "hermes") -> str:
        """Insert or update entity. Returns actual entity id."""
        cur = self.conn.cursor()
        eid = str(uuid_mod.uuid4())
        cur.execute("""
            INSERT INTO entities (id, canonical_name, bank_id, metadata, first_seen, last_seen, mention_count)
            VALUES (%s, %s, %s, '{}'::jsonb, NOW(), NOW(), 1)
            ON CONFLICT (bank_id, LOWER(canonical_name)) DO UPDATE SET
                last_seen = NOW(), mention_count = entities.mention_count + 1
            RETURNING id
        """, (eid, name, bank))
        row = cur.fetchone(); cur.close()
        return str(row[0]) if row else eid

    def add_unit_entity(self, unit_id: str, entity_id: str):
        """Insert unit_entities link."""
        cur = self.conn.cursor()
        cur.execute("INSERT INTO unit_entities (unit_id, entity_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (unit_id, entity_id))
        cur.close()

    def commit(self): self.conn.commit()

    def count_orphans(self, bank: str = "hermes") -> int:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM memory_units mu
            LEFT JOIN unit_entities ue ON ue.unit_id = mu.id
            WHERE ue.unit_id IS NULL AND mu.bank_id = %s
        """, (bank,))
        r = cur.fetchone(); cur.close()
        return r[0]

# ── LLM Extraction (no entity reference injection) ──

_EXTRACT_SYSTEM = """你是一个实体提取器。从文本中提取具体的技术、工具、框架、项目名称。

规则：
- 提取具体名称（如 LiteLLM, Docker, PostgreSQL, DeepSeek）
- 不要提取抽象概念（如 Architecture, Design, Concept, Solution）
- 不要提取角色（如 用户, Agent）
- 只返回 JSON 数组，如 ["LiteLLM", "Docker"]
- 如无具体实体返回 []
"""

def extract_batch(texts: list[str]) -> list[list[str]]:
    """Extract entities from a batch of texts via LLM."""
    if not texts: return []

    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=120) as cli:
                # thinking-required 模型（s-deepseek*/agnes*/deepseek-v4-flash）必须启用 thinking
                # 且 max_tokens>8192（业务硬约束）；其余模型保持原行为（无 thinking / 4096）。
                _is_think_model = LLM_MODEL.startswith(("s-deepseek", "agnes", "deepseek-v4-flash"))
                _payload = {
                    "model": LLM_MODEL,
                    "messages": [
                        {"role":"system", "content":_EXTRACT_SYSTEM},
                        {"role":"user", "content":
                            "逐条提取以下文本中的具体实体：\n\n"
                            + "\n---\n".join(f"[{i}] {t[:200]}" for i,t in enumerate(texts))
                            + "\n\n对每条返回 JSON 数组。"},
                    ],
                    "temperature":0.1,
                    "max_tokens": 16384 if _is_think_model else 4096,
                }
                if _is_think_model:
                    _payload["thinking"] = {"type": "enabled"}
                resp = cli.post(
                    LLM_API_URL,
                    headers={"Content-Type":"application/json",
                             "Authorization":f"Bearer {LLM_API_KEY}"},
                    json=_payload,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"].get("content","").strip()
                if not content: return [[] for _ in texts]

                # Parse — find all JSON arrays in response
                arrays = re.findall(r'\[[^\]]*\]', content)
                if arrays:
                    result = []
                    for arr in arrays[:len(texts)]:
                        try:
                            parsed = json.loads(arr)
                            if isinstance(parsed, list):
                                result.append([e.strip() for e in parsed if isinstance(e,str) and e.strip()])
                            else: result.append([])
                        except (json.JSONDecodeError, ValueError): result.append([])
                    while len(result) < len(texts): result.append([])
                    return result
                return [[] for _ in texts]

        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
            print(f"  [WARN] attempt {attempt+1}: {e}", file=sys.stderr)
            if attempt < 2: time.sleep(RETRY_INTERVAL_SEC)
    return [[] for _ in texts]

# ── Matching ──

def _match_entity(name: str, entity_idx: dict[str,str], threshold: float = 0.5) -> str | None:
    """Match an entity name against the index. Returns entity id or None."""
    name_lower = name.lower().strip()
    if not name_lower: return None

    # Exact match
    if name_lower in entity_idx:
        return entity_idx[name_lower]

    # Trigram fuzzy match
    best_id, best_sim = None, 0.0
    for ename, eid in entity_idx.items():
        sim = _trigram_sim(name_lower, ename)
        if sim > best_sim:
            best_sim, best_id = sim, eid

    return best_id if best_sim >= threshold else None

# ── Main ──

def main():
    """回填 scope 实体到记忆单元。
    
    从聚类实体表读取数据，通过 LLM 提取 scope 信息，
    批量回填到 memory_units 表的 entities 字段。
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        print("用法: --dry-run 或 --apply"); sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"=== Entity backfill ({mode}) ===")
    print(f"  LLM={LLM_MODEL} batch={args.batch_size}")

    # 1. Fetch orphans
    print("[1] 孤儿 memory_units ...")
    with DB(DB_URL) as db:
        orphans = db.fetch_orphans()
    print(f"  → {len(orphans)} 条无实体")
    if not orphans:
        print("  ✅ 无存量，无需 backfill"); return

    # 2. Fetch entities
    print("[2] 已有实体 ...")
    with DB(DB_URL) as db:
        entities = db.fetch_entities()
    print(f"  → {len(entities)} 个 (top: {[e['canonical_name'] for e in entities[:6]]})")

    # Build index: lowercase name → entity id
    entity_idx: dict[str,str] = {}
    for e in entities:
        entity_idx[e["canonical_name"].lower().strip()] = e["id"]

    # 3. LLM extraction (no entity reference injection)
    print("[3] LLM 批量提取实体...")
    batches = [orphans[i:i+args.batch_size] for i in range(0, len(orphans), args.batch_size)]
    extracted: list[tuple[str,list[str]]] = []  # (unit_id, [entity_names])

    for bi, batch in enumerate(batches):
        texts = [o["text"] for o in batch]
        results = extract_batch(texts)
        for o, names in zip(batch, results):
            extracted.append((str(o["id"]), names))
        if (bi+1) % 10 == 0 or bi == 0:
            total = sum(len(n) for n in results)
            print(f"  批次 {bi+1}/{len(batches)} → {total} 实体")

    # Stats
    all_names = [n for _, names in extracted for n in names if n]
    unique_names = set(n.lower() for n in all_names)
    print(f"  共提取 {len(all_names)} 个实体名 ({len(unique_names)} 去重)")
    print(f"  有提取结果的记忆: {sum(1 for _,n in extracted if n)}/{len(orphans)}")

    # 4. Match
    print("[4] 实体匹配...")
    write_plans: list[dict] = []
    new_entities: dict[str,str] = {}  # name_lower → generated uuid
    stats = {"exact":0, "trigram":0, "new":0, "skip":0}

    for unit_id, names in extracted:
        matched_ids = set()
        for name in names:
            mid = _match_entity(name, entity_idx, threshold=0.5)
            if mid:
                matched_ids.add(mid)
                # Distinguish exact vs trigram
                if name.lower().strip() in entity_idx:
                    stats["exact"] += 1
                else:
                    stats["trigram"] += 1
                continue
            # New entity
            nkey = name.lower().strip()
            if nkey not in new_entities:
                new_entities[nkey] = str(uuid_mod.uuid4())
            matched_ids.add(new_entities[nkey])
            stats["new"] += 1

        if matched_ids:
            for eid in matched_ids:
                write_plans.append({"unit_id": unit_id, "entity_id": eid})
        else:
            stats["skip"] += 1

    print(f"  exact={stats['exact']} trigram={stats['trigram']} new={stats['new']} skip={stats['skip']}")
    print(f"  → {len(new_entities)} 个新实体, {len(write_plans)} 条 unit_entities 待写入")

    if args.dry_run:
        print("\n=== DRY-RUN 完成，未写入 ===")
        if new_entities:
            print("  新实体示例:", list(new_entities.keys())[:15])
        return

    # 5. Write
    print("[5] 写入 DB...")
    with DB(DB_URL) as db:
        # Upsert new entities
        name_to_actual: dict[str,str] = {}
        for nkey, eid in new_entities.items():
            name_to_actual[nkey] = db.upsert_entity(nkey)

        # Remap plans
        for plan in write_plans:
            for nkey, planned_eid in new_entities.items():
                if plan["entity_id"] == planned_eid:
                    plan["entity_id"] = name_to_actual[nkey]
                    break

        # Batch write unit_entities
        for i in range(0, len(write_plans), WRITE_BATCH_SIZE):
            batch = write_plans[i:i+WRITE_BATCH_SIZE]
            for p in batch:
                db.add_unit_entity(p["unit_id"], p["entity_id"])
            db.commit()
            print(f"  写入 {len(batch)} 条 unit_entities")

        remaining = db.count_orphans()
    print(f"\n✅ 完成。孤儿 memory_units 剩余: {remaining} (原 {len(orphans)})")

if __name__ == "__main__":
    main()