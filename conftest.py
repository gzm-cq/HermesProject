"""Root conftest — ensure all plugin/script packages are importable during full test collection."""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent  # /mnt/d/HermesProject/

# Add scripts/*/ to path so nested packages (skillopt_sleep, knowledge_navigation, etc.) import
_scripts = _root / "scripts"
if _scripts.is_dir():
    for item in sorted(_scripts.iterdir()):
        if item.is_dir() and not item.name.startswith(('.', '_')):
            if str(item) not in sys.path:
                sys.path.insert(0, str(item))

# Add plugins/*/src to path
_plugins = _root / "plugins"
if _plugins.is_dir():
    for item in sorted(_plugins.iterdir()):
        if item.is_dir() and item.joinpath("src").is_dir():
            src_path = str(item / "src")
            if src_path not in sys.path:
                sys.path.insert(0, src_path)

# Also add the skillopt-sleep parent dir directly (belt-and-suspenders)
_skillopt_sleep = _root / "scripts" / "skillopt-sleep"
if _skillopt_sleep.is_dir() and str(_skillopt_sleep) not in sys.path:
    sys.path.insert(0, str(_skillopt_sleep))