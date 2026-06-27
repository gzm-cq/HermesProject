"""源文档加载器 — 负责加载和合并源素材文档。

遵循 Hermes Code Rules 规范。
"""
from __future__ import annotations

import glob
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..config import (
    get_desktop_fallback,
    get_env_config,
    get_parallel_config,
    get_source_extensions,
    load_report_config,
)
from .exceptions import SourceDocLoadError

logger = logging.getLogger(__name__)

__all__ = ["SourceDocumentLoader"]


class SourceDocumentLoader:
    """负责加载和合并源文档。

    搜索顺序：
    1. SOURCE_DOC_PATH 环境变量指定的文件（精确路径，单个文件）
    2. reports/<topic>/inputs 目录（用户素材）— 读取全部文件
    3. reports/<topic>/ 目录（兼容旧流程）
    4. 桌面临时 txt 文件（兼容旧流程）
    """

    def __init__(self, max_workers: int | None = None) -> None:
        """初始化源文档加载器。

        Args:
            max_workers: 最大并行工作线程数，None 时从配置读取
        """
        parallel_cfg = get_parallel_config()
        self._parallel_enabled = parallel_cfg.enabled
        self._max_workers = max_workers or parallel_cfg.source_max_workers

    def load(self, topic: str, report_type: str = "") -> str:
        """加载单个或多个源文档。

        Args:
            topic: 报告主题
            report_type: 报告类型（tech/market/research/product），
                         用于加载正确的扩展名配置

        Returns:
            合并后的源文档字符串。未找到时返回空字符串。
        """
        config = load_report_config(topic, report_type=report_type or None)
        source_exts = get_source_extensions(config)
        desktop_fallback = get_desktop_fallback(config)

        # 1. 环境变量精确指定路径（单个文件，兼容旧流程）
        env_path = get_env_config().source_doc_path
        if env_path:
            p = Path(env_path)
            if p.exists():
                content = self._read_file_content(p)
                if content and len(content) > 500:
                    logger.info("  source doc (env): %s (%d chars)", env_path, len(content))
                    return content
                else:
                    logger.warning(
                        "  SOURCE_DOC_PATH '%s' 内容过短 (%d chars)",
                        env_path, len(content or ""),
                    )

        # 2. 项目 reports/<topic>/inputs 目录（用户素材）— 读取全部文件
        project_root = Path(__file__).resolve().parent.parent.parent
        topic_dir = project_root / "reports" / topic
        input_dir = topic_dir / "inputs"
        fragments: list[tuple[str, str]] = []

        # 优先读 inputs/，没有就用 topic 目录根
        source_dirs = [input_dir, topic_dir]
        for sd in source_dirs:
            if sd.exists():
                file_paths: list[Path] = []
                for ext in source_exts:
                    for fp in sorted(sd.glob(f"*{ext}")):
                        if fp.is_dir() or fp.name.startswith("."):
                            continue
                        file_paths.append(fp)

                if not file_paths:
                    continue

                # 并行读取多个文件
                if self._parallel_enabled and len(file_paths) > 1:
                    fragments = self._load_files_parallel(file_paths)
                else:
                    fragments = self._load_files_serial(file_paths)

                if fragments:
                    break  # inputs/ 有素材就不再搜 topic 根

        # 3. 桌面临时 txt 文件（兼容旧流程）
        if not fragments:
            try:
                desktop = Path(desktop_fallback)
                if desktop.exists():
                    files = glob.glob(str(desktop / f"*{topic[:4]}*.txt"))
                    if not files:
                        files = glob.glob(str(desktop / "*.txt"))
                    if files:
                        content = self._read_file_content(Path(files[0]))
                        if content and len(content) > 500:
                            logger.info(
                                "  source material (desktop): %s (%d chars)",
                                files[0], len(content),
                            )
                            return content
            except Exception:
                pass

        if not fragments:
            logger.warning("  ⚠️ 未找到源文档: topic='%s'", topic)
            return ""

        return self._merge_documents(fragments)

    def _load_files_serial(self, file_paths: list[Path]) -> list[tuple[str, str]]:
        """串行读取文件列表。"""
        fragments: list[tuple[str, str]] = []
        for fp in file_paths:
            content = self._read_file_content(fp)
            if content and len(content) > 500:
                fragments.append((fp.name, content))
                logger.info(
                    "  source material (%s): %s (%d chars)",
                    fp.suffix.lstrip("."), fp.name, len(content),
                )
        return fragments

    def _load_files_parallel(self, file_paths: list[Path]) -> list[tuple[str, str]]:
        """并行读取文件列表，保持文件名排序。"""
        fragments: list[tuple[str, str]] = []
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(file_paths))) as executor:
            future_to_path = {
                executor.submit(self._read_file_content, path): path
                for path in file_paths
            }
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    content = future.result()
                    if content and len(content) > 500:
                        fragments.append((path.name, content))
                        logger.info(
                            "  source material (%s): %s (%d chars)",
                            path.suffix.lstrip("."), path.name, len(content),
                        )
                except Exception as e:
                    logger.warning("  并行加载文件失败 %s: %s", path.name, e)

        # 保持文件名排序（与串行一致）
        fragments.sort(key=lambda x: x[0])
        return fragments

    def _read_file_content(self, fp: Path) -> str | None:
        """读取单个文件的内容，支持 .md/.txt/.docx/.pdf。

        Args:
            fp: 文件路径

        Returns:
            文本内容，无法读取时返回 None
        """
        try:
            ext = fp.suffix.lower()
            if ext in (".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".xml"):
                try:
                    return fp.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    # GBK 编码回退（常见于部分中文 .txt 文件）
                    try:
                        return fp.read_text(encoding="gbk")
                    except UnicodeDecodeError:
                        logger.warning("  编码不可识别（utf-8/gbk 均失败）: %s", fp.name)
                        return None
            elif ext == ".docx":
                try:
                    from docx import Document as _Doc

                    doc = _Doc(str(fp))
                    paras: list[str] = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
                    return "\n".join(paras) if paras else None
                except ImportError:
                    logger.warning("  python-docx 未安装，跳过 .docx: %s", fp.name)
                    return None
                except Exception as e:
                    logger.warning("  读取 .docx 文件异常（跳过）: %s (%s)", fp.name, e)
                    return None
            elif ext == ".pdf":
                try:
                    import pymupdf as _PF

                    doc = _PF.open(str(fp))
                    return "\n".join(page.get_text() for page in doc)
                except ImportError:
                    try:
                        import pdfplumber as _PP

                        with _PP.open(str(fp)) as pdf:
                            return "\n".join(
                                page.extract_text() or "" for page in pdf.pages
                            )
                    except ImportError:
                        logger.warning(
                            "  未安装 pymupdf/pdfplumber，跳过 .pdf: %s", fp.name,
                        )
                        return None
                except Exception as e:
                    logger.warning("  读取 .pdf 失败: %s (%s)", fp.name, e)
                    return None
            else:
                logger.debug("  跳过不支持的文件类型: %s (%s)", fp.name, ext)
                return None
        except Exception as e:
            logger.warning("  读取文件失败: %s (%s)", fp.name, e)
            return None

    def _merge_documents(self, fragments: list[tuple[str, str]]) -> str:
        """合并多个文档，带文件名标记。

        Args:
            fragments: (文件名, 内容) 元组列表

        Returns:
            合并后的文档字符串
        """
        parts: list[str] = []
        for fname, content in fragments:
            parts.append(f"📄 {fname}")
            parts.append(content)
            parts.append("")
        merged = "\n".join(parts)
        logger.info("  ✅ 合并 %d 个素材文件, 总计 %d chars", len(fragments), len(merged))
        return merged
