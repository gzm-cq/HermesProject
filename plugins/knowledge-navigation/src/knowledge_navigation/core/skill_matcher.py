"""LLM skill 检索器：全量 LLM 匹配。

在 pre_llm_call 中根据用户消息选择相关 skill 并注入完整正文。

直接发送全部 skill 给 LLM 做语义匹配，不使用预筛选。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from knowledge_navigation.config import CONFIG
from knowledge_navigation.core.env_loader import get_env, get_env_int

logger = logging.getLogger(__name__)

SKILLS_HOME = Path.home() / ".hermes" / "skills"


# ══════════════════════════════════════════════════════════════════
# 可调参数：运行期读取（支持热更新）
# ══════════════════════════════════════════════════════════════════
# 以下 accessor 在**每次调用时**经 get_env_int() 读取，配合 env_loader 的
# 60s TTL 缓存，修改 .env 中的 KN_SKILL_* 后最多 60s 自动生效，无需重启 gateway。
#
# 运行时代码一律使用 _get_xxx() accessor；下方的模块级常量仅为「导入期快照」，
# 保留用于向后兼容（测试与外部引用），不参与运行时决策。
#
# 所有参数统一 clamp 到 >= 1：误配 0 或负数时回退默认值，
# 避免 range(step=0) 抛 ValueError、或切片 [:0] 静默返回空结果。

def _clamp_positive(value: int, fallback: int) -> int:
    """调优参数保护：非正数视为误配，回退默认值。"""
    return value if value >= 1 else fallback


def _get_top_k() -> int:
    """LLM 精排最终返回的 skill 数量。"""
    return _clamp_positive(get_env_int("KN_SKILL_TOP_K", 3), 3)


def _get_max_skills() -> int:
    """建索引时扫描的 SKILL.md 数量上限。"""
    return _clamp_positive(get_env_int("KN_SKILL_MAX_SKILLS", 500), 500)


def _get_prescreen_top_k() -> int:
    """关键词预筛保留的候选数量。"""
    return _clamp_positive(get_env_int("KN_SKILL_PRESCREEN_TOP_K", 30), 30)


def _get_embedding_top_k() -> int:
    """Embedding 预筛保留的候选数量。"""
    return _clamp_positive(get_env_int("KN_SKILL_EMBEDDING_TOP_K", 20), 20)


def _get_embedding_batch_size() -> int:
    """调用 embedding API 的单批文本数量。"""
    return _clamp_positive(get_env_int("KN_SKILL_EMBEDDING_BATCH_SIZE", 20), 20)


def _get_llm_timeout() -> int:
    """LLM 精排请求超时（秒）。

    默认 45s：keyword-prescreen 后 prompt 约 30 项，但预筛本身需 embedding 时间。
    """
    return _clamp_positive(get_env_int("KN_SKILL_MATCH_TIMEOUT", 45), 45)


# ── 导入期快照（向后兼容，勿用于运行时决策）──
_TOP_K = _get_top_k()
_MAX_SKILLS = _get_max_skills()
_PRESCREEN_TOP_K = _get_prescreen_top_k()
_EMBEDDING_TOP_K = _get_embedding_top_k()
_EMBEDDING_BATCH_SIZE = _get_embedding_batch_size()
_LLM_TIMEOUT = _get_llm_timeout()

_STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "他", "她", "它", "们", "什么", "怎么", "为什么", "如何",
    "是的", "不是", "可以", "可能", "应该", "需要", "知道", "这个", "那个",
    "但", "还", "或", "如果", "因为", "所以", "但是", "而且", "不过", "只是",
    "啊", "呢", "吗", "吧", "哦", "嗯", "呀", "啦", "呗", "喽",
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
    "just", "up", "out", "off", "away", "back", "down", "over", "under",
    "don", "now", "get", "got", "go", "going", "make", "made", "like",
}

# ── 模块级缓存 ──
_skill_index: dict[str, dict[str, Any]] | None = None
"""
{skill_path: {name, description, path, category, mtime}}
使用文件路径作为 key，便于增量更新时快速查找。
"""

# ── Embedding 缓存（LRU 淘汰，防止内存泄漏） ──
_EMBEDDING_CACHE_MAX = 512  # skill embedding 缓存上限
_QUERY_EMBEDDING_CACHE_MAX = 256  # query embedding 缓存上限
_embedding_cache: OrderedDict[str, np.ndarray] = OrderedDict()  # skill_path → embedding vector
_query_embedding_cache: OrderedDict[str, tuple[float, np.ndarray]] = OrderedDict()  # query → (timestamp, embedding)

# ── Embedding 熔断机制（线程安全） ──
_embedding_fail_count: int = 0  # 连续失败次数
_embedding_circuit_open_until: float = 0.0  # 熔断截止时间戳
_embedding_lock: threading.Lock = threading.Lock()  # 并发保护
_EMBEDDING_CIRCUIT_BREAK_THRESHOLD = 3  # 连续失败阈值
_EMBEDDING_CIRCUIT_COOLDOWN = 300  # 熔断冷却时间（秒），5 分钟

# ── 全局缓存锁 ──
_index_lock: threading.Lock = threading.Lock()  # _skill_index 并发保护
_embedding_cache_lock: threading.Lock = threading.Lock()  # _embedding_cache 并发保护
_query_embedding_cache_lock: threading.Lock = threading.Lock()  # _query_embedding_cache 并发保护


def _embedding_circuit_breaker() -> bool:
    """检查 embedding 熔断是否触发。返回 True 表示应跳过 embedding 阶段。"""
    global _embedding_fail_count, _embedding_circuit_open_until
    with _embedding_lock:
        if _embedding_circuit_open_until > 0 and time.time() < _embedding_circuit_open_until:
            return True
        if _embedding_circuit_open_until > 0 and time.time() >= _embedding_circuit_open_until:
            _embedding_fail_count = 0
            _embedding_circuit_open_until = 0.0
    return False


def _embedding_record_success() -> None:
    """记录 embedding 调用成功，重置失败计数。"""
    global _embedding_fail_count, _embedding_circuit_open_until
    with _embedding_lock:
        _embedding_fail_count = 0
        _embedding_circuit_open_until = 0.0


def _embedding_record_failure() -> None:
    """记录 embedding 调用失败，增加计数；达到阈值触发熔断。"""
    global _embedding_fail_count, _embedding_circuit_open_until
    with _embedding_lock:
        _embedding_fail_count += 1
        if _embedding_fail_count >= _EMBEDDING_CIRCUIT_BREAK_THRESHOLD:
            _embedding_circuit_open_until = time.time() + _EMBEDDING_CIRCUIT_COOLDOWN
            logger.warning(
                "Embedding 连续失败 %d 次，触发熔断，%.0f 秒后恢复",
                _EMBEDDING_CIRCUIT_BREAK_THRESHOLD,
                _EMBEDDING_CIRCUIT_COOLDOWN,
            )


_QUERY_EMBEDDING_CACHE_TTL = 1800  # query embedding 缓存 TTL（秒），30 分钟


def _get_embedding_config() -> tuple[str, str, str, int]:
    """获取 embedding 配置。

    运行时从环境变量动态 fallback，绕过 CONFIG 模块级单例在 import 时的
    env 未就绪问题（benchmark 进程 / 子 agent 场景）。
    优先级：ENV > CONFIG 默认值。
    """
    model = get_env("KN_SKILL_EMBEDDING_MODEL") or CONFIG.kn_skill_embedding_model
    url = get_env("KN_SKILL_EMBEDDING_URL") or CONFIG.kn_skill_embedding_url
    api_key = get_env("KN_SKILL_EMBEDDING_API_KEY") or CONFIG.kn_skill_embedding_api_key
    # 也 fallback SILICONFLOW_API_KEY
    if not api_key:
        api_key = get_env("SILICONFLOW_API_KEY", "")
    cfg_top_k = get_env("KN_SKILL_EMBEDDING_TOP_K")
    if cfg_top_k:
        cfg_top_k = int(cfg_top_k)
    else:
        cfg_top_k = CONFIG.kn_skill_embedding_top_k or _get_embedding_top_k()
    return (model, url, api_key, cfg_top_k)


def _get_query_embedding(query: str, model: str, url: str, api_key: str) -> np.ndarray | None:
    """获取 query 的 embedding，带缓存（TTL 30 分钟，LRU 淘汰）。"""
    global _query_embedding_cache

    now = time.time()
    with _query_embedding_cache_lock:
        if query in _query_embedding_cache:
            ts, emb = _query_embedding_cache[query]
            if now - ts < _QUERY_EMBEDDING_CACHE_TTL:
                _query_embedding_cache.move_to_end(query)
                return emb
            else:
                del _query_embedding_cache[query]

    try:
        import httpx
        resp = httpx.post(
            f"{url.rstrip('/')}/embeddings",
            json={"model": model, "input": query[:1000]},
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        embedding = np.array(data["data"][0]["embedding"], dtype=np.float32)
        with _query_embedding_cache_lock:
            _query_embedding_cache[query] = (now, embedding)
            while len(_query_embedding_cache) > _QUERY_EMBEDDING_CACHE_MAX:
                _query_embedding_cache.popitem(last=False)
        return embedding
    except Exception as e:
        logger.debug("Embedding API 调用失败: %s", e)
        return None


def _get_skill_embeddings(skills: list[dict[str, Any]], model: str, url: str, api_key: str) -> dict[str, np.ndarray]:
    """获取 skill 列表的 embedding，只对缺失的进行 API 调用，分批避免 token 超限。"""
    global _embedding_cache

    result = {}
    texts_to_fetch: list[tuple[str, str]] = []

    with _embedding_cache_lock:
        for skill in skills:
            path = skill.get("path", "")
            if path in _embedding_cache:
                result[path] = _embedding_cache[path]
                _embedding_cache.move_to_end(path)
            else:
                text = f"{skill.get('name', '')} {skill.get('description', '')}"
                texts_to_fetch.append((path, text))

    if not texts_to_fetch:
        return result

    import httpx

    batch_size = _get_embedding_batch_size()
    for i in range(0, len(texts_to_fetch), batch_size):
        batch = texts_to_fetch[i:i + batch_size]
        paths = [p for p, _ in batch]
        texts = [t for _, t in batch]

        try:
            resp = httpx.post(
                f"{url.rstrip('/')}/embeddings",
                json={"model": model, "input": texts},
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            with _embedding_cache_lock:
                for item in data.get("data", []):
                    idx = item.get("index", 0)
                    if 0 <= idx < len(paths):
                        path = paths[idx]
                        emb = np.array(item["embedding"], dtype=np.float32)
                        _embedding_cache[path] = emb
                        result[path] = emb
                while len(_embedding_cache) > _EMBEDDING_CACHE_MAX:
                    _embedding_cache.popitem(last=False)
        except Exception as e:
            logger.debug("Embedding 批量获取失败 (batch %d-%d): %s", i, i + len(batch), e)

    return result


def _embedding_prescreen(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Embedding 相似度预筛选：从候选中选出 top_k 个最相似的 skill。

    Args:
        query: 用户查询
        candidates: 关键词预筛选结果
        top_k: 返回候选数量；None 表示运行期读取 KN_SKILL_EMBEDDING_TOP_K

    Returns:
        按 embedding 相似度降序排列的 top_k 个 skill
    """
    if top_k is None:
        top_k = _get_embedding_top_k()

    if not candidates:
        return []

    # 熔断检查：连续失败过多时直接跳过
    if _embedding_circuit_breaker():
        logger.debug("Embedding 预筛选: 熔断中，跳过 embedding 阶段")
        return candidates[:top_k]

    model, url, api_key, cfg_top_k = _get_embedding_config()
    top_k = min(top_k, cfg_top_k)

    # API key 为空时直接跳过，避免无效调用
    if not api_key:
        logger.debug("Embedding 预筛选: API key 为空，跳过 embedding 阶段")
        return candidates[:top_k]

    # 获取 query embedding
    query_emb = _get_query_embedding(query, model, url, api_key)
    if query_emb is None:
        logger.debug("Embedding 预筛选: query embedding 获取失败，返回关键词结果")
        _embedding_record_failure()
        return candidates[:top_k]

    # 获取候选 skill embedding
    skill_embs = _get_skill_embeddings(candidates, model, url, api_key)

    # 计算相似度并排序（query_norm 外提，避免重复计算）
    query_norm = np.linalg.norm(query_emb)
    scored: list[tuple[float, dict[str, Any]]] = []
    for skill in candidates:
        path = skill.get("path", "")
        emb = skill_embs.get(path)
        if emb is not None:
            sim = float(np.dot(query_emb, emb) / (query_norm * np.linalg.norm(emb) + 1e-10))
            skill_copy = dict(skill)
            skill_copy["_emb_score"] = sim
            scored.append((sim, skill_copy))

    scored.sort(key=lambda x: -x[0])
    if not scored:
        logger.debug("Embedding 预筛选: 无有效 embedding，降级返回关键词结果")
        _embedding_record_failure()
        return candidates[:top_k]

    _embedding_record_success()
    return [s for _, s in scored[:top_k]]


