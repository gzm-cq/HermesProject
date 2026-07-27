"""conftest — ensure skillopt_sleep package is on sys.path when running from subdirectory."""
import sys
from pathlib import Path

_src = Path(__file__).resolve().parent / "skillopt_sleep"
if _src.is_dir() and str(_src.parent) not in sys.path:
    sys.path.insert(0, str(_src.parent))