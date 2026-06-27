"""
Dify KB Retriever — 报告生成阶段的 RAG 上下文注入
==================================================
职责：
- 在 Phase 1 并行搜索阶段，为每章节同步检索 Dify 知识库
- 将检索结果格式化为结构化文本，追加到章节搜索资料中
- 提供独立的检索接口，不依赖 Hermes 委托

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_env_config
from ..core.exceptions import HermesConnectionError, FileParseError

logger = logging.getLogger(__name__)

# ── Dify KB 连接配置（全部通过环境变量注入） ────────────────
# DIFY_COMPOSE / DIFY_API 作为旧名称保留向后兼容，
# 新代码统一通过 EnvConfig（AI_REPORT_DIFY_*）获取。


def _get_dify_compose() -> str:
    """获取 Dify Compose 路径（优先 AI_REPORT_DIFY_COMPOSE）。"""
    return get_env_config().dify_compose


def _get_dify_api() -> str:
    """获取 Dify API 地址（优先 AI_REPORT_DIFY_API）。"""
    return get_env_config().dify_api


def _require_env(name: str) -> str:
    """获取必需的环境变量，缺失则抛出清晰错误。"""
    value = os.environ.get(name)
    if not value:
        raise ValueError(
            f"缺少必需的环境变量 {name}。请设置后再试。"
        )
    return value


# ── 数据结构 ──────────────────────────────────────────────────


@dataclass
class KBSegment:
    """知识库检索结果片段。"""
    content: str
    score: float
    document_name: str = ""
    document_id: str = ""

    def to_formatted(self) -> str:
        """格式化为可读文本。"""
        score_str = f"({self.score:.2f})" if self.score > 0 else ""
        source = f"[{self.document_name}]" if self.document_name else ""
        return f"- {source} {score_str} {self.content[:500]}"


@dataclass
class KBResult:
    """知识库检索结果汇总。"""
    query: str
    segments: list[KBSegment] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str | None = None

    @property
    def success(self) -> bool:
        return len(self.segments) > 0 and self.error is None

    def format_text(self, max_chars: int = 4000) -> str:
        """格式化为可注入 prompt 的文本块。"""
        if self.error:
            logger.warning("Dify KB retrieve error: %s", self.error)
            return ""
        if not self.segments:
            return ""
        parts = ["【知识库参考】以下是与本章相关的知识库信息："]
        chars = len(parts[0])
        for seg in self.segments:
            formatted = seg.to_formatted()
            if chars + len(formatted) + 1 > max_chars:
                break
            parts.append(formatted)
            chars += len(formatted) + 1
        return "\n".join(parts)


# ── 检索器主类 ────────────────────────────────────────────────


class DifyKBRetriever:
    """Dify 知识库检索器 — 为报告章节提供 RAG 上下文。"""

    def __init__(self) -> None:
        """初始化 Dify KB 检索器。

        Raises:
            ValueError: 缺少必需的环境变量 DIFY_DATASET_API_KEY 或 DIFY_DATASET_ID。
        """
        self._dataset_id: str = _require_env("DIFY_DATASET_ID")
        self._api_key: str = _require_env("DIFY_DATASET_API_KEY")
        self._compose: str = _get_dify_compose()
        self._availability_checked: bool = False
        self._available: bool = True

    # ── 可用性检查 ─────────────────────────────────────────

    def check_available(self) -> bool:
        """检查 Dify API 容器是否可达。"""
        if self._availability_checked:
            return self._available
        self._availability_checked = True
        try:
            cmd = [
                "docker", "compose", "-f", self._compose,
                "exec", "-T", "api", "curl", "-s", "--connect-timeout", "5",
                f"{_get_dify_api()}/health",
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
            )
            self._available = result.returncode == 0 and bool(result.stdout.strip())
        except (subprocess.SubprocessError, OSError, FileNotFoundError, TimeoutError) as e:
            logger.warning("Dify KB not available: %s", e)
            self._available = False
        if not self._available:
            logger.info("Dify KB unavailable — skipping RAG retrieval")
        return self._available

    # ── 核心检索 ───────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
    ) -> KBResult:
        """检索 Dify 知识库。

        Args:
            query: 检索查询
            top_k: 返回的最大结果数

        Returns:
            检索结果汇总
        """
        start = time.time()
        result = KBResult(query=query)

        try:
            if not self.check_available():
                result.error = "Dify KB unavailable"
                return result

            body = json.dumps({"query": query, "top_k": top_k})
            curl_cmd = [
                "docker", "compose", "-f", self._compose,
                "exec", "-T", "api", "curl", "-s",
                "-X", "POST",
                f"{_get_dify_api()}/v1/datasets/{self._dataset_id}/retrieve",
                "-H", f"Authorization: Bearer {self._api_key}",
                "-H", "Content-Type: application/json",
                "-d", body,
            ]
            proc = subprocess.run(
                curl_cmd, capture_output=True, text=True, timeout=30,
            )
            raw = proc.stdout.strip()
            if not raw:
                result.error = "empty response"
                return result

            data = json.loads(raw)
            records = data.get("records") or data.get("result", [])
            if isinstance(data, dict) and "query" in data and "records" in data:
                records = data.get("records", [])

            for rec in records:
                segment_data = rec.get("segment", rec)
                content = segment_data.get("content", "")
                score = rec.get("score", 0.0) or rec.get("similarity", 0.0)
                doc_name = (
                    rec.get("document", {}).get("name", "")
                    or segment_data.get("document_name", "")
                )
                doc_id = (
                    rec.get("document", {}).get("id", "")
                    or segment_data.get("document_id", "")
                )
                if content:
                    result.segments.append(KBSegment(
                        content=content,
                        score=float(score),
                        document_name=doc_name,
                        document_id=doc_id,
                    ))

            result.elapsed_ms = (time.time() - start) * 1000
            logger.info(
                "Dify KB: '%s' → %d segments in %.0fms",
                query[:40], len(result.segments), result.elapsed_ms,
            )

        except json.JSONDecodeError as e:
            result.error = f"invalid JSON: {e}"
            logger.warning("Dify KB JSON parse error: %s", e)
        except subprocess.TimeoutExpired:
            result.error = "timeout"
            logger.warning("Dify KB retrieve timeout for '%s'", query[:40])
        except (OSError, ConnectionError, subprocess.SubprocessError, KeyError, ValueError) as e:
            result.error = str(e)
            logger.warning("Dify KB retrieve failed: %s", e)

        return result

    # ── 为章节生成检索查询 + 执行 ────────────────────────

    def retrieve_for_chapter(
        self,
        chapter_title: str,
        topic: str,
        report_type: str = "",
        top_k: int = 3,
    ) -> str:
        """为单个报告章节检索知识库，返回格式化文本。

        Args:
            chapter_title: 章节标题
            topic: 报告主题
            report_type: 报告类型（可选）
            top_k: 最大结果数

        Returns:
            格式化后的知识库文本，可直接注入 prompt
        """
        # 构建语义化查询
        parts = [topic, chapter_title]
        if report_type:
            parts.append(report_type)
        query = " ".join(parts)

        result = self.retrieve(query, top_k=top_k)
        return result.format_text(max_chars=3000)
