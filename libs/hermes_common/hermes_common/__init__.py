"""Hermes 统一共享库（hermes_common）。

跨「脚本层（scripts/*）」与「插件层（plugins/*）」复用的纯依赖工具集中地：
  - ledger:     F-1 统一反馈账本（append_ledger_event，零依赖）
  - llm_guard:  所有 LLM 调用的统一护栏（解析 / 重试 / 退避 / 限速，零第三方依赖）
  - text_utils: 关键词提取 / CJK 处理

消费方将本包父目录（开发态 libs/hermes_common 或生产态 /root/.hermes/lib）注入
sys.path 后，以 `from hermes_common.xxx import ...` 使用。

统一入口（唯一 bootstrap）：
  - ensure_on_path()  ：幂等地把本包父目录注入 sys.path（开发/生产双路径自定位）。
  - bootstrap()       ：ensure_on_path() + 失败即 raise（缺包哨兵），供消费方在
                        import 前调用；返回注入后的父目录路径。
消费方推荐统一样板（见各脚本 bootstrap 段）：
        try:
            from hermes_common import bootstrap
        except ImportError:
            ...  # 见下方 ensure_on_path 的兜底逻辑
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "append_ledger_event",
    "CJK_STOP_CHARS",
    "extract_keywords",
    "ensure_on_path",
    "bootstrap",
]


def _locate_parent() -> str:
    """自定位本包的父目录（应加入 sys.path 的根）。

    逻辑：本文件位于 `<父目录>/hermes_common/__init__.py`，故父目录 = 本文件的上两级。
    开发态父目录 = <repo>/libs/hermes_common；生产态父目录 = /root/.hermes/lib。
    无论哪种，父目录即 `Path(__file__).resolve().parent.parent`。
    """
    return str(Path(__file__).resolve().parent.parent)


def ensure_on_path() -> str:
    """幂等地把本包父目录注入 sys.path，返回该父目录。

    供消费方在 import 前调用，保证 `from hermes_common.xxx import ...` 可解析。
    若父目录已存在则跳过（幂等）。
    """
    parent = _locate_parent()
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return parent


def bootstrap() -> str:
    """统一入口：ensure_on_path() 后校验本包可导入，失败即 raise。

    缺包哨兵：#2 —— 生产态若部署缺失（父目录无 hermes_common/__init__.py），
    在此立即抛错，避免消费方静默降级为 no-op 桩导致 F-1 账本静默失效。

    Returns:
        注入后的父目录路径。

    Raises:
        ImportError: 本包无法在此父目录下定位/导入。
    """
    parent = ensure_on_path()
    # 校验父目录下确实存在本包（生产态防部署缺失）
    if not os.path.isfile(os.path.join(parent, "hermes_common", "__init__.py")):
        raise ImportError(
            f"hermes_common 包缺失：{os.path.join(parent, 'hermes_common')} 不存在。"
            "请检查部署（生产 /root/.hermes/lib）或 HERMES_COMMON_SRC 指向。"
        )
    return parent


# ── 模块级：确保父目录在 sys.path（幂等），供直接 `import hermes_common` 的消费方 ──
ensure_on_path()

from .ledger import append_ledger_event  # noqa: E402
from .text_utils import CJK_STOP_CHARS, extract_keywords  # noqa: E402