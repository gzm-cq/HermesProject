"""根级 conftest：将 src/ 注入 sys.path，使 `flywheel_health_report` 包在从仓库根做
测试收集 / 模块导入时可用（src-layout 包的标准做法）。

配合 pyproject.toml 的 `[tool.pytest.ini_options] pythonpath = ["src"]` 使用；
此文件额外覆盖非 pytest 的导入式收集场景。
"""
import sys
from pathlib import Path

_SRC = str(Path(__file__).parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
