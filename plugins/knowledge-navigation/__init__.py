"""knowledge-navigation 插件 —— 桥接到 src/ 布局。"""

import sys
from pathlib import Path

# 注入自身 src/
_src = Path(__file__).parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

# 注入 knowledge-tree-plugin 路径（hooks.py 会 import 其 public_api）
_kt_plugin = Path(__file__).parent.parent / "knowledge-tree-plugin" / "src"
if str(_kt_plugin) not in sys.path:
    sys.path.insert(0, str(_kt_plugin))

from knowledge_navigation import register, pre_llm_call  # noqa: E402

__all__ = ["register", "pre_llm_call"]
