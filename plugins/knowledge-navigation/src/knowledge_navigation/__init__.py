"""knowledge-navigation 插件 —— 自动 recall Hindsight 注入上下文。"""

import os
import sys
from pathlib import Path

# 注入 knowledge-tree-plugin 包路径（跟 knowledge-tree-plugin 注入 builder 的方式一致）
_KT_PLUGIN_ENV = os.environ.get("KT_PLUGIN_SRC")
if _KT_PLUGIN_ENV:
    _PLUGIN_SRC = Path(_KT_PLUGIN_ENV)
else:
    _PLUGIN_SRC = Path(__file__).resolve().parent.parent.parent.parent.parent \
        / "plugins" / "knowledge-tree-plugin" / "src"
_PLUGIN_SRC_STR = str(_PLUGIN_SRC)
if _PLUGIN_SRC_STR not in sys.path:
    sys.path.insert(0, _PLUGIN_SRC_STR)
if not (_PLUGIN_SRC / "knowledge_tree_plugin" / "__init__.py").exists():
    import logging
    logging.getLogger(__name__).warning(
        "knowledge-tree-plugin 路径未找到: %s（知识树 recall 将不可用，"
        "设置 KT_PLUGIN_SRC 环境变量指向正确的 src 目录）",
        _PLUGIN_SRC_STR,
    )

from knowledge_navigation.core.hooks import pre_llm_call

# 兼容旧的直接导入
from knowledge_navigation.core.filtering import filter_by_score as filter_results
from knowledge_navigation.core.filtering import format_context_lines as format_context
from knowledge_navigation.adapters.hindsight import HindsightClient

__all__ = [
    "pre_llm_call",
    "register",
    "filter_results",
    "format_context",
    "HindsightClient",
]


def register(ctx: object) -> None:
    """Hermes 插件注册入口。

    Args:
        ctx: Hermes 插件上下文对象，提供 register_hook 方法。
    """
    ctx.register_hook("pre_llm_call", pre_llm_call)
