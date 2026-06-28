"""记忆去重模块 — MinHash LSH + Jaccard fallback"""

from typing import Any

HAS_DATASKETCH = False
try:
    from datasketch import MinHash, MinHashLSH

    HAS_DATASKETCH = True
except ImportError:
    pass


def _bigrams(text: str) -> set[str]:
    """字符 bigram 集合，对中文更友好。"""
    if len(text) > 1:
        return {text[i : i + 2] for i in range(len(text) - 1)}
    return {text} if text else set()


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    """计算 Jaccard 相似度。"""
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def jaccard_dedup(
    memories: list[dict[str, Any]],
    threshold: float = 0.85,
) -> tuple[list[dict[str, Any]], int]:
    """O(n^2) Jaccard 去重（fallback）。

    保留最早创建的记忆，标记重复项。

    Args:
        memories: 记忆列表，每项含 id, text, created_at
        threshold: Jaccard 相似度阈值

    Returns:
        (去重后的记忆列表, 删除数量)
    """
    if not memories:
        return [], 0

    sorted_mems = sorted(
        memories,
        key=lambda m: (m.get("created_at") is None, m.get("created_at", "")),
    )

    bigram_cache: dict[str, set[str]] = {}
    removed: set[str] = set()

    for i in range(len(sorted_mems)):
        id_i = str(sorted_mems[i]["id"])
        if id_i in removed:
            continue
        if id_i not in bigram_cache:
            bigram_cache[id_i] = _bigrams(sorted_mems[i].get("text", ""))
        set_i = bigram_cache[id_i]

        for j in range(i + 1, len(sorted_mems)):
            id_j = str(sorted_mems[j]["id"])
            if id_j in removed:
                continue
            if id_j not in bigram_cache:
                bigram_cache[id_j] = _bigrams(sorted_mems[j].get("text", ""))
            set_j = bigram_cache[id_j]

            if _jaccard(set_i, set_j) > threshold:
                removed.add(id_j)

    result = [m for m in sorted_mems if str(m["id"]) not in removed]
    return result, len(removed)


def minhash_dedup(
    memories: list[dict[str, Any]],
    threshold: float = 0.85,
    num_perm: int = 128,
) -> tuple[list[dict[str, Any]], int]:
    """MinHash LSH 去重（高性能）。

    使用 datasketch.MinHashLSH 实现近似 Jaccard 去重。
    保留最早创建的记忆。

    算法特性说明：
    - MinHash 是概率算法，结果与精确 Jaccard 可能有小概率差异
    - num_perm=128 时，误差概率 < 1/2^128，可忽略
    - 与 jaccard_dedup 的差异主要在极端情况下（短文本、高相似度边界）
    - 性能优势：O(n) 近似查找 vs O(n^2) 精确比较

    Args:
        memories: 记忆列表，每项含 id, text, created_at
        threshold: MinHash 相似度阈值
        num_perm: MinHash 排列数

    Returns:
        (去重后的记忆列表, 删除数量)
    """
    if not memories:
        return [], 0

    if not HAS_DATASKETCH:
        return jaccard_dedup(memories, threshold)

    sorted_mems = sorted(
        memories,
        key=lambda m: (m.get("created_at") is None, m.get("created_at", "")),
    )

    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    minhash_map: dict[str, MinHash] = {}
    removed: set[str] = set()

    for mem in sorted_mems:
        mem_id = str(mem["id"])
        if mem_id in removed:
            continue

        shingles = _bigrams(mem.get("text", ""))
        mh = MinHash(num_perm=num_perm)
        for s in shingles:
            mh.update(s.encode("utf-8"))

        if len(minhash_map) > 0:
            candidates = lsh.query(mh)
            is_dup = False
            for cand_id in candidates:
                if cand_id in removed:
                    continue
                cand_mh = minhash_map.get(cand_id)
                if cand_mh is not None:
                    sim = mh.jaccard(cand_mh)
                    if sim > threshold:
                        removed.add(mem_id)
                        is_dup = True
                        break
            if is_dup:
                continue

        minhash_map[mem_id] = mh
        lsh.insert(mem_id, mh)

    result = [m for m in sorted_mems if str(m["id"]) not in removed]
    return result, len(removed)


def dedup_memories(
    memories: list[dict[str, Any]],
    threshold: float = 0.85,
    num_perm: int = 128,
    use_minhash: bool = True,
) -> tuple[list[dict[str, Any]], int, str]:
    """统一去重入口，自动选择 MinHash 或 Jaccard。

    Args:
        memories: 记忆列表，每项含 id, text, created_at
        threshold: 相似度阈值
        num_perm: MinHash 排列数（仅 MinHash 模式）
        use_minhash: 是否尝试使用 MinHash

    Returns:
        (去重后的记忆列表, 删除数量, 使用的方法名)
    """
    if use_minhash and HAS_DATASKETCH:
        result, removed = minhash_dedup(memories, threshold=threshold, num_perm=num_perm)
        return result, removed, "minhash"
    else:
        result, removed = jaccard_dedup(memories, threshold=threshold)
        method = "jaccard" if not use_minhash else "jaccard(fallback)"
        return result, removed, method
