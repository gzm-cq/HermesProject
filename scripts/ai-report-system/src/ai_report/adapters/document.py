"""Hermes Document Parser — 多格式文档解析器
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, TypeAlias

from ..core.base import BaseComponent
from ..config import get_config
from ..core.exceptions import DocumentError, FileParseError

# formats.py and utils.py content merged into this file below

logger = logging.getLogger(__name__)


class HermesDocumentParser(BaseComponent):
    """
    多格式文档解析器

    支持格式:
    - 纯文本 / Markdown / JSON / YAML
    - CSV / HTML / XML
    - INI/TOML 配置
    - 常见编程语言源码

    解析策略:
    1. 格式专用解析器（最佳）
    2. Markdown回退（%s）
    3. 纯文本回退（通用）
    4. 元数据提取（极限）

    特性:
    - 自动编码检测
    - 文件大小限制 (10MB)
    - 结构化元数据提取
    - 内容预览生成
    - 错误恢复和优雅降级
    """

    COMPONENT_NAME = "HermesDocumentParser"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "多格式文档解析器，支持15+种文件格式"
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

    def __init__(self, config: Any | None = None) -> None:
        self._max_file_size: int = self.MAX_FILE_SIZE
        super().__init__(config)

    def _initialize_internal(self) -> None:
        """初始化解析器"""
        logger.info(
            "%s 初始化完成, 支持 %d 种格式, 最大文件 %dMB",
            self.COMPONENT_NAME,
            len(FORMAT_REGISTRY),
            self._max_file_size // (1024 * 1024),
        )

    # ── 公开接口 ──────────────────────────────────────────

    def parse(
        self,
        file_path: str | Path,
        encoding: str | None = None,
        max_preview_lines: int = 100,
    ) -> ParseResult:
        """
        解析指定文件

        Args:
            file_path: 文件路径
            encoding: 文件编码（None自动检测）
            max_preview_lines: 预览最大行数

        Returns:
            解析结果字典，包含:
            - path: 文件路径
            - format: 检测到的格式
            - content: 完整内容
            - preview: 内容预览（前N行）
            - metadata: 文件元数据（行数、大小、编码等）
            - sections: 章节/结构信息（如有）
            - stats: 解析统计

        Raises:
            DocumentError: 文件不存在、过大或解析失败
        """
        start_time = time.time()
        path = Path(file_path) if isinstance(file_path, str) else file_path

        # ── 验证 ──
        if not path.exists():
            raise DocumentError(
                message=f"文件不存在: {path}",
                file_path=str(path),
                parse_error="FileNotFound",
            )
        if not path.is_file():
            raise DocumentError(
                message=f"路径不是文件: {path}",
                file_path=str(path),
                parse_error="NotAFile",
            )

        file_size = path.stat().st_size
        if file_size > self._max_file_size:
            raise DocumentError(
                message=f"文件过大: {file_size:,}字节 (限制: {self._max_file_size:,})",
                file_path=str(path),
                parse_error="FileTooLarge",
            )

        if file_size == 0:
            raise DocumentError(
                message=f"空文件: {path}",
                file_path=str(path),
                parse_error="EmptyFile",
            )

        # ── 读取 ──
        encoding = encoding or detect_encoding(path)
        try:
            raw_bytes = path.read_bytes()
            content = raw_bytes.decode(encoding, errors="replace")
        except (OSError, UnicodeDecodeError) as e:
            # 编码回退：尝试系统默认编码
            try:
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                raise DocumentError(
                    message=f"读取文件失败: {e}",
                    file_path=str(path),
                    parse_error=str(e),
                ) from e

        # ── 格式检测 ──
        ext = path.suffix.lower()
        fmt = FORMAT_REGISTRY.get(ext, "unknown")

        # ── 解析 ──
        parse_errors: list[str] = []
        parsed_content = content
        sections: list[dict[str, Any]] | None = None

        # 尝试格式特定解析
        try:
            if fmt == "json":
                parsed_content, sections = parse_json(content)
            elif fmt == "yaml":
                parsed_content, sections = parse_yaml(content)
            elif fmt == "csv":
                parsed_content, sections = parse_csv(content, path)
            elif fmt == "html":
                parsed_content = parse_html(content)
            elif fmt == "markdown":
                sections = extract_markdown_sections(content)
            elif fmt == "toml":
                parsed_content = parse_toml(content)
            elif fmt in {"python", "javascript", "typescript", "java", "sql"}:
                sections = extract_code_sections(content, fmt)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError, csv.Error, OSError) as e:
            parse_errors.append(str(e))
            logger.warning("格式解析 %s 失败: %s, 回退到文本解析", fmt, e)

        # 回退到文本解析
        if parse_errors and sections is None:
            sections = extract_text_sections(content)

        # ── 构建结果 ──
        lines = content.splitlines()
        line_count = len(lines)
        preview_lines = lines[:max_preview_lines]
        preview_text = "\n".join(preview_lines)

        result: ParseResult = {
            "path": str(path),
            "filename": path.name,
            "format": fmt,
            "extension": ext,
            "encoding": encoding,
            "size": file_size,
            "size_display": format_size(file_size),
            "lines": line_count,
            "content": content,
            "preview": preview_text,
            "metadata": {
                "created": safe_stat(path, "st_ctime"),
                "modified": safe_stat(path, "st_mtime"),
                "permissions": oct(path.stat().st_mode)[-3:],
            },
            "sections": sections or [],
            "stats": {
                "parse_time_ms": (time.time() - start_time) * 1000,
                "parse_errors": parse_errors,
                "success": len(parse_errors) == 0,
            },
            "parse_errors": parse_errors,
        }

        elapsed = time.time() - start_time
        self._record_performance(start_time, success=len(parse_errors) == 0)
        logger.info("解析完成: %s (%s, %d行, %.0fms)", path.name, fmt, line_count, elapsed * 1000)

        return result

    def parse_content(
        self,
        content: str,
        format_hint: str | None = None,
    ) -> ParseResult:
        """
        解析文本内容（无需文件）

        Args:
            content: 文本内容
            format_hint: 格式提示（'json', 'yaml', 'csv', 'markdown'等）

        Returns:
            解析结果（类似parse()但不含文件元数据）
        """
        fmt = format_hint or guess_format_from_content(content)
        parsed_content = content
        sections: list[dict[str, Any]] | None = None
        parse_errors: list[str] = []

        try:
            if fmt == "json":
                parsed_content, sections = parse_json(content)
            elif fmt == "yaml":
                parsed_content, sections = parse_yaml(content)
            elif fmt == "html":
                parsed_content = parse_html(content)
            elif fmt == "markdown":
                sections = extract_markdown_sections(content)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            parse_errors.append(str(e))

        lines = content.splitlines()
        result: ParseResult = {
            "path": None,
            "filename": None,
            "format": fmt,
            "content": content,
            "preview": "\n".join(lines[:50]),
            "lines": len(lines),
            "size": len(content.encode("utf-8")),
            "sections": sections or [],
            "stats": {
                "parse_errors": parse_errors,
                "success": len(parse_errors) == 0,
            },
            "parse_errors": parse_errors,
        }
        return result

    # ── 执行 ──

    def execute(self, operation: str = "parse", **kwargs: Any) -> Any:
        """执行解析操作"""
        operations = {
            "parse": self.parse,
            "parse_content": self.parse_content,
        }

        if operation not in operations:
            raise DocumentError(
                message=f"未知解析操作: {operation}",
                parse_error="UnknownOperation",
            )

        return operations[operation](**kwargs)


# ── formats.py ──

"""Hermes Document Parser — 格式特定解析器"""


import csv
import io
import json
import re
from pathlib import Path
from typing import Any

# _HtmlStripper is defined below in merged utils.py content


# ── 格式解析 ──────────────────────────────────────────


def parse_json(content: str) -> tuple[str, list[dict[str, Any]] | None]:
    """
    解析JSON内容

    Returns:
        (格式化后的JSON字符串, 顶层键列表作为section)
    """
    data = json.loads(content)
    formatted = json.dumps(data, indent=2, ensure_ascii=False)

    sections: list[dict[str, Any]] = []
    if isinstance(data, dict):
        sections = [
            {"type": "key", "title": k, "level": 1}
            for k in list(data.keys())[:50]
        ]
    elif isinstance(data, list):
        sections = [
            {"type": "array_item", "title": f"[{i}]", "level": 1}
            for i in range(min(len(data), 50))
        ]

    return formatted, sections


def parse_yaml(
    content: str,
) -> tuple[str, list[dict[str, Any]] | None]:
    """
    解析YAML内容
    纯实现，不依赖PyYAML库
    """
    lines = content.splitlines()
    sections: list[dict[str, Any]] = []
    formatted_lines: list[str] = []

    for line in lines:
        if line.strip().startswith("#"):
            continue
        # 检测顶层键（无缩进或仅有列表前缀）
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#"):
            indent = len(line) - len(line.lstrip())
            if indent == 0 and ":" in stripped:
                key = stripped.split(":")[0].strip()
                sections.append({"type": "key", "title": key, "level": 1})
        formatted_lines.append(line)

    return "\n".join(formatted_lines), sections


def parse_csv(
    content: str,
    path: Path,
) -> tuple[str, list[dict[str, Any]] | None]:
    """
    解析CSV内容
    """
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)

    if not rows:
        return content, []

    headers = reader.fieldnames or []
    sections = [{"type": "header", "title": h, "level": 1} for h in headers]

    formatted = f"Headers: {', '.join(headers)}\nRows: {len(rows)}\n---\n"
    for row in rows[:20]:
        formatted += json.dumps(row, ensure_ascii=False) + "\n"

    if len(rows) > 20:
        formatted += f"... ({len(rows) - 20} more rows)\n"

    return formatted, sections


def parse_html(content: str) -> str:
    """剥离HTML标签，返回纯文本"""
    stripper = _HtmlStripper()
    stripper.feed(content)
    return stripper.text


def parse_toml(content: str) -> str:
    """
    简单TOML解析（提取section标题）
    """
    lines = content.splitlines()
    formatted: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            formatted.append(f"\n## Section: {stripped}")
        else:
            formatted.append(line)
    return "\n".join(formatted)


# ── 结构提取 ──────────────────────────────────────────


def extract_markdown_sections(content: str) -> list[dict[str, Any]]:
    """提取Markdown章节结构"""
    sections: list[dict[str, Any]] = []
    for line in content.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            sections.append({
                "type": "heading",
                "title": title,
                "level": level,
            })
    return sections


def extract_text_sections(content: str) -> list[dict[str, Any]]:
    """从普通文本提取段落结构"""
    sections: list[dict[str, Any]] = []
    lines = content.splitlines()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # 检测可能的章节标题
        if (
            len(stripped) < 100
            and not stripped.endswith(".")
            and not stripped.endswith("。")
            and (i == 0 or not lines[i - 1].strip())
        ):
            sections.append({
                "type": "paragraph_header",
                "title": stripped[:80],
                "level": 1,
                "line": i + 1,
            })

    return sections


def extract_code_sections(
    content: str,
    language: str,
) -> list[dict[str, Any]]:
    """提取源代码中的函数/类定义"""
    sections: list[dict[str, Any]] = []

    # 匹配函数定义
    for match in re.finditer(
        r"^(?:async\s+)?def\s+(\w+)\s*\(",
        content,
        re.MULTILINE,
    ):
        sections.append({
            "type": "function",
            "title": match.group(1),
            "level": 1,
            "line": content[:match.start()].count("\n") + 1,
        })

    # 匹配类定义
    for match in re.finditer(
        r"^class\s+(\w+)",
        content,
        re.MULTILINE,
    ):
        sections.append({
            "type": "class",
            "title": match.group(1),
            "level": 2,
            "line": content[:match.start()].count("\n") + 1,
        })

    return sections


# ── utils.py ──

"""Hermes Document Parser — 共享工具模块"""


import html.parser
from pathlib import Path
from typing import Any, TypeAlias

# 类型别名
FileContent: TypeAlias = str
ParseResult: TypeAlias = dict[str, Any]


class _HtmlStripper(html.parser.HTMLParser):
    """HTML标签剥离器"""

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        """处理开始标签"""
        self._skip = tag in {"script", "style", "code"}

    def handle_endtag(self, tag: str) -> None:
        """处理结束标签"""
        self._skip = False

    def handle_data(self, data: str) -> None:
        """处理文本数据"""
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self._text.append(stripped)

    @property
    def text(self) -> str:
        """获取剥离后的文本"""
        return "\n".join(self._text)


# ── 支持的格式注册表 ───────────────────────────────────

FORMAT_REGISTRY: dict[str, str] = {
    ".txt": "text",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".csv": "csv",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
    ".toml": "toml",
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
    ".sql": "sql",
    ".log": "text",
}


# ── 辅助函数 ──────────────────────────────────────────


def detect_encoding(path: Path) -> str:
    """检测文件编码"""
    # 简单检测BOM
    raw = path.read_bytes()[:4]
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"
    return "utf-8"


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def safe_stat(path: Path, attr: str) -> float | None:
    """安全获取文件属性"""
    try:
        return getattr(path.stat(), attr)
    except (OSError, AttributeError):
        return None


def guess_format_from_content(content: str) -> str:
    """从内容推测格式"""
    stripped = content.strip()

    if stripped.startswith("{") or stripped.startswith("["):
        try:
            import json
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass

    if stripped.startswith("<"):
        return "html"

    if stripped.startswith("# ") or stripped.startswith("## "):
        return "markdown"

    return "text"
