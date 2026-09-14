#!/usr/bin/env bash
# HES — start the local server on Linux or macOS.
#
#   ./run.sh              browser application on http://127.0.0.1:8756/
#   ./run.sh desktop.py   native desktop window (needs pywebview)
cd "$(dirname "$0")"
exec python3 "${1:-server.py}"
