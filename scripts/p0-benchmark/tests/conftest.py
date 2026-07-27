"""Pytest 配置：统一 sys.path 注入，确保 p0_benchmark 可导入。"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保源码可导入
_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))