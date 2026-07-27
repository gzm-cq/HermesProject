"""conftest — ensure skillopt_sleep package is on sys.path for tests."""

import sys
from pathlib import Path

_src = Path(__file__).resolve().parent.parent / "skillopt_sleep"
if str(_src.parent) not in sys.path:
    sys.path.insert(0, str(_src.parent))
