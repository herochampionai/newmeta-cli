#!/usr/bin/env python
import sys
import os
from pathlib import Path

# Find NewMeta Supervisor
SCRIPT_DIR = Path(__file__).parent
WATCHER_PATH = SCRIPT_DIR / "watcher.py"

if __name__ == "__main__":
    sys.argv[0] = str(WATCHER_PATH)
    import subprocess
    sys.exit(subprocess.call([sys.executable, str(WATCHER_PATH)] + sys.argv[1:]))