def _get_skill_list() -> list[dict[str, Any]]:
    """将 dict 格式的索引转换为 list，保持向后兼容。"""
    with _index_lock:
        if _skill_index is None:
            return []
        if isinstance(_skill_index, list):
            return _skill_index.copy()
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

    if meta.get("archived", "").strip().lower() in ("true", "yes", "1"):
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

    with _index_lock:
        if _skill_index is not None and not incremental:
            if isinstance(_skill_index, list):
                return len(_skill_index) > 0
            return len(_skill_index) > 0

        if not SKILLS_HOME.exists():
            logger.warning("Skill index: skills dir not found: %s", SKILLS_HOME)
            _skill_index = {}
            return False

        if _skill_index is None or isinstance(_skill_index, list):
            return _build_full_index_locked()

        return _update_incremental_locked()


def _build_full_index() -> bool:
    """全量扫描构建 skill 索引（外部调用，带锁）。"""
    with _index_lock:
        return _build_full_index_locked()


def _build_full_index_locked() -> bool:
    """全量扫描构建 skill 索引（内部调用，假设已持有锁）。"""
    global _skill_index, _embedding_cache

    t0 = time.time()
    skill_files = list(SKILLS_HOME.rglob("SKILL.md"))
    max_skills = _get_max_skills()
    if len(skill_files) > max_skills:
        skill_files = skill_files[:max_skills]

    index: dict[str, dict[str, Any]] = {}
    n_skipped = 0

    for fp in skill_files:
        skill_data = _load_skill_file(fp)
        if skill_data is None:
            n_skipped += 1
            continue
        index[str(fp)] = skill_data

    _skill_index = index
    with _embedding_cache_lock:
        _embedding_cache.clear()

    elapsed = (time.time() - t0) * 1000
    logger.info(
        "Skill index built (full): %d indexed, %d skipped in %.0fms",
        len(index), n_skipped, elapsed,
    )
    return len(index) > 0


