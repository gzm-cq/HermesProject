import os, psycopg2, numpy as np, json

u = os.environ["KT_DB_URL"]
c = psycopg2.connect(u); cur = c.cursor()
cur.execute("SELECT id, parent_id, k_vector::text FROM knowledge_tree WHERE node_type='knowledge_point' AND k_vector IS NOT NULL")
rows = cur.fetchall()
c.close()

# parse vectors
nodes = []
for rid, pid, vt in rows:
    vec = np.array([float(x) for x in vt.strip("[]").split(",") if x.strip()], dtype=np.float32)
    if vec.shape[0] == 0:
        continue
    nodes.append((int(rid), int(pid) if pid is not None else -1, vec))

print(f"loaded {len(nodes)} kps with vectors")

# existing edges (both directions) as set of frozensets
c = psycopg2.connect(u); cur = c.cursor()
cur.execute("SELECT from_node_id, to_node_id FROM knowledge_tree_edges")
existing = set()
for a, b in cur.fetchall():
    existing.add(frozenset((int(a), int(b))))
c.close()
print(f"existing edges cover {len(existing)} pairs")

by_subject = {}
for rid, pid, vec in nodes:
    by_subject.setdefault(pid, []).append((rid, vec))

def cos_sim(a, b):
    na = np.linalg.norm(a); nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))

# For each threshold, count NEW distinct nodes that would gain at least one edge
# (strategy 3: within-subject; strategy 2 cross-subject is additional, estimate lower bound here)
thresholds = [0.65, 0.6, 0.55, 0.5, 0.45, 0.4]
results = {}
for thr in thresholds:
    new_nodes = set()
    new_pairs = 0
    for pid, kps in by_subject.items():
        if len(kps) < 2:
            continue
        ids = [k[0] for k in kps]
        vecs = np.stack([k[1] for k in kps])
        # normalize
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        nv = vecs / norms
        sim = nv @ nv.T  # cosine
        n = len(ids)
        for i in range(n):
            for j in range(i + 1, n):
                if sim[i, j] >= thr:
                    pair = frozenset((ids[i], ids[j]))
                    if pair not in existing:
                        new_pairs += 1
                        new_nodes.add(ids[i]); new_nodes.add(ids[j])
    results[thr] = (new_pairs, len(new_nodes))
    # orphan after = total - (nodes_with_edge_existing + new_nodes)
    existing_nodes = set()
    for e in existing:
        existing_nodes.update(e)
    orphan_after = len(nodes) - len(existing_nodes | new_nodes)
    print(f"thr={thr}: new_pairs(within-subj)={new_pairs} new_nodes={len(new_nodes)} orphan_after(lowerbound)={orphan_after} ({orphan_after/len(nodes)*100:.1f}%)")

print(json.dumps({str(k): v for k, v in results.items()}))
