"""定向重建 KT 关联边 + 重采质量基线（不触发 confidence/domain/split 副作用）。

用法（WSL venv 内）:
  cd /root/.hermes/scripts/knowledge-tree-builder
  source venv/bin/activate
  python3 scripts/rebuild_edges_and_baseline.py

行为:
  1. 备份现有 kt-baseline-latest.json
  2. 测量重建前 orphan 率
  3. build_kp_edges(vector_threshold=0.55, same_subject_threshold=0.55, dry_run=False)
     —— 仅"追加"缺失边（不删已有边），orphan 只降不增
  4. 测量重建后 orphan 率
  5. collect_baseline_metrics 重采基线，写回 kt-baseline-latest.json
  6. 旋转 prev <- latest（消除 07-24 陈旧 prev 造成的虚假退化 delta）
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

from knowledge_tree_builder.adapters.database import DatabaseAdapter
from knowledge_tree_builder.core.consolidation import ConsolidationEngine

FLYWHEEL_DIR = os.environ.get("FLYWHEEL_DIR", "/root/.hermes/data/flywheel")
LATEST = os.path.join(FLYWHEEL_DIR, "kt-baseline-latest.json")
PREV = os.path.join(FLYWHEEL_DIR, "kt-baseline-prev.json")
VEC_T = float(os.environ.get("KT_VECTOR_EDGE_SIM_THRESHOLD", "0.55"))
SAME_T = float(os.environ.get("KT_EDGE_SIM_THRESHOLD", "0.55"))
# 跨 subject centroid 门控：默认 0.72（相对原硬编码 0.80 放宽，进一步降 orphan）
CG_T = float(os.environ.get("KT_VECTOR_CENTROID_GATE", "0.72"))


def _load_env(path: str) -> dict:
    env: dict = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _orphan_stats(adapter: DatabaseAdapter) -> dict:
    cur = adapter.cursor
    cur.execute("SELECT COUNT(*) FROM knowledge_tree WHERE node_type='knowledge_point'")
    total = int(cur.fetchone()[0] or 0)
    cur.execute(
        "SELECT COUNT(*) FROM knowledge_tree kp "
        "LEFT JOIN knowledge_tree_edges e ON kp.id=e.from_node_id OR kp.id=e.to_node_id "
        "WHERE kp.node_type='knowledge_point' AND e.from_node_id IS NULL"
    )
    orphan = int(cur.fetchone()[0] or 0)
    cur.execute("SELECT COUNT(*) FROM knowledge_tree_edges WHERE relation_type='related'")
    edges = int(cur.fetchone()[0] or 0)
    return {"total": total, "orphan": orphan, "edges": edges,
            "orphan_pct": round(orphan / total * 100, 1) if total else 0.0}


def main() -> None:
    env = _load_env("/root/.hermes/.env")
    db_url = env.get("KT_DB_URL")
    if not db_url:
        raise SystemExit("KT_DB_URL 未设置")

    adapter = DatabaseAdapter(db_url)
    eng = ConsolidationEngine()

    print("=== 重建前 ===")
    before = _orphan_stats(adapter)
    print(f"  total={before['total']} edges={before['edges']} "
          f"orphan={before['orphan']} ({before['orphan_pct']}%)")

    print(f"\n=== 重建 KP 关联边 (vec={VEC_T}, same={SAME_T}, centroid_gate={CG_T}) ===")
    res = eng.build_kp_edges(
        adapter,
        vector_threshold=VEC_T,
        same_subject_threshold=SAME_T,
        centroid_gate=CG_T,
        dry_run=False,
    )
    adapter.conn.commit()
    print(f"  同源共现: {res['source_edges']} 边")
    print(f"  向量桥接: {res['vector_edges']} 边")
    print(f"  同科高相似: {res['same_subject_edges']} 边")
    print(f"  本次新增: {res['total']} 边")

    print("\n=== 重建后 ===")
    after = _orphan_stats(adapter)
    print(f"  total={after['total']} edges={after['edges']} "
          f"orphan={after['orphan']} ({after['orphan_pct']}%)")
    drop = before['orphan'] - after['orphan']
    print(f"  orphan 减少: {drop} ({before['orphan_pct']}→{after['orphan_pct']}%)")

    print("\n=== 重采质量基线 ===")
    metrics = eng.collect_baseline_metrics(adapter)
    if not metrics:
        raise SystemExit("collect_baseline_metrics 返回 None")
    # 备份现有 latest
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bak = f"{LATEST}.bak.{ts}"
    if os.path.exists(LATEST):
        shutil.copy(LATEST, bak)
        print(f"  已备份旧 latest: {bak}")
    payload = {"collected_at": datetime.now(timezone.utc).isoformat(), "metrics": metrics}
    with open(LATEST, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"  已写回 {LATEST}")
    print(f"  orphan_kps={metrics['orphan_kps']} total_kps={metrics['total_kps']}")

    print("\n=== 旋转 prev <- latest ===")
    if os.path.exists(PREV):
        p_bak = f"{PREV}.bak.{ts}"
        shutil.copy(PREV, p_bak)
        print(f"  已备份旧 prev: {p_bak}")
    shutil.copy(LATEST, PREV)
    print(f"  已同步 prev <- latest（消除陈旧 prev 造成的虚假退化 delta）")

    print("\n✅ 完成")


if __name__ == "__main__":
    main()