def _update_incremental() -> bool:
    """增量更新 skill 索引：检查 mtime，只处理有变化的文件（外部调用，带锁）。"""
    with _index_lock:
        return _update_incremental_locked()


def _update_incremental_locked() -> bool:
    """增量更新 skill 索引：检查 mtime，只处理有变化的文件（内部调用，假设已持有锁）。"""
    global _skill_index, _embedding_cache

    if _skill_index is None:
        return _build_full_index_locked()

    t0 = time.time()
    skill_files = list(SKILLS_HOME.rglob("SKILL.md"))
    max_skills = _get_max_skills()
    if len(skill_files) > max_skills:
        skill_files = skill_files[:max_skills]

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
                    with _embedding_cache_lock:
                        _embedding_cache.pop(path_str, None)
                    removed += 1
                    n_skipped += 1
                    continue
                _skill_index[path_str] = skill_data
                with _embedding_cache_lock:
                    _embedding_cache.pop(path_str, None)
                updated += 1

    for path_str in indexed_paths - current_paths:
        del _skill_index[path_str]
        with _embedding_cache_lock:
            _embedding_cache.pop(path_str, None)
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
    with _index_lock:
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
        m = _re.match(r"^(name|description|archived):\s*(.+)$", line)
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

    委托 core.text_utils.extract_keywords，skill 预筛选激进配置：
    - 英文 >=2 字符
    - CJK 连续段 + 2-gram 子串
    - 额外过滤 skill 场景专用停用词表 _STOPWORDS
    """
    from hermes_common.text_utils import extract_keywords as _tu_extract
    raw = _tu_extract(
        text,
        min_en_length=2,
        include_cjk_bigrams=True,
        include_cjk_full=True,
    )
    return {k for k in raw if k not in _STOPWORDS}


def _keyword_prescreen(
    query: str,
    index: list[dict[str, Any]],
    top_k: int | None = None,
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
        top_k: 返回候选数量；None 表示运行期读取 KN_SKILL_PRESCREEN_TOP_K

    Returns:
        按得分降序排列的 top_k 个 skill，每个 skill 附带 _score 字段
    """
    if top_k is None:
        top_k = _get_prescreen_top_k()

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
    top_k: int | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """LLM 语义精排。从候选中选出 top_k 个技能。

    Args:
        query: 用户查询
        top_k: 最多返回数量；None 表示运行期读取 KN_SKILL_TOP_K
        candidates: 候选 skill 列表（预筛选结果），None 表示用全量索引
    """
    if top_k is None:
        top_k = _get_top_k()

    if not _skill_index:
        return []

    skill_list = _get_skill_list()
    pool = candidates if candidates is not None else skill_list
    if not pool:
        return []

    skill_text = _build_skill_prompt(pool)
    prompt = (
        "你是一个技能选择器。用户遇到了一个问题，你需要从可用技能列表中选出 1-3 个最可能帮助解决问题的技能。\n\n"
        "## 选择流程\n\n"
        "```\n"
        "用户问题\n"
        "  |\n"
        "  +- 问题中是否包含具体工具/产品名？(如 LiteLLM, Docker, PostgreSQL, 飞书)\n"
        "  |   +- 是 -> 在技能列表中搜索包含该名称或其同义词的技能 -> 候选集 A\n"
        "  |   +- 否 -> 提取核心任务概念(如 部署 调试 配置) -> 候选集 A\n"
        "  |\n"
        "  +- 问题是否描述了一个工作流程？(如 怎么部署 如何配置 审查流程)\n"
        "  |   +- 是 -> 在技能描述中搜索包含该工作流关键词的技能 -> 候选集 B\n"
        "  |   +- 否 -> 候选集 B 为空\n"
        "  |\n"
        "  +- 问题是否涉及特定领域？(如 AI 安全 数据库 前端)\n"
        "  |   +- 是 -> 在技能描述中搜索该领域术语 -> 候选集 C\n"
        "  |   +- 否 -> 候选集 C 为空\n"
        "  |\n"
        "  +- 合并候选集 A B C -> 按相关性排序 -> 取 top 1-3\n"
        "```\n\n"
        "为什么用决策树：技能列表有 30-50 项，线性扫描容易遗漏。决策树帮你在不同维度上并行搜索，提高召回率。\n\n"
        "## 关键原则\n\n"
        "1. 技能名称是强信号：name 通常是技能的核心关键词(如 database-migrations git-workflow)。如果用户问题中的词与某个 skill name 直接相关，优先选它。\n"
        "2. 描述中的术语是弱信号：description 提供补充上下文。当 name 不直接匹配时，检查 description 中是否包含问题领域的术语。\n"
        "3. 宁多选不少选：如果不确定某个技能是否相关，选上它。漏选一个有用技能的代价远大于多选一个无关技能。\n\n"
        "## 示例\n\n"
        "### 示例 1(工具名匹配 + 语义关联)\n"
        "用户问题：PG 连接错误怎么排查\n"
        "可用技能列表(部分)：\n"
        "- hermes-fallback-model-troubleshooting: 修复 Hermes 模型 fallback LiteLLM 路由 PG 连接错误等问题\n"
        "- systematic-debugging: 系统化调试方法论\n"
        "- database-migrations: 安全的数据库 schema 变更模式\n"
        "- docker-patterns: Docker 和 Docker Compose 开发模式\n"
        "输出：[\"hermes-fallback-model-troubleshooting\", \"systematic-debugging\", \"database-migrations\"]\n\n"
        "### 示例 2(工作流匹配 + 领域匹配)\n"
        "用户问题：怎么部署插件\n"
        "可用技能列表(部分)：\n"
        "- land-and-deploy: 合并 PR 等待 CI 部署到生产环境的完整工作流\n"
        "- setup-deploy: 配置部署目标和策略\n"
        "- ship: 检测+合并 base branch 运行测试 review 部署\n"
        "- hermes-agent: 配置 Hermes Agent 的 CLI 模型 工具\n"
        "输出：[\"land-and-deploy\", \"setup-deploy\", \"ship\"]\n\n"
        "### 示例 3(概念关联 无直接关键词重叠)\n"
        "用户问题：用什么工具查日志\n"
        "可用技能列表(部分)：\n"
        "- system-health-check: 全栈健康检查 包含日志分析和服务状态\n"
        "- system-operations-rules: 系统运维操作规范 覆盖 Docker WSL 网络 包管理\n"
        "- collect-baseline: 采集 recall 基线数据和统计对比\n"
        "输出：[\"system-health-check\", \"system-operations-rules\"]\n\n"
        "### 示例 4(无关查询)\n"
        "用户问题：推荐一部电影\n"
        "可用技能列表：\n"
        "- database-migrations: 数据库 schema 变更\n"
        "- frontend-patterns: 前端开发模式\n"
        "输出：[]\n\n"
        "## 可用技能列表\n"
        + skill_text + "\n\n"
        "## 用户问题\n"
        + query + "\n\n"
        "## 输出(仅 JSON 数组 不要其他文字)\n"
    )

    # max_tokens=8192：适配sensenova-6.8-flash-lite thinking-heavy responses，避免 reasoning 吃掉所有 budget
    # 耗尽 token 配额导致 content 字段为空（512 在长 prompt 下经常不够用）
    # 不重试：单次失败立即返回空触发 fallback（kw+emb union top-K），
    # 避免最坏 45s × 2 = 90s 的长尾叠加（实测 p99=66s）。
    for attempt in range(1):
        try:
            import httpx
            api_key = get_env("LITELLM_MASTER_KEY", "")
            skill_url = get_env("KN_SKILL_MATCHER_API_URL") or CONFIG.skill_matcher_api_url
            skill_model = get_env("KN_SKILL_MATCHER_MODEL") or CONFIG.skill_matcher_model
            resp = httpx.post(
                f"{skill_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={
                    "model": skill_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 8192,
                    "temperature": 0.1,
                    "thinking": {"type": "disabled"},
                },
                timeout=_get_llm_timeout(),
            )
            resp.raise_for_status()
            body = resp.json()
            choice = body["choices"][0]
            msg = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")
            raw = (msg.get("content") or "").strip()

            # 兜底：content 空但 reasoning_content 非空时，从 reasoning 末尾提取 JSON 数组。
            # 触发场景：LiteLLM 降级到不支持 thinking:disabled 的推理模型（如 sensenova），
            # reasoning_content 占满 token 后 content 为空。reasoning 末尾通常会给出最终答案。
            if not raw:
                reasoning = (msg.get("reasoning_content") or "").strip()
                if reasoning:
                    # 匹配 reasoning 末尾的 JSON 数组（支持多行、带引号变体）
                    m = re.search(r'\[\s*"[^"]*"(?:\s*,\s*"[^"]*")*\s*\]\s*$', reasoning, re.MULTILINE)
                    if m:
                        raw = m.group(0)
                        logger.info(
                            "Skill match LLM: content 空，从 reasoning_content 兜底提取 %s", raw
                        )
                # 记录 length 截断告警，便于监控 LiteLLM 路由异常
                if finish_reason == "length":
                    logger.warning(
                        "Skill match LLM: finish_reason=length, content 空 (model=%s, prompt=%d chars)",
                        resp.headers.get("x-litellm-model-group", "unknown"),
                        len(prompt),
                    )

            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            names = json.loads(raw)
            if not isinstance(names, list):
                logger.debug("Skill match LLM: non-list: %s", raw)
                return []

            info_map = {s["name"]: {"description": s["description"], "path": s["path"]} for s in skill_list}
            # 大小写不敏感的名称映射，处理 LLM 返回的大小写/连字符变体
            info_map_lower = {s["name"].lower(): s["name"] for s in skill_list}
            results: list[dict[str, str]] = []
            for name in names[:top_k]:
                # 类型校验：LLM 偶发返回非字符串元素（dict/int/null），跳过避免 KeyError/TypeError
                if not isinstance(name, str):
                    continue
                # 优先精确匹配，否则尝试大小写不敏感匹配
                matched_name = name if name in info_map else info_map_lower.get(name.lower())
                if matched_name:
                    prescreen_score = 0.0
                    if candidates:
                        for c in candidates:
                            if c["name"] == matched_name:
                                kw_s = c.get("_score", 0.0)
                                emb_s = c.get("_emb_score", 0.0)
                                # 归一化到 keyword 量级：emb 0-1 × 50
                                prescreen_score = max(kw_s, emb_s * 50.0)
                                break
                    final_score = 0.5 + prescreen_score * 0.01
                    results.append({
                        "name": matched_name,
                        "description": info_map[matched_name]["description"],
                        "path": info_map[matched_name]["path"],
                        "score": f"{final_score:.3f}",
                    })
            return results

        except Exception as e:
            logger.debug("Skill match LLM error (no retry): %s", e)
            return []


