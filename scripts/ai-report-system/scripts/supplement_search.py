"""
supplement_search — 章节按需补搜
=================================
在代理层运行（有 hermes_tools 的上下文）。拿到 StateGraph 输出的
chapter_prompts 后，对每章的 writing_intent 生成精准搜索词 →
hermes_tools.web_search → LLM 选 URL → web_extract → 注入 materials_text。

与 pre_search.py 共享相同的搜索模式（hermes_tools 直接搜索）。
结果写入缓存供子进程 MaterialService.prepare() 读取。

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── 搜索引擎（hermes_tools） ──────────────────────────────


def _hash_query(query: str) -> str:
    """搜索词 → 短 hash，用于缓存文件名。"""
    return hashlib.md5(query.encode()).hexdigest()[:12]


def _search_via_hermes(query: str, max_results: int = 6) -> list[dict[str, str]]:
    """通过 hermes_tools.web_search 搜索。

    仅在 Hermes agent 上下文可用（ImportError 表示不在 agent 上下文）。
    """
    try:
        from hermes_tools import web_search
    except ImportError:
        logger.warning("  hermes_tools not available (not in agent context)")
        return []

    try:
        result = web_search(query=query, limit=max_results)
        raw = result.get("data", {}).get("web", []) if isinstance(result, dict) else []
        logger.debug("  hermes_tools: %d results", len(raw))
        return raw
    except Exception as e:
        logger.warning("  hermes_tools search failed: %s", e)
        return []


def _extract_via_hermes(url: str) -> str | None:
    """通过 hermes_tools.web_extract 提取页面内容。"""
    try:
        from hermes_tools import web_extract
    except ImportError:
        return None

    try:
        result = web_extract(urls=[url])
        for ext in (result.get("results", []) if isinstance(result, dict) else []):
            content = ext.get("content", "")
            if content:
                return content[:5000]
    except Exception as e:
        logger.debug("  web_extract failed %s: %s", url[:40], e)
    return None


# ── LLM 辅助 ────────────────────────────────────────


def _select_urls_via_llm(
    items: list[dict[str, str]],
    writing_intent: str,
    max_select: int,
) -> list[str]:
    """LLM 从搜索结果中选最相关的 URL。"""
    if not items:
        return []

    items_text = "\n".join(
        f"[{i}] {r.get('title', '')}\n    URL: {r.get('url', '')}\n    摘要: {r.get('description', '')[:200]}"
        for i, r in enumerate(items)
    )
    prompt = (
        f"你是一个研究助手。需要从以下搜索结果中，选出最符合写作意图的 URL。\n\n"
        f"写作意图: {writing_intent}\n\n"
        f"搜索结果:\n{items_text}\n\n"
        f"请选出最多 {max_select} 个最相关的 URL，只输出 URL 列表，每行一个。"
        f"不需要解释。如果都不相关，输出空行。"
    )
    from ai_report.adapters.ai_client import call_llm
    try:
        response = call_llm(prompt, max_tokens=300, temperature=0.1)
        urls: list[str] = []
        for line in response.strip().split("\n"):
            line = line.strip().strip('"').strip("'")
            if line.startswith("[") and "](" in line:
                m = re.search(r'\]\(([^)]+)\)', line)
                line = m.group(1) if m else line
            if line.startswith("http://") or line.startswith("https://"):
                urls.append(line)
        return urls[:max_select] if urls else [r.get("url", "") for r in items[:max_select]]
    except Exception:
        return [r.get("url", "") for r in items[:max_select]]


def _generate_queries(topic: str, title: str, writing_intent: str, key_points: list[str]) -> list[str]:
    """LLM 根据章节要素生成 2 个精准搜索词。"""
    if not writing_intent and not key_points:
        return [f"{topic} {title}"]

    from ai_report.adapters.ai_client import call_llm

    kp_text = "\n".join(f"- {kp}" for kp in (key_points or [])[:3])
    prompt = (
        f"你是一个研究助手。需要为一篇报告的章节生成搜索词，用于在网上查找相关素材。\n\n"
        f"报告主题: {topic}\n章节标题: {title}\n写作意图: {writing_intent}\n"
        f"必须覆盖要点:\n{kp_text}\n\n"
        f"请生成 2 个精准的搜索词，每行一个，直接输出搜索词即可。"
    )
    try:
        response = call_llm(prompt, max_tokens=200, temperature=0.3)
        queries = [q.strip() for q in response.strip().split("\n") if q.strip()[:3] != "---"]
        return [q for q in queries if len(q) > 5][:2]
    except Exception:
        return [f"{topic} {title}"]


def _credibility(url: str) -> str:
    """简单可信度判断。"""
    url = url.lower()
    high = ["gov.cn", "sasac.gov.cn", "cnki.net", "reuters.com", "bloomberg.com"]
    medium = ["csdn.net", "infoq.cn", "36kr.com", "arxiv.org", "ieee.org", "acm.org"]
    for d in high:
        if d in url:
            return "high"
    for d in medium:
        if d in url:
            return "medium"
    return "low"


def _safe_key(title: str) -> str:
    """标题 → 文件系统安全名。"""
    s = title.replace(" ", "_").replace("/", "_").replace("\\", "_")
    return re.sub(r'[^\w\-\u4e00-\u9fff]', '', s)[:30]


# ── 主入口 ──────────────────────────────────────────


def supplement_chapters(
    chapter_prompts: list[dict[str, Any]],
    cache_root: str | Path,
    topic: str,
    max_articles_per_chapter: int = 2,
) -> list[dict[str, Any]]:
    """对每章按写作意图精准搜索，结果注入 materials_text。

    使用 hermes_tools.web_search + web_extract（代理层可用）。
    每章搜索 2 个精准查询 → 合并去重 → LLM 选 URL → 提取全文 →
    写入缓存 + 注入 materials_text。

    Args:
        chapter_prompts: StateGraph 输出的章节提示列表
        cache_root: 缓存根目录
        topic: 报告主题
        max_articles_per_chapter: 每章最多提取文章数

    Returns:
        注入网络素材后的 chapter_prompts（enriched）
    """
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)

    enriched = list(chapter_prompts)

    for i, cp in enumerate(chapter_prompts):
        title = cp.get("title", f"第{i+1}章")
        writing_intent = cp.get("writing_intent", "")
        key_points = cp.get("key_points", [])
        preferred = cp.get("preferred_source", "any")

        if preferred == "source_only":
            logger.info("  [supplement][%d/%d] %s: source_only, 跳过", i + 1, len(chapter_prompts), title)
            continue

        queries = _generate_queries(topic, title, writing_intent, key_points)
        if not queries:
            continue

        cache_dir = cache_root / f"chapter-{i+1}-{_safe_key(title)}"
        cache_dir.mkdir(parents=True, exist_ok=True)

        all_articles: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for q in queries[:2]:
            raw_results = _search_via_hermes(q)
            if not raw_results:
                continue

            items = []
            for r in raw_results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    items.append({
                        "title": r.get("title", "") or r.get("name", ""),
                        "url": url,
                        "description": r.get("description", "") or r.get("snippet", "") or r.get("content", "") or "",
                    })

            if not items:
                continue

            selected = _select_urls_via_llm(items, writing_intent, max_articles_per_chapter)
            for url in selected:
                content = _extract_via_hermes(url)
                if content:
                    all_articles.append({
                        "url": url,
                        "title": url[:50],
                        "content": content[:3000],
                        "credibility": _credibility(url),
                    })

        if all_articles:
            cache_data = {
                "chapter_key": cache_dir.name,
                "query": queries[0],
                "articles": all_articles,
                "created_at": __import__("datetime").datetime.now().isoformat(),
            }
            cache_file = cache_dir / "supplement.json"
            cache_file.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("  [supplement][%d/%d] %s: %d 篇文章", i + 1, len(chapter_prompts), title, len(all_articles))

            existing_text = cp.get("materials_text", "") or ""
            supplement_text = "\n\n## 网络补充素材\n" + "\n".join(
                f"--- {a['url'][:40]} [{a['credibility']}] ---\n{a['content']}"
                for a in all_articles
            )
            enriched[i]["materials_text"] = existing_text + supplement_text
        else:
            logger.info("  [supplement][%d/%d] %s: 未找到相关素材", i + 1, len(chapter_prompts), title)

    return enriched
