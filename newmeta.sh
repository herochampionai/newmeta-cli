#!/bin/bash
# NewMeta CLI - Works in Git Bash, WSL, Linux, Mac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
python cli.py "$@"