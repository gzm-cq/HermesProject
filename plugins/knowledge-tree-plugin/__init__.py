"""knowledge-tree-plugin — 暴露 register() 给 Hermes 插件系统"""

import os
import sys
from pathlib import Path

# Hermes 插件加载器只把插件根目录加入 sys.path，
# src/ 子目录不在 path 上，需要手动注入
_KT_PLUGIN_SRC = Path(__file__).resolve().parent / "src"
_KT_SRC_STR = str(_KT_PLUGIN_SRC)
if _KT_SRC_STR not in sys.path:
    sys.path.insert(0, _KT_SRC_STR)

from knowledge_tree_plugin import register, post_llm_call

__all__ = ["register", "post_llm_call"]