# ====================================================================
# 入口
# ====================================================================

def match_skills(
    query: str,
    top_k: int | None = None,
    enable_keyword_prescreen: bool = True,
) -> list[dict[str, str]]:
    """技能匹配：关键词预筛 + Embedding 预筛 + LLM 精排。

    Stage 1 (keyword prescreen): 从全量 skill 中快速选出 top-30 候选
    Stage 1.5 (embedding prescreen): 独立从全量 skill 中选出 top-20 候选
    Stage 2 (LLM): 从 union（≤50）候选中选出 top_k 个最相关的

    三阶段管线保证 LLM prompt 大小可控，同时通过 embedding 补全关键词漏筛。

    Args:
        query: 用户消息
        top_k: 最多返回数量；None 表示运行期读取 KN_SKILL_TOP_K
        enable_keyword_prescreen: 是否启用关键词预筛（默认 True）

    Returns:
        [{name, description, score, path}, ...]
        调用方可用 path + strip_frontmatter 读 SKILL.md 正文。
    """
    if top_k is None:
        top_k = _get_top_k()

    if not ensure_index():
        return []

    if not query or not query.strip():
        return []

    t0 = time.time()
    skill_list = _get_skill_list()

    # Stage 1: 关键词预筛
    if enable_keyword_prescreen:
        kw_candidates = _keyword_prescreen(query, skill_list, top_k=_get_prescreen_top_k())
        if not kw_candidates:
            logger.debug("Skill match: keyword prescreen returned empty")
            return []
    else:
        kw_candidates = []

    # Stage 1.5: Embedding 预筛（独立全量，补全关键词漏筛）
    emb_candidates: list[dict[str, Any]] = []
    if enable_keyword_prescreen and not _embedding_circuit_breaker():
        _, _, emb_api_key, _ = _get_embedding_config()
        if emb_api_key:
            emb_candidates = _embedding_prescreen(query, skill_list, top_k=_get_embedding_top_k())
            # 降级检查：返回的候选没有 _emb_score 说明 embedding 失败，跳过
            if emb_candidates and not any("_emb_score" in c for c in emb_candidates):
                logger.debug("Skill match: embedding prescreen degraded, skipping")
                emb_candidates = []

    # Union + 去重（keyword 优先，embedding 补充未命中的）
    if enable_keyword_prescreen:
        seen_names: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for c in kw_candidates:
            name = c["name"]
            if name not in seen_names:
                seen_names.add(name)
                candidates.append(c)
        for c in emb_candidates:
            name = c["name"]
            if name not in seen_names:
                seen_names.add(name)
                candidates.append(c)

        if not candidates:
            logger.debug("Skill match: no candidates after union")
            return []

        n_kw = len(kw_candidates)
        n_emb = len(emb_candidates)
        n_union = len(candidates)
        logger.debug(
            "Skill match prescreen: keyword=%d, embedding=%d, union=%d query=%s",
            n_kw, n_emb, n_union, query[:100].replace("\n", " "),
        )
        llm_candidates: list[dict[str, Any]] | None = candidates
    else:
        llm_candidates = None
        n_union = len(skill_list)

    # Stage 2: LLM 精排
    results = _llm_match(query, top_k, candidates=llm_candidates)
    if results:
        elapsed = (time.time() - t0) * 1000
        logger.info(
            "Skill match (kw+emb+LLM): %s (%.0fms, %d→%d) query=%s",
            [r["name"] for r in results],
            elapsed,
            n_union,
            len(results),
            query[:100].replace("\n", " "),
        )
        return results

    # Fallback: LLM 返回空时，用 union top-K 兜底（仅预筛模式）
    if llm_candidates is None:
        logger.debug("Skill match: empty (LLM returned nothing, no prescreen)")
        return []

    logger.debug("Skill match: LLM returned empty, falling back to union top-%d", top_k)
    info_map = {s["name"]: {"description": s["description"], "path": s["path"]} for s in skill_list}
    # 排序：取 keyword 和 embedding 中较高的归一化分数
    def _fallback_sort_key(c: dict[str, Any]) -> float:
        kw_s = c.get("_score", 0.0) * 0.01
        emb_s = c.get("_emb_score", 0.0) * 0.2
        return max(kw_s, emb_s)

    fallback: list[dict[str, str]] = []
    for c in sorted(llm_candidates, key=_fallback_sort_key, reverse=True)[:top_k]:
        name = c["name"]
        if name in info_map:
            # 分数对齐：fallback 基线 0.3，低于 LLM 命中的 0.5（min 封顶 0.49 防止超越 LLM 基线）
            best = _fallback_sort_key(c)
            final_score = min(0.49, 0.3 + best)
            fallback.append({
                "name": name,
                "description": info_map[name]["description"],
                "path": info_map[name]["path"],
                "score": f"{final_score:.3f}",
            })
    if fallback:
        elapsed = (time.time() - t0) * 1000
        logger.info(
            "Skill match (fallback): %s (%.0fms, union=%d→%d) query=%s",
            [r["name"] for r in fallback],
            elapsed,
            n_union,
            len(fallback),
            query[:100].replace("\n", " "),
        )
    return fallback


