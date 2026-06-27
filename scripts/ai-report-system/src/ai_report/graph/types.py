"""
报告生成系统 — 共享数据类型
遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ── 网络搜索结果 ──────────────────────────────────────────


@dataclass
class WebResult:
    """网络搜索结果项。"""
    title: str
    url: str
    snippet: str = ""
    source: str = "web"


@dataclass
class ExtractedArticle:
    """web_extract 提取的全文。"""
    url: str
    title: str
    content: str                  # markdown 正文
    word_count: int = 0
    credibility: str = "medium"   # high / medium / low

    def __post_init__(self) -> None:
        if not self.word_count:
            self.word_count = len(self.content)


@dataclass
class KBSegment:
    """Dify KB 检索结果片段。"""
    content: str
    score: float = 0.0
    document_name: str = ""
    source_label: str = "知识库"  # 来源标记：知识库 / 源文档


@dataclass
class SourceRef:
    """源文档相关段落。"""
    section_title: str
    content: str
    word_count: int = 0

    def __post_init__(self) -> None:
        if not self.word_count:
            self.word_count = len(self.content)


# ── 素材包 ────────────────────────────────────────────────


@dataclass
class MaterialPack:
    """一次素材准备的完整产出。"""
    chapter_key: str
    query: str
    web_results: list[WebResult] = field(default_factory=list)
    extracted: list[ExtractedArticle] = field(default_factory=list)
    kb_segments: list[KBSegment] = field(default_factory=list)
    source_refs: list[SourceRef] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")

    def format_for_prompt(self, max_web: int = 2, max_kb: int = 1) -> str:
        """格式化为 prompt 中的「可用素材」段落。

        按可信度排序，取 Top N。
        """
        parts: list[str] = []

        if self.source_refs:
            parts.append("### 源文档（可信度: 高）")
            parts.append("以下内容来自用户提供的源文档，作为主要参考依据：")
            for ref in self.source_refs[:2]:
                parts.append(ref.content[:1000])
            parts.append("")

        extracted_sorted = sorted(
            self.extracted,
            key=lambda x: {"high": 3, "medium": 2, "low": 1}.get(x.credibility, 1),
            reverse=True,
        )
        top_web = extracted_sorted[:max_web]
        if top_web:
            parts.append("### 网络资料（可信域来源）")
            for art in top_web:
                label = {"high": "★★★", "medium": "★★☆", "low": "★☆☆"}.get(
                    art.credibility, "★★☆"
                )
                parts.append(f"[{label}] {art.title}")
                parts.append(f"来源: {art.url}")
                parts.append(art.content[:1500])
                parts.append("")

        if self.kb_segments:
            parts.append("### 知识库参考")
            for seg in self.kb_segments[:max_kb]:
                if seg.source_label == "源文档":
                    parts.append(f"[源文档] {seg.content[:800]}")
                else:
                    parts.append(f"[知识库] {seg.content[:800]}")
                parts.append("")

        return "\n".join(parts).strip()

    def to_cache_dict(self) -> dict[str, Any]:
        """序列化为可缓存 JSON。"""
        return {
            "chapter_key": self.chapter_key,
            "query": self.query,
            "created_at": self.created_at,
            "web_results": [{"title": r.title, "url": r.url, "snippet": r.snippet}
                            for r in self.web_results],
            "extracted": [{"url": a.url, "title": a.title,
                           "content": a.content, "word_count": a.word_count,
                           "credibility": a.credibility}
                          for a in self.extracted],
            "kb_segments": [{"content": s.content, "score": s.score,
                             "document_name": s.document_name,
                             "source_label": s.source_label}
                            for s in self.kb_segments],
            "source_refs": [{"section_title": r.section_title,
                             "content": r.content, "word_count": r.word_count}
                            for r in self.source_refs],
        }


# ── 报告目标 ──────────────────────────────────────────────


@dataclass
class ReportGoal:
    """报告总体目标。"""
    title: str
    purpose: str
    target_audience: str
    overall_strategy: str
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")


# ── 章节提示词 ────────────────────────────────────────────


@dataclass
class ChapterPrompt:
    """单章节的完整写作提示词包。"""
    title: str
    level: int = 2
    section_type: str = "body"
    estimated_words: int = 500
    writing_intent: str = ""
    key_points: list[str] = field(default_factory=list)
    avoid_topics: list[str] = field(default_factory=list)
    # preferred_source removed — material sourcing is done in Step 5 (CLI level)
    chart_spec: dict[str, Any] | None = None
    materials_text: str = ""         # curate 节点已拼好的素材文本
    cache_dir: str = ""              # 缓存目录路径，供质量回溯

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "level": self.level,
            "section_type": self.section_type,
            "estimated_words": self.estimated_words,
            "writing_intent": self.writing_intent,
            "key_points": self.key_points,
            "avoid_topics": self.avoid_topics,
            # preferred_source removed
            "chart_spec": self.chart_spec,
        }
