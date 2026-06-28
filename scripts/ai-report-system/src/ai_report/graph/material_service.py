"""
MaterialService — 统一素材准备服务
===================================
所有环节（规划/写作/重写/优化）共用同一个入口。

职责：
  web_search(可信域约束) → LLM 选 URL → web_extract → 全文
  + Dify KB retrieve
  + 源文档相关段落提取
  → MaterialPack → 缓存 search_cache/{report_id}/

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from ..adapters.dify_kb import DifyKBRetriever
from ..config import get_env_config
from .types import (
    ExtractedArticle,
    KBSegment,
    MaterialPack,
    SourceRef,
    WebResult,
)

logger = logging.getLogger(__name__)

# ── 可信域名白名单 ────────────────────────────────────────

HIGH_CREDIBILITY_DOMAINS: list[str] = [
    "gov.cn", "sasac.gov.cn", "cnki.net",
    "mof.gov.cn", "miit.gov.cn", "ndrc.gov.cn",
]

MEDIUM_CREDIBILITY_DOMAINS: list[str] = [
    "csdn.net", "infoq.cn", "oschina.net",
    "36kr.com", "tech.qq.com", "solidot.org",
    "arxiv.org", "ieee.org", "acm.org",
]


# ── 工具函数 ──────────────────────────────────────────────


def _credibility_from_url(url: str) -> str:
    """从 URL 域名判定可信度。"""
    url_lower = url.lower()
    for domain in HIGH_CREDIBILITY_DOMAINS:
        if domain in url_lower:
            return "high"
    for domain in MEDIUM_CREDIBILITY_DOMAINS:
        if domain in url_lower:
            return "medium"
    return "low"


def _hash_query(query: str) -> str:
    """搜索词 → 短 hash，用于缓存文件名。"""
    return hashlib.md5(query.encode()).hexdigest()[:12]


def _extract_source_sections(
    source_doc: str,
    chapter_titles: list[str],
) -> list[SourceRef]:
    """从源文档中按章节标题提取相关段落。

    源文档格式：一、二、三 或 1. 2. 3. 分隔。
    返回与章节标题最相关的段落。
    """
    refs: list[SourceRef] = []
    lines = source_doc.split("\n")
    current_section = "前言"
    current_content: list[str] = []

    for line in lines:
        stripped = line.strip()
        # 检测中文章节标记
        if stripped and (
            stripped.startswith("一、")
            or stripped.startswith("二、")
            or stripped.startswith("三、")
            or stripped.startswith("四、")
            or stripped.startswith("五、")
            or stripped.startswith("（")
            or stripped.startswith("(")
            or stripped[0].isdigit()
        ):
            # 保存上一节
            if current_content:
                text = "\n".join(current_content)
                refs.append(SourceRef(
                    section_title=current_section,
                    content=text,
                ))
            current_section = stripped[:30]
            current_content = []
        else:
            if stripped:
                current_content.append(stripped)

    # 保存最后一节
    if current_content:
        text = "\n".join(current_content)
        refs.append(SourceRef(
            section_title=current_section,
            content=text,
        ))

    return refs


def _select_urls_via_llm(
    web_results: list[WebResult],
    writing_intent: str,
    max_select: int = 3,
    llm_caller=None,
) -> list[str]:
    """让 LLM 从中选出最符合写作意图的 URL。"""
    if not web_results:
        return []

    if llm_caller is None:
        from ..adapters.ai_client import call_llm as _fallback
        llm_caller = _fallback

    items_text = "\n".join(
        f"[{i}] {r.title}\n    URL: {r.url}\n    摘要: {r.snippet[:200]}"
        for i, r in enumerate(web_results)
    )
    prompt = (
        f"你是一个研究助手。需要从以下搜索结果中，选出最符合写作意图的 URL。\n\n"
        f"写作意图: {writing_intent}\n\n"
        f"搜索结果:\n{items_text}\n\n"
        f"请选出最多 {max_select} 个最相关的 URL，只输出 URL 列表，每行一个。"
        f"不需要解释。如果都不相关，输出空行。"
    )
    response = llm_caller(prompt, max_tokens=500, temperature=0.1)
    selected: list[str] = []
    for line in response.strip().split("\n"):
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            selected.append(line)
    return selected[:max_select]


# ── 主类 ──────────────────────────────────────────────────


class MaterialService:
    """统一素材准备服务。"""

    def __init__(self, cache_root: str | None = None,
                 source_doc_kb_name: str | None = None,
                 domain_config: dict[str, list[str]] | None = None,
                 llm_caller=None) -> None:
        """初始化 MaterialService。

        Args:
            cache_root: 素材缓存根目录
            source_doc_kb_name: Dify KB 中源文档的文档名。
                用于在返回结果中标记"源文档"vs"知识库"。
                None = 所有 KB 结果统一标记为"知识库"（通用模式）。
            domain_config: 域配置，格式 {'high': [...], 'medium': [...]}。
                None = 使用模块级全局默认值。
            llm_caller: LLM 调用函数，默认使用 hermes_tools.ai_client.call_llm。
        """
        self._kb_retriever = DifyKBRetriever()
        self._cache_root = Path(cache_root or "./search_cache")
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._source_doc_kb_name = source_doc_kb_name
        # 域配置：实例级覆盖模块级全局变量
        dc = domain_config or {}
        self._high_cred_domains: list[str] = dc.get("high", HIGH_CREDIBILITY_DOMAINS)
        self._medium_cred_domains: list[str] = dc.get("medium", MEDIUM_CREDIBILITY_DOMAINS)
        # LLM 调用器：注入点
        if llm_caller is not None:
            self._llm_caller = llm_caller
        else:
            from ..adapters.ai_client import call_llm as _fallback
            self._llm_caller = _fallback

    def _credibility_from_url(self, url: str) -> str:
        """从 URL 判定可信度（使用实例级域配置）。"""
        url_lower = url.lower()
        for domain in self._high_cred_domains:
            if domain in url_lower:
                return "high"
        for domain in self._medium_cred_domains:
            if domain in url_lower:
                return "medium"
        return "low"

    # ── 公开入口 ──────────────────────────────────────────

    def prepare(
        self,
        chapter_key: str,
        query: str,
        source_doc: str = "",
        writing_intent: str = "",
        domain_filter: list[str] | None = None,
        with_kb: bool = True,
        max_web: int = 3,
    ) -> MaterialPack:
        """统一素材准备入口。

        Args:
            chapter_key: 章节标识，如 "chapter-1-三网架构"
            query: 搜索词
            source_doc: 源文档全文
            writing_intent: 写作意图（用于 URL 筛选）
            domain_filter: 可信域约束，None 用默认白名单
            with_kb: 是否检索 Dify KB
            max_web: web_extract 取几篇全文
        """
        logger.info(
            "MaterialService: prepare chapter='%s' query='%s'",
            chapter_key, query[:50],
        )

        # 0. 检查缓存（代理层预填充后缓存命中，跳过 web 搜索）
        cached = self.load_cache(chapter_key, query)
        if cached is not None:
            logger.info("  cache HIT: %s '%s' (%d web, %d extracted)",
                        chapter_key, query[:40],
                        len(cached.web_results), len(cached.extracted))
            return cached
        logger.debug("  cache MISS: %s '%s'", chapter_key, query[:40])

        pack = MaterialPack(chapter_key=chapter_key, query=query)

        # 1. Web 搜索 + 提取
        self._do_web_search(pack, query, writing_intent, domain_filter, max_web)

        # 2. Dify KB 检索
        if with_kb:
            self._do_kb_retrieve(pack, query)

        # 3. 源文档段落提取
        if source_doc:
            refs = _extract_source_sections(source_doc, [chapter_key])
            if refs:
                pack.source_refs = refs[:2]

        # 4. 缓存
        self._cache_pack(pack)

        return pack

    def prepare_supplement(
        self,
        chapter_key: str,
        original_content: str,
        diagnosis: str,
        domain_filter: list[str] | None = None,
    ) -> str:
        """质量重写/优化时，补搜新素材。

        Args:
            chapter_key: 章节标识
            original_content: 已写好的章节内容
            diagnosis: 质量诊断结果

        Returns:
            格式化的补充素材文本，直接拼入重写 prompt
        """
        # 根据诊断生成新的搜索词
        supplement_query = self._llm_caller(
            f"你是一个研究助手。某章节存在以下问题：\n\n{diagnosis}\n\n"
            f"请生成 2 个搜索词，用于在网上补充相关资料。"
            f"直接输出搜索词，每行一个。",
            max_tokens=200, temperature=0.3,
        )
        queries = [q.strip() for q in supplement_query.strip().split("\n") if q.strip()]

        parts: list[str] = []
        for q in queries[:2]:
            pack = self.prepare(
                chapter_key=chapter_key,
                query=q,
                domain_filter=domain_filter,
                with_kb=False,
                max_web=1,
            )
            if pack.extracted:
                parts.append(f"\n【补充资料 - {q}】")
                for art in pack.extracted:
                    parts.append(art.content[:1500])

        return "\n".join(parts)

    # ── Web 搜索 ──────────────────────────────────────────

    def _do_web_search(
        self,
        pack: MaterialPack,
        query: str,
        writing_intent: str,
        domain_filter: list[str] | None,
        max_web: int,
    ) -> None:
        """Web 搜索 — 主引擎 Tavily，备选 DuckDuckGo。

        管线子进程可用（基于 HTTP API，不依赖 hermes_tools）。
        web_extract 用 httpx + lxml 取全文。
        """
        logger.info("  web_search: '%s' (max_web=%d)", query[:50], max_web)

        # 1. 搜索（Tavily 主 → DuckDuckGo 备）
        raw_results = self._search_tavily(query) or self._search_duckduckgo(query)
        if not raw_results:
            logger.info("  web_search returned 0 results")
            return

        for r in raw_results[:6]:
            title = r.get("title", "") or r.get("name", "")
            url = r.get("url", "")
            snippet = r.get("description", "") or r.get("snippet", "") or r.get("content", "") or ""
            pack.web_results.append(WebResult(title=title, url=url, snippet=snippet))

        # 2. LLM 选 URL
        if writing_intent:
            selected_urls = _select_urls_via_llm(pack.web_results, writing_intent, max_select=max_web, llm_caller=self._llm_caller)
        else:
            selected_urls = [r.url for r in pack.web_results[:max_web]]
        if not selected_urls:
            return

        # 3. 取全文（httpx + lxml）
        for url in selected_urls[:max_web]:
            try:
                content = self._fetch_page_text(url)
                if content:
                    credibility = self._credibility_from_url(url)
                    pack.extracted.append(ExtractedArticle(
                        url=url,
                        title=url[:50],
                        content=content[:3000],
                        credibility=credibility,
                    ))
                    logger.info("  extracted: %s (%s, %d chars)", url[:60], credibility, min(len(content), 3000))
            except Exception as e:
                logger.warning("  extract failed: %s", e)

    def _search_tavily(self, query: str) -> list[dict[str, str]] | None:
        """Tavily 搜索（主引擎）。"""
        api_key = get_env_config().tavily_api_key
        if not api_key:
            logger.debug("  Tavily: 无 API key")
            return None
        try:
            import httpx as _httpx
            resp = _httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": 6},
                timeout=15.0,
            )
            if resp.status_code != 200:
                logger.warning("  Tavily: HTTP %d", resp.status_code)
                return None
            data = resp.json()
            results = data.get("results", [])
            logger.info("  Tavily: %d results", len(results))
            return results
        except Exception as e:
            logger.warning("  Tavily failed: %s", e)
            return None

    def _search_duckduckgo(self, query: str) -> list[dict[str, str]] | None:
        """DuckDuckGo 搜索（备用引擎）。"""
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=6))
            logger.info("  DuckDuckGo: %d results", len(results))
            return results
        except Exception as e:
            logger.warning("  DuckDuckGo failed: %s", e)
            return None

    def _fetch_page_text(self, url: str) -> str | None:
        """httpx 获取页面内容 → lxml 提取纯文本。"""
        try:
            import httpx as _httpx
            resp = _httpx.get(url, timeout=15.0, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return None
            from lxml import html as _html
            tree = _html.fromstring(resp.content)
            # 移除 script/style
            for bad in tree.xpath("//script | //style | //nav | //footer | //header"):
                bad.getparent().remove(bad)
            text = tree.text_content()
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            return "\n".join(lines[:200])  # 最多 200 行
        except Exception as e:
            logger.debug("  fetch_page_text failed %s: %s", url[:40], e)
            return None

    # ── Dify KB 检索 ──────────────────────────────────────

    def _do_kb_retrieve(self, pack: MaterialPack, query: str) -> None:
        """Dify KB 检索。"""
        try:
            result = self._kb_retriever.retrieve(query, top_k=3)
            if result.success:
                for seg in result.segments:
                    label = "源文档" if self._source_doc_kb_name and seg.document_name == self._source_doc_kb_name else "知识库"
                    pack.kb_segments.append(KBSegment(
                        content=seg.content,
                        score=seg.score,
                        document_name=seg.document_name,
                        source_label=label,
                    ))
                logger.info("  KB: %d segments", len(pack.kb_segments))
        except Exception as e:
            logger.warning("  KB retrieve failed: %s", e)

    # ── 缓存 ──────────────────────────────────────────────

    def _cache_pack(self, pack: MaterialPack) -> None:
        """缓存素材包到文件。"""
        cache_dir = self._cache_root / pack.chapter_key
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"base_{_hash_query(pack.query)}.json"
        try:
            cache_file.write_text(
                json.dumps(pack.to_cache_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("  cached: %s", cache_file)
        except Exception as e:
            logger.warning("  cache write failed: %s", e)

    def load_cache(self, chapter_key: str, query: str) -> MaterialPack | None:
        """读取已缓存的素材包。

        搜索顺序：
        1. {cache_root}/{chapter_key}/base_{hash}.json  — 精确匹配
        2. {cache_root}/materials/ 下的统一池回退
        3. 均未命中 → None
        """
        cache_dir = self._cache_root / chapter_key
        if cache_dir.exists():
            result = self._load_cache_file(cache_dir, chapter_key, query)
            if result is not None:
                return result

        # 统一素材池回退
        pool_dir = self._cache_root / "materials"
        if pool_dir.exists() and pool_dir != cache_dir:
            result = self._load_cache_file(pool_dir, chapter_key, query)
            if result is not None:
                return result

        return None

    def load_all_materials(self) -> list[dict[str, Any]]:
        """读取统一素材池 materials/all_articles.json。

        StateGraph 和内容生成共用此池：
        - StateGraph: 用于参考大纲 + 全文支撑章节规划
        - 内容生成: 直接读取全篇文章作为写作素材

        Returns:
            文章列表，每篇含 title/url/content/credibility/toc_lines
        """
        pool_path = self._cache_root / "materials" / "all_articles.json"
        if not pool_path.exists():
            logger.debug("  materials pool not found: %s", pool_path)
            return []
        try:
            data = json.loads(pool_path.read_text(encoding="utf-8"))
            articles = data.get("articles", [])
            logger.debug("  materials pool: %d articles", len(articles))
            return articles
        except Exception as e:
            logger.warning("  materials pool load failed: %s", e)
            return []

    def _load_cache_file(
        self, cache_dir: Path, chapter_key: str, query: str,
    ) -> MaterialPack | None:
        """从指定缓存目录尝试加载。"""
        cache_file = cache_dir / f"base_{_hash_query(query)}.json"
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            pack = MaterialPack(
                chapter_key=data["chapter_key"],
                query=data["query"],
                created_at=data.get("created_at", ""),
                web_results=[WebResult(**r) for r in data.get("web_results", [])],
                extracted=[ExtractedArticle(**a) for a in data.get("extracted", [])],
                kb_segments=[KBSegment(**s) for s in data.get("kb_segments", [])],
                source_refs=[SourceRef(**r) for r in data.get("source_refs", [])],
            )
            return pack
        except Exception as e:
            logger.warning("  cache load failed: %s", e)
            return None
