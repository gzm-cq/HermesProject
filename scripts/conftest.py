"""Root conftest for scripts/ — ensure each subproject's package is on sys.path."""

import sys
from pathlib import Path

_scripts = Path(__file__).resolve().parent  # /mnt/d/HermesProject/scripts/

# Add each subproject directory to sys.path so their packages are importable
for item in sorted(_scripts.iterdir()):
    if item.is_dir() and not item.name.startswith(('.', '_')):
        # Check if it has a Python package we need
        pkg = item / item.name.replace('-', '_')
        if pkg.exists():
            if str(item) not in sys.path:
                sys.path.insert(0, str(item))
