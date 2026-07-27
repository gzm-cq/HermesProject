"""Pytest 配置：统一 sys.path 注入，避免每个测试文件重复。"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on sys.path for all tests
_project_dir = Path(__file__).resolve().parent.parent
for p in (str(_project_dir / "src"), str(_project_dir / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)
