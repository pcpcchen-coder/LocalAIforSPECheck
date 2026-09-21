#!/usr/bin/env bash
set -u
cd -- "$(dirname -- "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
    exec ".venv/bin/python" launcher.py
elif command -v python3 >/dev/null 2>&1; then
    exec python3 launcher.py
else
    echo "Python was not found. Please ask IT to follow README.md first."
    exit 1
fi
