"""knowledge-tree-plugin — 知识树增量学习

注册 Hermes 插件 hook：
- post_llm_call: LLM 响应后 → 分析对话 → 提取知识点 → 增量放置到知识树

pre_llm_call 由 knowledge-navigation 插件统一负责（融合知识树 + Hindsight 两个分域）。
本插件通过 public_api.recall_from_tree() 向知识导航插件提供知识树召回能力。
"""

import os
import sys
from pathlib import Path

# 注入 knowledge-tree-builder 包路径（优先使用环境变量，硬编码路径作为 fallback）
_KT_SRC_ENV = os.environ.get("KT_BUILDER_SRC")
if _KT_SRC_ENV:
    _KT_SRC = Path(_KT_SRC_ENV)
else:
    _KT_SRC = Path(__file__).resolve().parent.parent.parent.parent.parent \
        / "scripts" / "knowledge-tree-builder" / "src"
_KT_SRC_STR = str(_KT_SRC)
if _KT_SRC_STR not in sys.path:
    sys.path.insert(0, _KT_SRC_STR)
if not (_KT_SRC / "knowledge_tree_builder" / "__init__.py").exists():
    import logging
    logging.getLogger(__name__).warning(
        "知识树 builder 路径未找到: %s（知识树功能将不可用，"
        "设置 KT_BUILDER_SRC 环境变量指向正确的 src 目录）",
        _KT_SRC_STR,
    )

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
