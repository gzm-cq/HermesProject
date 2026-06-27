"""Pre-phase: 输入扫描 — 从文件系统中选取可分析的文件"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from knowledge_tree_builder.models import AdmittedFile, ScanResult, SkippedFile

logger = logging.getLogger(__name__)

# ========== 排除规则常量 ==========

_EXCLUDED_FILENAMES: frozenset[str] = frozenset({
    "index.md", "moc.md", "readme.md", "README.md",
})

_EXCLUDED_DIR_PREFIXES: tuple[str, ...] = (
    "_bak", "_archive", "_tmp", "__pycache__", ".git", ".pytest_cache",
    ".qoder", ".mypy_cache", ".ruff_cache",
)

_EXCLUDED_EXTENSIONS: frozenset[str] = frozenset({
    # 日志/临时
    ".log", ".tmp", ".bak", ".pyc", ".pyo",
    # 图片
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".ico", ".webp",
    # 文档
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".pptx",
    # 压缩包
    ".zip", ".tar", ".gz", ".rar", ".7z",
    # 代码产物
    ".egg-info",
})

_MIN_FILE_SIZE_BYTES: int = 50


# ========== 主函数 ==========


def scan_input_dir(input_dir: str) -> ScanResult:
    """扫描输入目录，返回入列/跳过文件列表。

    排除规则优先级:
    1. 目录名以排除前缀开头 → 整目录跳过
    2. 文件名在排除列表中 → 跳过
    3. 扩展名在排除列表中 → 跳过
    4. 文件大小 < 50 bytes → 跳过（空文件）
    5. 文件读取失败（编码问题）→ 跳过并记日志

    输入目录不存在或为空 → empty_dir=True, admitted_files=[]

    Args:
        input_dir: 输入目录路径

    Returns:
        ScanResult: 扫描结果
    """
    root = Path(input_dir)

    if not root.exists() or not root.is_dir():
        logger.warning("输入目录不存在或不是目录: %s", input_dir)
        return ScanResult(
            source_dir=input_dir,
            admitted_files=[],
            skipped=[],
            empty_dir=True,
        )

    admitted: list[AdmittedFile] = []
    skipped: list[SkippedFile] = []

    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue

        # 检查路径中是否包含排除目录
        if _path_has_excluded_dir(file_path, root):
            continue

        # 文件名排除
        exclusion_reason = _is_excluded_file(file_path)
        if exclusion_reason:
            skipped.append(SkippedFile(path=str(file_path), reason=exclusion_reason))
            continue

        # 空文件/二进制检查
        binary_reason = _is_binary_or_empty(file_path)
        if binary_reason:
            skipped.append(SkippedFile(path=str(file_path), reason=binary_reason))
            continue

        # 读取标题
        title = _extract_title(file_path)
        admitted.append(AdmittedFile(path=str(file_path), title=title))

    empty_dir = len(admitted) == 0 and len(skipped) == 0

    return ScanResult(
        source_dir=input_dir,
        admitted_files=admitted,
        skipped=skipped,
        empty_dir=empty_dir,
    )


# ========== 内部函数 ==========


def _path_has_excluded_dir(file_path: Path, root: Path) -> bool:
    """检查文件路径中是否包含被排除的目录名。"""
    try:
        relative = file_path.relative_to(root)
    except ValueError:
        return False
    for part in relative.parts[:-1]:  # 不含文件名本身
        if _is_excluded_dir_name(part):
            return True
    return False


def _is_excluded_dir_name(name: str) -> bool:
    """目录名是否在排除列表中。"""
    for prefix in _EXCLUDED_DIR_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def _is_excluded_file(file_path: Path) -> str | None:
    """文件排除检查。None=不排除; 否则返回原因字符串。"""
    # 特定文件名
    if file_path.name in _EXCLUDED_FILENAMES:
        return f"排除文件名: {file_path.name}"

    # 扩展名
    if file_path.suffix.lower() in _EXCLUDED_EXTENSIONS:
        return f"排除扩展名: {file_path.suffix}"

    return None


def _is_binary_or_empty(file_path: Path) -> str | None:
    """空文件或二进制检查。None=可读; 否则返回原因。"""
    try:
        size = file_path.stat().st_size
    except OSError:
        return "无法读取文件信息"

    if size < _MIN_FILE_SIZE_BYTES:
        return f"文件太小: {size} bytes"

    # 尝试读取前 512 bytes 判断是否文本
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(512)
        # 如果包含 null bytes 则视为二进制
        if b"\x00" in chunk:
            return "二进制文件"
    except OSError as e:
        return f"读取失败: {e}"

    return None


def _extract_title(file_path: Path) -> str:
    """从 Markdown 首行提取标题。

    "# Title" → "Title"; YAML 前置元数据 → 跳过; 无标题 → 文件名去扩展名。
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            in_yaml = False
            first_line = True
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                # 跳过 YAML 前置元数据（--- 开头）
                if first_line and stripped == "---":
                    in_yaml = True
                    first_line = False
                    continue
                if in_yaml:
                    # YAML 结束
                    if stripped == "---":
                        in_yaml = False
                        continue
                    # 还在一开始的 YAML 块里，继续
                    continue
                if stripped.startswith("#"):
                    title = stripped.lstrip("#").strip()
                    if title:
                        return title
                # 第一行非空但不是标题，直接用这行（跳过 YAML 后的第一个非空行）
                return stripped[:100]
    except (OSError, UnicodeDecodeError):
        pass

    return file_path.stem
