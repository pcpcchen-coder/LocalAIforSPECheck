#!/bin/bash
set -u
cd -- "$(dirname -- "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
    ".venv/bin/python" launcher.py
elif command -v python3 >/dev/null 2>&1; then
    python3 launcher.py
else
    echo "Python was not found. Please ask IT to follow README.md first."
    read -r -p "Press Return to close this window. " _reply
    exit 1
fi
result=$?
if [ "$result" -ne 0 ]; then
    echo "Application did not start. Please send this window to IT."
    read -r -p "Press Return to close this window. " _reply
fi
exit "$result"
