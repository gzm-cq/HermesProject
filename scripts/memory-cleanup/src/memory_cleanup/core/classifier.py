"""分类器 — Phase 1 并行分批分类，以及 remove 候选计算。"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from memory_cleanup.core.prompts import build_system_prompt

if TYPE_CHECKING:
    from memory_cleanup.adapters.llm_client import LLMClient

logger = logging.getLogger(__name__)
# 包含这些关键词的条目直接删除，跳过 Phase 2 验证
AUTO_REMOVE_PATTERNS = [
    "清理",
    "MEMORY.md 清理",
    "V5 方案",
    "V6",
    "方法论",
    "Memory cleaning methodology",
    "memory cleanup pipeline",
]


def classify_all(
    entries: list[str],
    source_type: str,
    llm_client: "LLMClient",
    batch_size: int = 10,
    max_workers: int = 8,
    vote_count: int = 1,
) -> dict[str, Any]:
    """并行分批分类全部条目，支持多轮投票（remove 并集，其他决策取交集）。

    Args:
        entries: 条目列表
        source_type: "MEMORY" 或 "USER"
        llm_client: LLMClient 实例（依赖注入）
        batch_size: 每批条目数
        max_workers: 并发线程数
        vote_count: 投票轮数（>1 时跑 N 轮；remove 并集，其他决策取交集）

    Returns:
        {"merge": [...], "remove": [...], "compress": [...], "hindsight": [...], "flagged": [...]}
    """
    if not entries:
        return {"merge": [], "remove": [], "compress": [], "hindsight": [], "flagged": []}

    if vote_count <= 1:
        return _classify_single_round(entries, source_type, llm_client, batch_size, max_workers)

    # 多轮投票：跑 N 轮，取交集
    print(f"     {source_type}: {vote_count} 轮投票模式", flush=True)
    rounds = []
    for r in range(vote_count):
        print(f"     ── 第 {r + 1}/{vote_count} 轮 ──", flush=True)
        result = _classify_single_round(entries, source_type, llm_client, batch_size, max_workers)
        rounds.append(result)

    return _vote_intersect(rounds, entries)


def _classify_single_round(
    entries: list[str],
    source_type: str,
    llm_client: "LLMClient",
    batch_size: int,
    max_workers: int,
) -> dict[str, Any]:
    """单轮分类：分批并行，每批内部再拆子批并行，减少单次 LLM 上下文量。"""
    # 子批大小：每 5 条一个 LLM 调用
    SUB_BATCH_SIZE = 5
    # 先分外层批（batch_size 条/批）
    outer_batches = [
        (entries[s : s + batch_size], s)
        for s in range(0, len(entries), batch_size)
    ]
    # 每批再拆子批
    sub_batches: list[tuple[list[str], int]] = []
    for batch, offset in outer_batches:
        for i in range(0, len(batch), SUB_BATCH_SIZE):
            sub_batches.append((batch[i:i + SUB_BATCH_SIZE], offset + i))
    print(
        f"     {source_type}: {len(outer_batches)} 批 × {SUB_BATCH_SIZE} 条子批"
        f" = {len(sub_batches)} 个调用（{len(entries)} 条）...",
        flush=True,
    )

    all_merge: list[dict] = []
    all_remove: list[dict] = []
    all_compress: list[dict] = []
    all_hindsight: list[dict] = []
    all_flagged: list[dict] = []

    def _classify_one(batch: list[str], offset: int) -> dict[str, Any]:
        system_prompt = build_system_prompt(source_type, offset)
        return llm_client.classify_batch(batch, offset, source_type, system_prompt)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_classify_one, b, s): (b, s) for b, s in sub_batches}
        done = 0
        for f in as_completed(futures):
            done += 1
            result = f.result()
            if "error" in result:
                batch, offset = futures[f]
                logger.warning(
                    "批失败 [%d-%d]: %s — 尝试单条重试",
                    offset, offset + len(batch) - 1, result["error"][:120],
                )
                # 单条重试：逐条调用，一条失败不污染其他
                retry_ok = 0
                retry_fail = 0
                for i, entry in enumerate(batch):
                    idx = offset + i
                    single_prompt = build_system_prompt(source_type, idx)
                    single_result = llm_client.classify_batch([entry], idx, source_type, single_prompt)
                    if "error" not in single_result:
                        all_merge.extend(single_result.get("merge", []))
                        all_remove.extend(single_result.get("remove", []))
                        all_compress.extend(single_result.get("compress", []))
                        all_hindsight.extend(single_result.get("hindsight", []))
                        retry_ok += 1
                    else:
                        retry_fail += 1
                        all_flagged.append({
                            "range": [idx, idx],
                            "count": 1,
                            "reason": f"单条重试仍失败: {single_result['error'][:150]}",
                        })
                logger.info(
                    "单条重试 [%d-%d]: 成功 %d / 失败 %d",
                    offset, offset + len(batch) - 1, retry_ok, retry_fail,
                )
                continue
            all_merge.extend(result.get("merge", []))
            all_remove.extend(result.get("remove", []))
            all_compress.extend(result.get("compress", []))
            all_hindsight.extend(result.get("hindsight", []))
            if done % 3 == 0 or done == len(futures):
                print(
                    f"     批次 {done}/{len(futures)}"
                    f"（merge {len(all_merge)}组 / remove {len(all_remove)}条"
                    f" / compress {len(all_compress)}条 / hindsight {len(all_hindsight)}条）",
                    flush=True,
                )

    def _dedup(items: list[dict], key_fn: Any) -> list[dict]:
        seen: set = set()
        result: list[dict] = []
        for item in items:
            k = key_fn(item)
            if k is not None and k not in seen:
                seen.add(k)
                result.append(item)
        return result

    merged = _dedup(all_merge, lambda m: tuple(m.get("indices", [])))
    compressed = _dedup(all_compress, lambda c: c.get("index", -1))
    hindsight = _dedup(all_hindsight, lambda h: h.get("index", -1))
    # hindsight 仅适用于 USER
    if source_type == "USER":
        from memory_cleanup.config import CONFIG
        if CONFIG.keyword_backfill:
            hindsight = backfill_hindsight_keywords(
                entries, hindsight, keyword_count=CONFIG.hindsight_keyword_count
            )
        hindsight = validate_hindsight_quality(entries, hindsight)
    return {
        "merge": validate_merge_quality(entries, merged),
        "remove": _dedup(all_remove, lambda r: r.get("index", -1)),
        "compress": validate_compress_quality(entries, compressed, source_type),
        "hindsight": hindsight,
        "flagged": all_flagged,
    }


def _vote_intersect(
    rounds: list[dict[str, Any]], entries: list[str]
) -> dict[str, Any]:
    """多轮投票：remove 取并集，其它决策取交集。

    - remove：任一轮标 remove 的 index 都保留，后续交给 Phase 2 验证
    - compress：所有轮次都标 compress 的 index 才保留（取第一轮的精简版本）
    - hindsight：所有轮次都标 hindsight 的 index 才保留（取第一轮的关键词标签）
    - merge：所有轮次都标 merge 的 indices 组合才保留（取第一轮的合并版本）
    - flagged：取所有轮次的并集（任一轮 flagged 就标记）
    """
    n = len(rounds)
    if n == 0:
        return {"merge": [], "remove": [], "compress": [], "hindsight": [], "flagged": []}

    # remove 并集（2026-06-13 修复：不同轮次 LLM 产出不同 remove 候选）
    # 交集过于严格导致投票后 remove=0/0，改为并集保留所有轮次的判断
    remove_sets = [
        {r.get("index", -1) for r in rnd.get("remove", []) if r.get("index", -1) >= 0}
        for rnd in rounds
    ]
    remove_union = remove_sets[0]
    for s in remove_sets[1:]:
        remove_union |= s

    # 用第一轮的 remove 数据填充并集条目
    remove_by_idx: dict[int, dict] = {}
    for rnd in rounds:
        for r in rnd.get("remove", []):
            idx = r.get("index", -1)
            if idx >= 0 and idx in remove_union:
                remove_by_idx[idx] = r  # 后轮覆盖前轮，取相同 index 的最后一个原因描述
    final_remove = list(remove_by_idx.values())

    # compress 交集
    compress_sets = [
        {c.get("index", -1) for c in rnd.get("compress", []) if c.get("index", -1) >= 0}
        for rnd in rounds
    ]
    compress_intersection = compress_sets[0]
    for s in compress_sets[1:]:
        compress_intersection &= s

    final_compress = [
        c for c in rounds[0].get("compress", [])
        if c.get("index", -1) in compress_intersection
    ]

    # hindsight 交集
    hindsight_sets = [
        {h.get("index", -1) for h in rnd.get("hindsight", []) if h.get("index", -1) >= 0}
        for rnd in rounds
    ]
    hindsight_intersection = hindsight_sets[0]
    for s in hindsight_sets[1:]:
        hindsight_intersection &= s

    final_hindsight = [
        h for h in rounds[0].get("hindsight", [])
        if h.get("index", -1) in hindsight_intersection
    ]

    # merge 交集（按 indices 元组匹配）
    merge_sets = [
        {tuple(m.get("indices", [])) for m in rnd.get("merge", [])}
        for rnd in rounds
    ]
    merge_intersection = merge_sets[0]
    for s in merge_sets[1:]:
        merge_intersection &= s

    final_merge = [
        m for m in rounds[0].get("merge", [])
        if tuple(m.get("indices", [])) in merge_intersection
    ]

    # flagged 并集
    all_flagged: list[dict] = []
    for rnd in rounds:
        all_flagged.extend(rnd.get("flagged", []))

    print(
        f"     投票结果: remove {len(final_remove)}/{len(remove_sets[0])}（并集 / 首轮）"
        f" compress {len(final_compress)}/{len(compress_sets[0])}"
        f" hindsight {len(final_hindsight)}/{len(hindsight_sets[0])}"
        f" merge {len(final_merge)}/{len(merge_sets[0])}"
        f"（交集 / 首轮）",
        flush=True,
    )

    return {
        "merge": final_merge,
        "remove": final_remove,
        "compress": final_compress,
        "hindsight": final_hindsight,
        "flagged": all_flagged,
    }


def calc_remove_candidates(
    entries: list[str], result: dict[str, Any]
) -> tuple[list[dict], list[dict]]:
    """将 remove 候选分拣为「直接删」和「需 Phase 2 验证」两类。

    直接删的条件：
    - 空条目（仅"§"）
    - 包含 AUTO_REMOVE_PATTERNS 关键词
    - 已被 merge/compress 覆盖的索引

    Returns:
        (direct_list, need_verify_list)
    """
    remove_list = result.get("remove", [])
    direct: list[dict] = []
    need_v2: list[dict] = []

    covered_indices: set[int] = set()
    for m in result.get("merge", []):
        covered_indices.update(m.get("indices", []))
    for c in result.get("compress", []):
        covered_indices.add(c.get("index", -1))
    for h in result.get("hindsight", []):
        covered_indices.add(h.get("index", -1))

    for r in remove_list:
        idx = r.get("index", -1)
        if idx < 0:
            continue
        text = entries[idx] if idx < len(entries) else ""
        is_auto = False
        if text.strip() in ("", "§"):
            is_auto = True
        for pat in AUTO_REMOVE_PATTERNS:
            if pat in text:
                is_auto = True
                break
        if idx in covered_indices:
            is_auto = True
        if is_auto:
            direct.append(r)
        else:
            need_v2.append(r)

    return direct, need_v2


# ── merge/compress 质量校验 ──


def validate_merge_quality(
    entries: list[str], merge_list: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """校验 merge 输出的抽象度 —— 过滤掉"简单拼接"类低质量 merge。

    质量标准：
    - 合并文本长度不应超过各原文总长度的 80%（否则是拼接而非抽象）
    - 当所有原文都含日期时，合并文本仍含具体日期 → 抽象不足
    - 关键词覆盖率检查（中文 + 英文兜底）：
      * 条目数 ≤ 3 的小批量：任意一条原文的关键词出现在合并文本中即放行
      * 条目数 > 3：avg 覆盖率 ≥ 0.15 才能通过

    Returns:
        仅保留通过校验的 merge 条目
    """

    DATE_PATTERN = re.compile(r"\d{4}[年/-]\d{1,2}[月/-]?\d{0,2}")
    passed: list[dict[str, Any]] = []

    for m in merge_list:
        merged = m.get("合并为", "")
        indices = m.get("indices", [])
        originals = [entries[i] for i in indices if i < len(entries)]
        if not originals or not merged:
            continue

        # 检查1：不是简单拼接（合并文本长度 < 原文总长度 * 0.8）
        total_orig_len = sum(len(o) for o in originals)
        if len(merged) > total_orig_len * 0.8:
            logger.info(
                "merge 质量过滤 [%s]: 疑似拼接 (%d > %d*0.8)",
                indices, len(merged), total_orig_len,
            )
            continue

        # 检查2：所有原文都含日期时，合并文本不应含具体日期
        # 但如果已充分抽象（长度 < 原文 50%），即使含日期也放行
        dates_in_orig = sum(1 for o in originals if DATE_PATTERN.search(o))
        if dates_in_orig == len(originals) and DATE_PATTERN.search(merged):
            if len(merged) >= total_orig_len * 0.5:
                logger.info("merge 质量过滤 [%s]: 抽象后仍含具体日期且未充分压缩", indices)
                continue

        # 检查3：关键词覆盖率（优先中文，纯英文条目兜底到英文单词）
        # 相比 Jaccard（交集/并集），覆盖率（交集/原文集）对技术文档更友好，
        # 不会因为合并文本引入新术语就大幅拉低分值。
        # 条目数 ≤ 3 时用 max（任一原文重叠即放行），避免短条目误杀。
        # 中文关键词为空时回退到英文单词（[a-zA-Z]{4,}）检查。
        ALL_KW_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}")
        orig_kws = [set(ALL_KW_PATTERN.findall(o)) for o in originals]
        merged_kw = set(ALL_KW_PATTERN.findall(merged))
        if merged_kw:
            coverages = [
                len(okw & merged_kw) / max(len(okw), 1)
                for okw in orig_kws
            ]
            if len(originals) <= 3:
                # 小批量：任一原文的关键词出现在合并文本中即放行
                if max(coverages) <= 0:
                    logger.info(
                        "merge 质量过滤 [%s]: 小批量无任何关键词重叠", indices
                    )
                    continue
            else:
                avg_coverage = sum(coverages) / len(coverages)
                if avg_coverage < 0.15:
                    logger.info(
                        "merge 质量过滤 [%s]: 关键词覆盖率过低 %.2f",
                        indices, avg_coverage,
                    )
                    continue

        passed.append(m)

    return passed


def _chinese_bigrams(text: str) -> set[str]:
    """提取中文 2-字滑动窗口 bigram。

    从文本中提取所有连续中文序列，再生成每个序列的重叠 2-字组合。
    同时保留英文单词 ([a-zA-Z]{4,}) 作为补充。
    相比 [\u4e00-\u9fff]{2,}（中文长序列），bigram 对 LLM 重述更鲁棒。

    Example:
        "项目计划拆解" → {"项目", "目计", "计划", "划拆", "拆解"}
    """
    result: set[str] = set()
    # 提取所有中文字符
    chinese_chars = re.findall(r"[\u4e00-\u9fff]+", text)
    for seq in chinese_chars:
        if len(seq) >= 2:
            for i in range(len(seq) - 1):
                result.add(seq[i : i + 2])
    # 补充英文关键词
    en_words = re.findall(r"[a-zA-Z]{4,}", text)
    result.update(en_words)
    return result


def _extract_dates(text: str) -> set[str]:
    """提取文本中的日期字符串。

    支持格式：
    - YYYY-MM-DD (2026-06-28)
    - YYYY/MM/DD (2026/06/28)
    - MM月DD日 (6月28日、06月28日)
    """
    result: set[str] = set()
    # YYYY-MM-DD
    dates1 = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", text)
    result.update(dates1)
    # YYYY/MM/DD
    dates2 = re.findall(r"\d{4}/\d{1,2}/\d{1,2}", text)
    result.update(dates2)
    # MM月DD日
    dates3 = re.findall(r"\d{1,2}月\d{1,2}日", text)
    result.update(dates3)
    return result


def _extract_key_numbers(text: str) -> set[str]:
    """提取带单位的关键数字。

    匹配模式：数字（整数或小数）+ 可选单位（中英文单位、常见缩写）
    例如：100ms、5000条、3.14、100个、50%
    """
    result: set[str] = set()
    pattern = re.compile(
        r"\d+\.?\d*"  # 数字（整数或小数）
        r"(?:"  # 单位组（可选）
        r"[a-zA-Z]+"  # 英文单位：ms、s、min、h、KB、MB、GB 等
        r"|[\u4e00-\u9fff]{1,3}"  # 中文单位：条、个、次、天 等
        r"|%"  # 百分号
        r")?",
        re.IGNORECASE,
    )
    matches = pattern.findall(text)
    result.update(matches)
    return result


def _extract_proper_nouns(text: str) -> set[str]:
    """提取英文专有名词（大写开头，长度 >= 4）。

    例如：Python、PostgreSQL、HDBSCAN、TensorFlow
    """
    result: set[str] = set()
    # 匹配大写字母开头，后跟至少 3 个字母（总长度 >= 4）
    pattern = re.compile(r"\b[A-Z][a-zA-Z]{3,}\b")
    matches = pattern.findall(text)
    result.update(matches)
    return result


def validate_compress_quality(
    entries: list[str],
    compress_list: list[dict[str, Any]],
    source: str = "MEMORY",
    strict_mode: bool | None = None,
    min_ratio_memory: float = 12.0,
    min_ratio_user: float = 12.0,
    keyword_overlap_memory: float = 0.20,
    keyword_overlap_user: float = 0.10,
    entity_retention_memory: float = 0.30,
) -> list[dict[str, Any]]:
    """校验 compress 输出的信息完整性 —— 确保关键事实不被丢弃。

    质量规则：
    - 压缩版长度不能小于 10 字符（过短说明过度压缩）
    - 原文/压缩版的压缩比不得超过上限
    - 关键实体（IP/端口/版本号）必须 100% 保留（仅 MEMORY）
    - 所有实体（含非关键路径/URL）的保留率需满足下限（仅 MEMORY）
    - 压缩版必须比原文更短（至少有 5% 的实际节省）
    - 压缩版关键词重叠要求
      * MEMORY 使用 [\u4e00-\u9fff]{2,} 提取中文关键词
      * USER 使用中文 bigram（2-字滑动窗口），对 LLM 重述更鲁棒
    - 严格模式下新增检查：
      * 日期保留：原文中的日期必须 100% 保留
      * 数字保留：原文中的关键数字必须 100% 保留
      * 专有名词保留：原文中的英文专有名词必须 100% 保留

    Args:
        entries: 原始条目列表
        compress_list: 压缩候选列表
        source: "MEMORY" 或 "USER"
        strict_mode: 是否启用严格模式（None 时使用全局配置）
        min_ratio_memory: MEMORY 压缩比上限（原文/压缩版）
        min_ratio_user: USER 压缩比上限
        keyword_overlap_memory: MEMORY 关键词重叠下限
        keyword_overlap_user: USER 关键词重叠下限
        entity_retention_memory: MEMORY 实体保留率下限

    Returns:
        仅保留通过校验的 compress 条目
    """
    from memory_cleanup.config import CONFIG

    if strict_mode is None:
        strict_mode = CONFIG.compress_strict_mode

    if strict_mode:
        max_ratio = CONFIG.compress_min_ratio_memory if source == "MEMORY" else CONFIG.compress_min_ratio_user
        kw_threshold = CONFIG.compress_keyword_overlap_memory if source == "MEMORY" else CONFIG.compress_keyword_overlap_user
        entity_retention = CONFIG.compress_entity_retention_memory if source == "MEMORY" else 0.0
    else:
        max_ratio = min_ratio_memory if source == "MEMORY" else min_ratio_user
        kw_threshold = keyword_overlap_memory if source == "MEMORY" else keyword_overlap_user
        entity_retention = entity_retention_memory if source == "MEMORY" else 0.0

    CRITICAL_PATTERN = re.compile(
        r"\d+\.\d+\.\d+\.\d+"
        r"|:\d{2,5}(?!\.\d)"
        r"|v\d+\.\d+"
    )
    NON_CRITICAL_PATTERN = re.compile(
        r"https?://[^\s]+"
        r"|/[\w/]+(?:\.[\w]+)?"
    )
    passed: list[dict[str, Any]] = []

    for c in compress_list:
        idx = c.get("index", -1)
        compressed = c.get("精简为", "")
        if idx < 0 or idx >= len(entries) or not compressed:
            continue

        original = entries[idx]

        if len(compressed) < 10:
            logger.info("compress 质量过滤 [%d]: 过短 (%d)", idx, len(compressed))
            continue

        ratio = len(original) / max(len(compressed), 1)
        if ratio > max_ratio:
            logger.info("compress 质量过滤 [%d]: 压缩比过高 (%.1f > %.1f)", idx, ratio, max_ratio)
            continue

        if source == "MEMORY":
            orig_critical = CRITICAL_PATTERN.findall(original)
            if orig_critical:
                comp_critical = CRITICAL_PATTERN.findall(compressed)
                missing_critical = set(orig_critical) - set(comp_critical)
                if missing_critical:
                    logger.info(
                        "compress 质量过滤 [%d]: 遗漏关键实体 %s", idx, missing_critical
                    )
                    continue

        if source == "MEMORY":
            orig_noncritical = NON_CRITICAL_PATTERN.findall(original)
            orig_all = orig_critical + orig_noncritical
            if orig_all:
                comp_all = CRITICAL_PATTERN.findall(compressed) + NON_CRITICAL_PATTERN.findall(compressed)
                retained = set(orig_all) & set(comp_all)
                retention_ratio = len(retained) / len(orig_all)
                if retention_ratio < entity_retention:
                    logger.info(
                        "compress 质量过滤 [%d]: 实体保留率 %.0f%% < %.0f%%",
                        idx, retention_ratio * 100, entity_retention * 100,
                    )
                    continue

        if len(compressed) >= len(original) * 0.95:
            logger.info(
                "compress 质量过滤 [%d]: 几乎未压缩 (%d→%d)",
                idx, len(original), len(compressed),
            )
            continue

        if source == "USER":
            orig_kw = _chinese_bigrams(original)
            comp_kw = _chinese_bigrams(compressed)
        else:
            ALL_KW_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}")
            orig_kw = set(ALL_KW_PATTERN.findall(original))
            comp_kw = set(ALL_KW_PATTERN.findall(compressed))
        if orig_kw and comp_kw:
            kw_overlap = len(orig_kw & comp_kw) / max(len(orig_kw), 1)
            if kw_overlap < kw_threshold:
                logger.info(
                    "compress 质量过滤 [%d]: 关键词重叠过低 %.2f < %.2f", idx, kw_overlap, kw_threshold
                )
                continue

        if strict_mode:
            orig_dates = _extract_dates(original)
            if orig_dates:
                comp_dates = _extract_dates(compressed)
                missing_dates = orig_dates - comp_dates
                if missing_dates:
                    logger.info(
                        "compress 质量过滤 [%d]: 遗漏日期 %s", idx, missing_dates
                    )
                    continue

            orig_numbers = _extract_key_numbers(original)
            if orig_numbers:
                comp_numbers = _extract_key_numbers(compressed)
                missing_numbers = orig_numbers - comp_numbers
                if missing_numbers:
                    logger.info(
                        "compress 质量过滤 [%d]: 遗漏关键数字 %s", idx, missing_numbers
                    )
                    continue

            orig_nouns = _extract_proper_nouns(original)
            if orig_nouns:
                comp_nouns = _extract_proper_nouns(compressed)
                missing_nouns = orig_nouns - comp_nouns
                if missing_nouns:
                    logger.info(
                        "compress 质量过滤 [%d]: 遗漏专有名词 %s", idx, missing_nouns
                    )
                    continue

        passed.append(c)

    return passed


def extract_hindsight_keywords(text: str, max_count: int = 5) -> list[str]:
    """从条目中提取关键词，用于 hindsight 回填。

    提取策略（优先级从高到低）：
    1. 英文专有名词（大写开头，长度 >= 4）
    2. 中文 2-词序列（[\u4e00-\u9fff]{2,}）
    3. 英文单词（[a-zA-Z]{4,}）

    最终去重并限制数量。

    Args:
        text: 条目原文
        max_count: 最大关键词数量（3-8）

    Returns:
        关键词列表
    """
    keywords: list[str] = []
    seen: set[str] = set()

    def _add(words: list[str]) -> None:
        for w in words:
            w = w.strip()
            if not w or len(w) < 2:
                continue
            norm = w.lower()
            if norm in seen:
                continue
            seen.add(norm)
            keywords.append(w)

    proper_nouns = list(_extract_proper_nouns(text))
    _add(proper_nouns)

    cn_words = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    _add(cn_words)

    en_words = re.findall(r"[a-zA-Z]{4,}", text)
    _add(en_words)

    max_count = max(3, min(8, max_count))
    return keywords[:max_count]


def backfill_hindsight_keywords(
    entries: list[str],
    hindsight_list: list[dict[str, Any]],
    keyword_count: int = 5,
) -> list[dict[str, Any]]:
    """为 hindsight 条目回填关键词标签。

    对缺少关键词字段或关键词数量不足的条目，使用规则提取补充。
    已有的 LLM 生成关键词优先保留，仅在不足时补齐。

    Args:
        entries: 原始条目列表
        hindsight_list: hindsight 分类结果列表
        keyword_count: 每个条目的目标关键词数量

    Returns:
        回填后的 hindsight 列表（原地修改并返回）
    """
    for h in hindsight_list:
        idx = h.get("index", -1)
        if idx < 0 or idx >= len(entries):
            continue

        existing_tags = h.get("关键词", [])
        if not isinstance(existing_tags, list):
            existing_tags = []
        existing_tags = [t for t in existing_tags if t and isinstance(t, str)]

        if len(existing_tags) >= keyword_count:
            continue

        text = entries[idx]
        extracted = extract_hindsight_keywords(text, max_count=keyword_count)

        merged: list[str] = []
        seen: set[str] = set()
        for t in existing_tags + extracted:
            norm = t.lower()
            if norm not in seen:
                seen.add(norm)
                merged.append(t)
            if len(merged) >= keyword_count:
                break

        h["关键词"] = merged

    return hindsight_list


def validate_hindsight_quality(
    entries: list[str], hindsight_list: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """校验 hindsight 迁移质量 —— 确保条目适合迁到 Hindsight。

    质量规则：
    - index 合法
    - "关键词"字段存在且列表非空（至少 1 个标签）
    - 条目长度 ≥ 20 字符（过短无 hindsight 意义）

    Returns:
        仅保留通过校验的 hindsight 条目
    """
    passed: list[dict[str, Any]] = []
    for h in hindsight_list:
        idx = h.get("index", -1)
        if idx < 0 or idx >= len(entries):
            continue

        tags = h.get("关键词", [])
        if not tags or not isinstance(tags, list):
            logger.info("hindsight 质量过滤 [%d]: 缺少有效关键词标签", idx)
            continue
        # tags 列表中至少有一个非空字符串
        valid_tags = [t for t in tags if t and isinstance(t, str)]
        if len(valid_tags) < 1:
            logger.info("hindsight 质量过滤 [%d]: 关键词标签为空", idx)
            continue

        original = entries[idx]
        if len(original) < 20:
            logger.info("hindsight 质量过滤 [%d]: 条目过短 (%d < 20)", idx, len(original))
            continue

        passed.append(h)

    return passed
