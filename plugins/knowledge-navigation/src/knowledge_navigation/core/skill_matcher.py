"""LLM skill 检索器：关键词预筛选 → LLM 精排两级架构。

在 pre_llm_call 中根据用户消息选择相关 skill 并注入完整正文。

两级架构：
  Stage 1: 关键词预筛选（345 → Top-20，<1ms）
  Stage 2: LLM 语义精排（20 → Top-3，~500ms）
  比全量 LLM 匹配节省 ~85% token，延迟降低 ~50%。"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from knowledge_navigation.config import CONFIG

logger = logging.getLogger(__name__)

SKILLS_HOME = Path.home() / ".hermes" / "skills"
_TOP_K = 3
_MAX_SKILLS = 500
_PRESCREEN_TOP_K = 20

_LLM_TIMEOUT = 15

_STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "他", "她", "它", "们", "什么", "怎么", "为什么", "如何",
    "是的", "不是", "可以", "可能", "应该", "需要", "知道", "这个", "那个",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as", "into",
    "through", "during", "before", "after", "above", "below", "between",
    "and", "but", "or", "nor", "not", "so", "yet", "both", "either", "neither",
    "each", "every", "all", "any", "few", "more", "most", "other", "some",
    "such", "no", "only", "own", "same", "than", "too", "very",
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves",
    "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself", "she", "her", "hers", "herself",
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves",
    "this", "that", "these", "those",
    "what", "which", "who", "whom", "whose", "where", "when", "how",
    "if", "then", "else", "than", "because", "as", "until", "while",
    "about", "against", "between", "through", "during", "before", "after",
}

# ── 模块级缓存 ──
_skill_index: dict[str, dict[str, Any]] | None = None
"""
{skill_path: {name, description, path, category, mtime}}
使用文件路径作为 key，便于增量更新时快速查找。
"""


def _get_skill_list() -> list[dict[str, Any]]:
    """将 dict 格式的索引转换为 list，保持向后兼容。"""
    if _skill_index is None:
        return []
    if isinstance(_skill_index, list):
        return _skill_index
    return list(_skill_index.values())


def _load_skill_file(fp: Path) -> dict[str, Any] | None:
    """加载单个 SKILL.md 文件，返回 skill 数据字典。

    Returns:
        包含 name, description, path, category, mtime 的 dict，
        如果文件无效则返回 None。
    """
    try:
        stat = fp.stat()
        text = fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    meta = _parse_frontmatter(text)
    name = meta.get("name", "")
    desc = meta.get("description", "")
    if not name or not desc:
        return None

    category = fp.parent.parent.name if fp.parent.parent != SKILLS_HOME else ""

    return {
        "name": name,
        "description": desc,
        "path": str(fp),
        "category": category,
        "mtime": stat.st_mtime,
    }


# ====================================================================
# 索引构建（首次调用时懒加载，后续增量更新）
# ====================================================================

def ensure_index() -> bool:
    """构建/更新 skill 索引。

    - 首次调用：全量扫描，构建索引
    - 后续调用（增量模式）：检查 mtime，只更新有变化的文件
    - 后续调用（非增量模式）：直接返回缓存

    Returns:
        索引是否非空
    """
    global _skill_index

    incremental = CONFIG.skill_index_incremental

    if _skill_index is not None and not incremental:
        if isinstance(_skill_index, list):
            return len(_skill_index) > 0
        return len(_skill_index) > 0

    if not SKILLS_HOME.exists():
        logger.warning("Skill index: skills dir not found: %s", SKILLS_HOME)
        _skill_index = {}
        return False

    if _skill_index is None or isinstance(_skill_index, list):
        return _build_full_index()

    return _update_incremental()


def _build_full_index() -> bool:
    """全量扫描构建 skill 索引。"""
    global _skill_index

    t0 = time.time()
    skill_files = list(SKILLS_HOME.rglob("SKILL.md"))
    if len(skill_files) > _MAX_SKILLS:
        skill_files = skill_files[:_MAX_SKILLS]

    index: dict[str, dict[str, Any]] = {}
    n_skipped = 0

    for fp in skill_files:
        skill_data = _load_skill_file(fp)
        if skill_data is None:
            n_skipped += 1
            continue
        index[str(fp)] = skill_data

    _skill_index = index

    elapsed = (time.time() - t0) * 1000
    logger.info(
        "Skill index built (full): %d indexed, %d skipped in %.0fms",
        len(index), n_skipped, elapsed,
    )
    return len(index) > 0


def _update_incremental() -> bool:
    """增量更新 skill 索引：检查 mtime，只处理有变化的文件。"""
    global _skill_index

    if _skill_index is None:
        return _build_full_index()

    t0 = time.time()
    skill_files = list(SKILLS_HOME.rglob("SKILL.md"))
    if len(skill_files) > _MAX_SKILLS:
        skill_files = skill_files[:_MAX_SKILLS]

    current_paths = {str(fp) for fp in skill_files}
    indexed_paths = set(_skill_index.keys())

    added = 0
    updated = 0
    removed = 0
    n_skipped = 0

    for fp in skill_files:
        path_str = str(fp)
        try:
            stat = fp.stat()
        except Exception:
            continue

        if path_str not in _skill_index:
            skill_data = _load_skill_file(fp)
            if skill_data is None:
                n_skipped += 1
                continue
            _skill_index[path_str] = skill_data
            added += 1
        else:
            if stat.st_mtime > _skill_index[path_str]["mtime"]:
                skill_data = _load_skill_file(fp)
                if skill_data is None:
                    del _skill_index[path_str]
                    removed += 1
                    n_skipped += 1
                    continue
                _skill_index[path_str] = skill_data
                updated += 1

    for path_str in indexed_paths - current_paths:
        del _skill_index[path_str]
        removed += 1

    elapsed = (time.time() - t0) * 1000
    if added > 0 or updated > 0 or removed > 0:
        logger.info(
            "Skill index updated (incremental): %d total, +%d added, ~%d updated, -%d removed in %.0fms",
            len(_skill_index), added, updated, removed, elapsed,
        )
    else:
        logger.debug("Skill index unchanged (incremental check in %.0fms)", elapsed)

    return len(_skill_index) > 0


def rebuild_skill_index() -> bool:
    """强制重建 skill 索引（全量扫描）。

    用于手动刷新索引，清除增量缓存。
    """
    global _skill_index
    _skill_index = None
    return ensure_index()

def _parse_frontmatter(text: str) -> dict[str, str]:
    """从 SKILL.md 中提取 name / description。"""
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta
    end = text.find("\n---\n", 3)
    if end == -1:
        return meta
    body = text[3:end]
    import re as _re
    for line in body.strip().split("\n"):
        m = _re.match(r"^(name|description):\s*(.+)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta


def strip_frontmatter(text: str) -> str:
    """去除 SKILL.md 开头的 YAML frontmatter，返回正文。

    与 _parse_frontmatter 共用同一套分隔逻辑，避免 hooks/skill_matcher
    两处重复实现 frontmatter 解析。
    """
    if not text.startswith("---"):
        return text
    end = text.find("\n---\n", 3)
    if end == -1:
        return text
    return text[end + 5:].lstrip("\n")


# ====================================================================
# Stage 1: 关键词预筛选
# ====================================================================

def _extract_keywords(text: str) -> set[str]:
    """从文本中提取关键词（中英文混合）。

    - 英文：按非字母数字分割，转小写，过滤停用词和长度 < 2 的
    - 中文：提取连续 2+ 汉字的片段（简单 n-gram）
    """
    keywords: set[str] = set()

    # 英文单词
    en_words = re.findall(r'[a-zA-Z][a-zA-Z0-9_\-]+', text.lower())
    for w in en_words:
        if len(w) >= 2 and w not in _STOPWORDS:
            keywords.add(w)

    # 中文片段（2+ 连续汉字）
    zh_segments = re.findall(r'[\u4e00-\u9fff]{2,}', text)
    for seg in zh_segments:
        # 整段作为关键词
        if seg not in _STOPWORDS:
            keywords.add(seg)
        # 2-gram 子串（增加召回）
        for i in range(len(seg) - 1):
            gram = seg[i:i+2]
            if gram not in _STOPWORDS:
                keywords.add(gram)

    return keywords


def _keyword_prescreen(
    query: str,
    index: list[dict[str, Any]],
    top_k: int = _PRESCREEN_TOP_K,
) -> list[dict[str, Any]]:
    """关键词预筛选：从全量 skill 中快速选出 top_k 个候选。

    评分规则：
    - name 完全匹配：+10
    - name 关键词重叠：每个 +5
    - category 关键词重叠：每个 +3
    - description 关键词重叠：每个 +1

    Args:
        query: 用户查询
        index: skill 索引列表
        top_k: 返回候选数量

    Returns:
        按得分降序排列的 top_k 个 skill，每个 skill 附带 _score 字段
    """
    if not index:
        return []

    query_keywords = _extract_keywords(query)
    if not query_keywords:
        result = []
        for s in sorted(index, key=lambda x: x["name"])[:top_k]:
            s_copy = dict(s)
            s_copy["_score"] = 0.0
            result.append(s_copy)
        return result

    scored: list[tuple[float, dict[str, Any]]] = []
    query_lower = query.lower()

    for skill in index:
        if skill.get("category") == ".archive":
            continue

        name = skill.get("name", "")
        desc = skill.get("description", "")
        category = skill.get("category", "")
        name_lower = name.lower()
        score = 0.0

        # Name 精确匹配
        if name_lower == query_lower:
            score += 10

        # Name 关键词重叠
        name_keywords = _extract_keywords(name)
        name_overlap = query_keywords & name_keywords
        score += len(name_overlap) * 5.0

        # Category 关键词重叠
        cat_keywords = _extract_keywords(category)
        cat_overlap = query_keywords & cat_keywords
        score += len(cat_overlap) * 3.0

        # Description 关键词重叠
        desc_keywords = _extract_keywords(desc)
        desc_overlap = query_keywords & desc_keywords
        score += len(desc_overlap) * 1.0

        if score > 0:
            scored.append((score, skill))

    # 按得分降序，得分相同按 name 字母序
    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    result = []
    for score, skill in scored[:top_k]:
        s_copy = dict(skill)
        s_copy["_score"] = score
        result.append(s_copy)
    return result


# ====================================================================
# Stage 2: LLM 精排
# ====================================================================

def _build_skill_prompt(index: list[dict[str, Any]]) -> str:
    """将 skill 索引格式化为 LLM 可读的列表。

    按名称字母序排列。每行 name + 描述（截断到 120 字），过滤归档。
    """
    lines: list[str] = []
    for s in sorted(index, key=lambda x: x["name"]):
        if s.get("category") == ".archive":
            continue
        desc = s.get("description", "")
        desc_trunc = (desc[:120] + "...") if len(desc) > 120 else desc
        lines.append(f"- {s['name']}: {desc_trunc}")
    return "\n".join(lines)


def _llm_match(
    query: str,
    top_k: int = _TOP_K,
    candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """LLM 语义精排。从候选中选出 top_k 个技能。

    Args:
        query: 用户查询
        top_k: 最多返回数量
        candidates: 候选 skill 列表（预筛选结果），None 表示用全量索引
    """
    if not _skill_index:
        return []

    skill_list = _get_skill_list()
    pool = candidates if candidates is not None else skill_list
    if not pool:
        return []

    skill_text = _build_skill_prompt(pool)
    prompt = (
        "你是一个技能选择器。你的任务是根据用户问题，从给定的技能列表中选出 1-3 个最可能解决问题的技能，并以 JSON 数组形式返回技能名称。\n\n"
        "请严格遵循以下规则：\n\n"
        "## 分析流程\n"
        "1. **意图解析**：阅读用户问题，提炼出核心任务、领域、工具或痛点，形成 3-5 个关键概念。\n"
        "2. **概念扩展（必须补齐中、英文）**：针对每个关键概念，生成 1-2 组同义词、近义词、上位词、下位词或强关联词汇，每组必须同时包含中文和英文表达。\n"
        "3. **技能匹配**：\n"
        "   - 优先按技能名称（name）直接匹配——name 是有意义的关键词标识符\n"
        "   - 如果 name 无直接匹配，再用扩展出的中英文词表逐一对照技能描述（description）中的核心术语\n"
        "   - 只选明确能解决或显著辅助用户问题的技能\n"
        "   - 宁可少选不可错选——没有足够相关的技能时返回 []\n\n"
        "## 输出要求\n"
        "- 仅输出一个合法的 JSON 数组，元素为技能名称字符串，不要用 ``` 包裹或附带任何其他文字\n"
        "- 技能名称必须与可用技能列表中的名称完全一致（含大小写、连字符、符号）\n"
        "- 数量控制在 1-3 个；如果无相关技能，返回 []\n"
        "- 每次对同一问题返回一致的结果\n\n"
        "## 可用技能列表\n"
        + skill_text + "\n\n"
        "## 用户问题\n"
        + query + "\n\n"
        "## 你的输出（仅 JSON）\n"
    )

    for attempt in range(2):
        try:
            import httpx
            api_key = os.environ.get("LITELLM_MASTER_KEY", "")
            resp = httpx.post(
                "http://127.0.0.1:4142/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={
                    "model": "s-deepseek-v4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.1,
                    "extra_body": {"thinking": {"type": "disabled"}},
                },
                timeout=_LLM_TIMEOUT,
            )
            resp.raise_for_status()
            body = resp.json()
            raw = (body["choices"][0]["message"]["content"] or "").strip()

            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            names = json.loads(raw)
            if not isinstance(names, list):
                logger.debug("Skill match LLM: non-list: %s", raw)
                return []

            info_map = {s["name"]: {"description": s["description"], "path": s["path"]} for s in skill_list}
            results: list[dict[str, str]] = []
            for name in names[:top_k]:
                if name in info_map:
                    prescreen_score = 0.0
                    if candidates:
                        for c in candidates:
                            if c["name"] == name:
                                prescreen_score = c.get("_score", 0.0)
                                break
                    final_score = 0.5 + prescreen_score * 0.01
                    results.append({
                        "name": name,
                        "description": info_map[name]["description"],
                        "path": info_map[name]["path"],
                        "score": f"{final_score:.3f}",
                    })
            return results

        except Exception as e:
            if attempt == 0:
                logger.debug("Skill match LLM 重试 (attempt=0): %s", e)
                continue
            logger.debug("Skill match LLM error: %s", e)
            return []


# ====================================================================
# 入口
# ====================================================================

def match_skills(
    query: str,
    top_k: int = _TOP_K,
    enable_keyword_prescreen: bool = True,
) -> list[dict[str, str]]:
    """技能匹配：关键词预筛选 → LLM 精排 两级架构。

    两级架构：
      Stage 1: 关键词预筛选（345 → Top-20，<1ms）
      Stage 2: LLM 语义精排（20 → Top-3，~500ms）
    比全量 LLM 匹配节省 ~85% token，延迟降低 ~50%。

    Args:
        query: 用户消息
        top_k: 最多返回数量
        enable_keyword_prescreen: 是否启用关键词预筛选（Feature Flag）

    Returns:
        [{name, description, score, path}, ...]
        调用方可用 path + strip_frontmatter 读 SKILL.md 正文。
    """
    if not ensure_index():
        return []

    # 空查询 / 纯空白 → 无匹配
    if not query or not query.strip():
        return []

    t0 = time.time()

    skill_list = _get_skill_list()

    if enable_keyword_prescreen and skill_list:
        # 两级模式：关键词预筛选 → LLM 精排
        prescreen_start = time.time()
        candidates = _keyword_prescreen(query, skill_list)
        prescreen_ms = (time.time() - prescreen_start) * 1000

        if not candidates:
            logger.debug("Skill match: empty (keyword prescreen returned nothing)")
            return []

        results = _llm_match(query, top_k, candidates=candidates)
        total_ms = (time.time() - t0) * 1000

        if results:
            logger.info(
                "Skill match (2-stage): prescreen=%d candidates, matched=%s (prescreen=%.0fms, total=%.0fms)",
                len(candidates),
                [r["name"] for r in results],
                prescreen_ms,
                total_ms,
            )
            return results

        logger.debug("Skill match: empty (2-stage LLM returned nothing)")
        return []
    else:
        # 单级模式：全量 LLM 匹配（兼容旧行为）
        results = _llm_match(query, top_k)
        if results:
            elapsed = (time.time() - t0) * 1000
            logger.info(
                "Skill match (LLM full): %s (%.0fms)",
                [r["name"] for r in results],
                elapsed,
            )
            return results

        logger.debug("Skill match: empty (LLM returned nothing)")
        return []