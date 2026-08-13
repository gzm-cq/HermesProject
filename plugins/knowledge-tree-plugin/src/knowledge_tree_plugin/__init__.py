"""knowledge-tree-plugin — 知识树增量学习

注册 Hermes 插件 hook：
- post_llm_call: LLM 响应后 → 分析对话 → 提取知识点 → 增量放置到知识树

pre_llm_call 由 knowledge-navigation 插件统一负责（融合知识树 + Hindsight 两个分域）。
本插件通过 public_api.recall_from_tree() 向知识导航插件提供知识树召回能力。
"""

import os
import sys
from pathlib import Path

from knowledge_tree_plugin.kt_builder_path import ensure_kt_builder_on_path

# 注入 knowledge-tree-builder 包路径（环境变量 → 向上自定位 → 生产态固定路径）
ensure_kt_builder_on_path()

# 统一共享库 hermes_common bootstrap（唯一入口：双路径自定位 + 缺包哨兵）
try:
    from hermes_common import bootstrap  # noqa: F401
except ImportError:
    _parent = os.environ.get("HERMES_COMMON_SRC") or ""
    if not _parent:
        _d = Path(__file__).resolve().parent
        for _ in range(12):
            _cand = _d / "libs" / "hermes_common"
            if (_cand / "hermes_common" / "__init__.py").is_file():
                _parent = str(_cand)
                break
            if _d.parent == _d:
                break
            _d = _d.parent
    if not _parent:
        _prod = "/root/.hermes/lib"
        if os.path.isfile(os.path.join(_prod, "hermes_common", "__init__.py")):
            _parent = _prod
    if _parent and _parent not in sys.path:
        sys.path.insert(0, _parent)
    from hermes_common import bootstrap  # noqa: F401
bootstrap()

from knowledge_tree_plugin.hooks import post_llm_call

__all__ = [
    "post_llm_call",
    "register",
]


def register(ctx: object) -> None:
    """Hermes 插件注册入口。

    Args:
        ctx: Hermes 插件上下文对象，提供以下方法：
            - register_hook(hook_name: str, callback: Callable)
            - logger: 插件日志器
    """
    try:
        # pre_llm_call 由 knowledge-navigation 插件统一负责
        ctx.register_hook("post_llm_call", post_llm_call)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            "知识树插件注册失败: %s", e,
        )
        raise
