"""knowledge-tree-builder 源码路径定位（唯一入口）。

本插件依赖同仓 `scripts/knowledge-tree-builder`（提供 embedding / LLM 分析 /
DatabaseAdapter 等能力），但两者是独立部署单元，不能靠固定层级的相对路径硬编码
（开发态与生产态目录层级不同，重构目录即失效）。

定位顺序（与 hermes_common.bootstrap 同风格）：
  1. 环境变量 ``KT_BUILDER_SRC``（显式指定，优先级最高）
  2. 从本文件向上逐级查找 ``scripts/knowledge-tree-builder/src``（开发态仓库内）
  3. 生产态固定根 ``/root/.hermes/scripts/knowledge-tree-builder/src``

用法：
    from knowledge_tree_plugin.kt_builder_path import ensure_kt_builder_on_path
    ensure_kt_builder_on_path()          # 幂等；找不到仅告警，返回 None
    ensure_kt_builder_on_path(strict=True)  # 找不到直接 raise（缺包哨兵）
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

__all__ = ["locate_kt_builder_src", "ensure_kt_builder_on_path"]

logger = logging.getLogger(__name__)

_ENV_VAR = "KT_BUILDER_SRC"
_REL = Path("scripts") / "knowledge-tree-builder" / "src"
_PROD_ROOT = Path("/root/.hermes")
_MAX_UP = 12


def _is_valid(src: Path) -> bool:
    """校验候选目录下确实存在 knowledge_tree_builder 包。"""
    return (src / "knowledge_tree_builder" / "__init__.py").is_file()


def locate_kt_builder_src() -> Path | None:
    """定位 knowledge-tree-builder 的 src 目录，找不到返回 None。"""
    env = os.environ.get(_ENV_VAR)
    if env:
        cand = Path(env)
        if _is_valid(cand):
            return cand
        logger.warning(
            "%s 指向的路径无效（缺少 knowledge_tree_builder 包）: %s", _ENV_VAR, cand,
        )

    # 开发态：从本文件向上逐级找仓库根下的 scripts/knowledge-tree-builder/src
    d = Path(__file__).resolve().parent
    for _ in range(_MAX_UP):
        cand = d / _REL
        if _is_valid(cand):
            return cand
        if d.parent == d:
            break
        d = d.parent

    # 生产态固定根
    cand = _PROD_ROOT / _REL
    if _is_valid(cand):
        return cand
    return None


def ensure_kt_builder_on_path(strict: bool = False) -> Path | None:
    """幂等地把 knowledge-tree-builder 的 src 注入 sys.path。

    Args:
        strict: True 时定位失败直接抛 ImportError（缺包哨兵）；
                False 时仅告警并返回 None（知识树功能降级不可用）。

    Returns:
        注入的 src 路径；未找到且 strict=False 时返回 None。

    Raises:
        ImportError: strict=True 且未定位到时。
    """
    src = locate_kt_builder_src()
    if src is None:
        msg = (
            "knowledge-tree-builder 源码未找到（依次尝试：环境变量 "
            f"{_ENV_VAR}、向上查找 {_REL}、生产态 {_PROD_ROOT / _REL}）。"
            f"请检查部署或设置 {_ENV_VAR} 指向正确的 src 目录。"
        )
        if strict:
            raise ImportError(msg)
        logger.warning("%s 知识树功能将不可用。", msg)
        return None

    s = str(src)
    if s not in sys.path:
        sys.path.insert(0, s)
    return src
