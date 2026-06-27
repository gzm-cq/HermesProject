"""
pre_search — 代理层预搜索脚本
================================
在 Hermes agent 上下文（有 hermes_tools.web_search）中运行，
将搜索结果写入缓存文件，供子进程的 MaterialService.prepare() 读取。

用法（Hermes agent 中）：
    from scripts.pre_search import pre_search_and_cache
    pre_search_and_cache(cache_root, chapter_key, queries, writing_intent)

缓存文件位置：
    {cache_root}/{chapter_key}/base_{md5(query)}.json
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 非 Hermes agent 上下文优雅降级 ──────────────────────────────
try:
    from hermes_tools import web_search, web_extract  # type: ignore[unused-import]
except ImportError:
    print('❌ pre_search.py 需要在 Hermes agent 会话中执行（from hermes_tools 不可用）')
    print('   请在 agent 聊天中运行此脚本')
    import sys

    sys.exit(1)
# ────────────────────────────────────────────────────────────────


def _hash_query(query: str) -> str:
    """搜索词 → 短 hash，用于缓存文件名。"""
    return hashlib.md5(query.encode()).hexdigest()[:12]


def _select_urls_via_llm(
    web_results: list[dict[str, str]],
    writing_intent: str,
    max_select: int = 3,
) -> list[str]:
    """LLM 从中选出最符合写作意图的 URL。（纯文本版，不依赖 MaterialService）"""
    if not web_results:
        return []

    items_text = "\n".join(
        f"[{i}] {r.get('title', '')}\n    URL: {r.get('url', '')}\n    摘要: {r.get('description', '')[:200]}"
        for i, r in enumerate(web_results)
    )
    prompt = (
        f"你是一个研究助手。需要从以下搜索结果中，选出最符合写作意图的 URL。\n\n"
        f"写作意图: {writing_intent}\n\n"
        f"搜索结果:\n{items_text}\n\n"
        f"请选出最多 {max_select} 个最相关的 URL，只输出 URL 列表，每行一个。"
        f"不需要解释。如果都不相关，输出空行。"
    )
    try:
        from ai_report.adapters.ai_client import call_llm
        response = call_llm(prompt, max_tokens=500, temperature=0.1)
    except ImportError:
        logger.warning("  call_llm not available, using heuristic (top N)")
        return [r.get("url", "") for r in web_results[:max_select]]

    selected: list[str] = []
    for line in response.strip().split("\n"):
        line = line.strip().strip('"').strip("'")  # 去掉可能的引号包裹
        # 提取 markdown 链接 [text](url) 中的 url
        if line.startswith("[") and "](" in line:
            import re as _re
            m = _re.search(r'\]\(([^)]+)\)', line)
            if m:
                line = m.group(1)
        if line.startswith("http://") or line.startswith("https://"):
            selected.append(line)
    # LLM 返回了但格式不对时，用启发式兜底
    if not selected:
        logger.info("  LLM URL parse failed, fallback to heuristic top %d", max_select)
        return [r.get("url", "") for r in web_results[:max_select]]
    return selected[:max_select]


def _credibility_from_url(url: str) -> str:
    """从 URL 域名判定可信度。（轻量版，不依赖模块级常量）"""
    url_lower = url.lower()
    HIGH = ["gov.cn", "sasac.gov.cn", "cnki.net", "mof.gov.cn", "miit.gov.cn", "ndrc.gov.cn"]
    MEDIUM = ["csdn.net", "infoq.cn", "oschina.net", "36kr.com", "tech.qq.com", "solidot.org", "arxiv.org", "ieee.org", "acm.org"]
    for domain in HIGH:
        if domain in url_lower:
            return "high"
    for domain in MEDIUM:
        if domain in url_lower:
            return "medium"
    return "low"


def pre_search_and_cache(
    cache_root: str | Path,
    chapter_key: str,
    queries: list[str],
    writing_intent: str = "",
    max_web: int = 3,
) -> int:
    """统一预搜索 — 结果存到 materials/all_articles.json。

    不再区分"大纲素材"和"写作素材"，所有结果合并为一个池。
    搜索流程：2-3 个宽搜 → 去重合并 → LLM 选最佳 URL → 提取全文 → 保存。

    Args:
        cache_root: 缓存根目录（reports/<topic>/search_cache/）
        chapter_key: 保留参数（实际统一存到 materials/）
        queries: 搜索词列表（2-3 个宽词足矣）
        writing_intent: 写作意图，用于 LLM 选 URL
        max_web: 最终提取几篇全文

    Returns:
        成功提取的文章数
    """
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    from hermes_tools import web_search, web_extract

    # ── 1. 搜索合并阶段 ─────────────────────────────────────
    all_web_items: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for q in queries[:3]:
        logger.info("  [pre_search] searching: '%s'", q[:50])
        try:
            search_result = web_search(query=q, limit=8)
            raw_results = search_result.get("data", {}).get("web", []) if isinstance(search_result, dict) else []
        except Exception as e:
            logger.warning("  [pre_search] search failed '%s': %s", q[:30], e)
            continue

        for r in raw_results:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_web_items.append({
                    "title": r.get("title", "") or r.get("name", ""),
                    "url": url,
                    "description": r.get("description", "") or r.get("snippet", "") or "",
                })

    logger.info("  [pre_search] 合并后 %d 条唯一结果", len(all_web_items))
    if not all_web_items:
        return 0

    # ── 2. LLM 统一选 URL ──────────────────────────────────
    if writing_intent:
        selected_urls = _select_urls_via_llm(all_web_items, writing_intent, max_select=max_web)
    else:
        selected_urls = [r["url"] for r in all_web_items[:max_web]]
    if not selected_urls:
        return 0

    # ── 3. 提取全文 ─────────────────────────────────────────
    articles: list[dict[str, Any]] = []
    for url in selected_urls[:max_web]:
        try:
            extract_result = web_extract(urls=[url])
            for ext in (extract_result.get("results", []) if isinstance(extract_result, dict) else []):
                content = ext.get("content", "")
                title = ext.get("title", "")
                if content:
                    articles.append({
                        "url": url,
                        "title": title or url[:50],
                        "content": content[:5000],  # 5000 chars 全文，支撑写作
                        "credibility": _credibility_from_url(url),
                        "toc_lines": _extract_toc(content),
                    })
                    logger.info("  [pre_search] extracted: %s (%d chars)", url[:60], min(len(content), 5000))
        except Exception as e:
            logger.warning("  [pre_search] extract failed %s: %s", url[:40], e)

    # ── 4. 写入统一池 ──────────────────────────────────────
    materials_dir = cache_root / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)

    # 合并到现有池（追加模式）
    pool_path = materials_dir / "all_articles.json"
    if pool_path.exists():
        existing = json.loads(pool_path.read_text(encoding="utf-8"))
        existing_urls = {a["url"] for a in existing.get("articles", [])}
        for a in articles:
            if a["url"] not in existing_urls:
                existing.setdefault("articles", []).append(a)
        articles = existing["articles"]
        pool_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("  [pre_search] 追加到现有池: %d 篇文章", len(articles))
    else:
        pool_path.write_text(json.dumps({
            "created_at": __import__("datetime").datetime.now().isoformat(),
            "articles": articles,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("  [pre_search] 创建统一素材池: %d 篇文章", len(articles))

    return len(articles)


def _extract_toc(content: str) -> list[str]:
    """从文章内容提取目录行。"""
    toc: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped.lstrip("#").strip()
            toc.append(f"{'  ' * (level - 1)}- {text}")
    return toc[:20]
