#!/usr/bin/env python3
# memory-classify-v6.py — 向后兼容入口
# 原始逻辑已重构为标准包 src/memory_cleanup/，此文件保持旧调用路径兼容。
# 用法：python3 memory-classify-v6.py [--apply]（行为与重构前完全一致）
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from memory_cleanup.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
