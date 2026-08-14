import os
import sys

# 让 tests/ 能 import hermes_common 包（libs/hermes_common 为其父目录）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
