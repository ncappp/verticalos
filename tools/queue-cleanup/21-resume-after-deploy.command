#!/bin/bash
set -uo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
PY="$HOME/Downloads/faxclip-telegram-bridge-v14/.venv/bin/python"
if [ -x "$PY" ]; then "$PY" "$HERE/resume.py"; else printf 'Основная папка FaxClip с Python должна оставаться в Downloads.\n'; fi
printf '\nНажмите Enter, чтобы закрыть окно.';read -r _ || true
