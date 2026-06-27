"""报告输出 — 格式化打印分类结果，无 IO 副作用。"""

from typing import Any


def print_report(source: str, entries: list[str], result: dict[str, Any], char_limit: int = 50000) -> None:
    """打印 Phase 1 分类结果的格式化报告。

    Args:
        source: 来源标识（如 "MEMORY.md"）
        entries: 条目列表
        result: 分类结果
        char_limit: 字符限制（MEMORY.md 默认 50000，USER.md 默认 15000）
    """
    merge_list = result.get("merge", [])
    remove_list = result.get("remove", [])
    compress_list = result.get("compress", [])
    hindsight_list = result.get("hindsight", [])

    remove_indices: set[int] = set(
        r.get("index", -1) for r in remove_list if r.get("index", -1) >= 0
    )
    for m in merge_list:
        for j in m.get("indices", []):
            remove_indices.add(j)
    for c in compress_list:
        idx = c.get("index", -1)
        if idx >= 0:
            remove_indices.add(idx)
    for h in hindsight_list:
        idx = h.get("index", -1)
        if idx >= 0:
            remove_indices.add(idx)

    final_keep = [i for i in range(len(entries)) if i not in remove_indices]
    keep_chars = sum(len(entries[i]) for i in final_keep)
    total_chars = sum(len(e) for e in entries)

    limit = char_limit

    print(f"\n{'#' * 60}")
    print(f"#  {source} — 分类结果")
    print(
        f"#  {len(entries)} 条 / {total_chars:,} 字符"
        f" → 保留 {len(final_keep)} 条 / {keep_chars:,} 字符"
        f"（{keep_chars / limit * 100:.0f}%）"
    )
    print(f"{'#' * 60}")
    print(f"  merge: {len(merge_list)} 组 | remove: {len(remove_indices)} 条 | compress: {len(compress_list)} 条 | hindsight: {len(hindsight_list)} 条")

    if merge_list:
        print(f"\n  🔗 merge:")
        for m in merge_list:
            idxs = m.get("indices", [])
            merged = m.get("合并为", "")
            print(f"    [{', '.join(str(x) for x in idxs)}]")
            for j in idxs[:3]:
                if j < len(entries):
                    print(f"      [{j}] {entries[j].split(chr(10))[0][:70]}")
            print(f"    → {merged[:120]}")

    if compress_list:
        print(f"\n  ✂️ compress:")
        for c in compress_list:
            idx = c.get("index", -1)
            if idx < 0 or idx >= len(entries):
                continue
            compressed = c.get("精简为", "")
            print(f"    [{idx:>3}] {len(entries[idx]):>4} → {len(compressed):>4} 字符")
            print(f"      原: {entries[idx].split(chr(10))[0][:70]}")
            print(f"      新: {compressed[:100]}")

    if hindsight_list:
        print(f"\n  📤 hindsight（迁到 Hindsight）:")
        for h in hindsight_list:
            idx = h.get("index", -1)
            if idx < 0 or idx >= len(entries):
                continue
            tags = h.get("关键词", [])
            tag_str = ", ".join(tags[:5])
            print(f"    [{idx:>3}] {len(entries[idx]):>4}char  {entries[idx].split(chr(10))[0][:70]}")
            print(f"           关键词: {tag_str}")

    if remove_indices:
        print(f"\n  🗑️ remove:")
        for idx in sorted(remove_indices):
            if idx >= len(entries):
                continue
            reason = ""
            for r in remove_list:
                if r.get("index") == idx:
                    reason = r.get("原因", "")
                    break
            print(f"    [{idx:>3}] {len(entries[idx]):>4}char  {entries[idx].split(chr(10))[0][:70]}")
            if reason:
                print(f"            原因: {reason}")

    flagged = result.get("flagged", [])
    if flagged:
        total_flagged = sum(f.get("count", 0) for f in flagged)
        print(f"\n  ⚠️ flagged（分类失败，共 {total_flagged} 条）:")
        for f in flagged:
            rng = f.get("range", [0, 0])
            print(f"    [{rng[0]}-{rng[1]}] {f.get('count', 0)} 条 — {f.get('reason', '')[:80]}")


def print_v2_detail(label: str, v2_list: list[dict[str, Any]], entries: list[str]) -> None:
    """打印 Phase 2 验证结果的详细报告。"""
    if not v2_list:
        return
    print(f"\n  🔵 {label} ({len(v2_list)} 条):")
    for item in v2_list:
        idx = item.get("index", -1)
        note = item.get("note", "")
        corrected = item.get("corrected_text", "")
        original = entries[idx] if 0 <= idx < len(entries) else "?"
        print(f"    [{idx}] {original[:80].split(chr(10))[0]}")
        if corrected:
            print(f"       修正→ {corrected[:120]}")
        if note:
            print(f"       原因: {note}")