# ── 技术关键词提取（供 Router 全false防护使用） ──

_tech_keywords_cache: frozenset[str] | None = None
_tech_keywords_ts: float = 0.0
_TECH_KEYWORDS_TTL = 3600.0  # 1 hour


def get_tech_keywords() -> frozenset[str]:
    """从已加载的 skill 列表中提取技术关键词集合。

    用于 Router 全false防护：当 LLM 返回全关时，检查 query 是否含技术名词。
    关键词来源：
    - skill name 按 -/_ 拆分，取 ≥3 字符的部分
    - skill description 走 _extract_keywords（CJK 2-gram + English token）
    - 过滤停用词

    Returns:
        frozenset[str]，TTL 缓存 1 小时。skill 索引未加载时返回空集。
    """
    global _tech_keywords_cache, _tech_keywords_ts
    import time as _time

    now = _time.time()
    if _tech_keywords_cache is not None and (now - _tech_keywords_ts) < _TECH_KEYWORDS_TTL:
        return _tech_keywords_cache

    skills = _get_skill_list()
    if not skills:
        _tech_keywords_cache = frozenset()
        _tech_keywords_ts = now
        return _tech_keywords_cache

    keywords: set[str] = set()

    for skill in skills:
        if skill.get("category") == ".archive":
            continue

        # name 拆分：flywheel-health-report → flywheel, health, report
        name = skill.get("name", "")
        for part in re.split(r"[-_/.]+", name):
            part = part.strip().lower()
            if len(part) >= 3 and part not in _STOPWORDS:
                keywords.add(part)

        # description 关键词
        desc = skill.get("description", "")
        if desc:
            keywords.update(_extract_keywords(desc))

    result = frozenset(k for k in keywords if len(k) >= 3)
    _tech_keywords_cache = result
    _tech_keywords_ts = now
    logger.debug("Tech keywords extracted: %d items from %d skills (filtered >=3 chars)", len(result), len(skills))
    return result