#!/bin/bash
# One-time setup on macOS: a private Python environment with vivaah installed.
# Usage: open Terminal in this folder and run   ./setup_mac.sh
set -euo pipefail
cd "$(dirname "$0")"

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "Python 3 was not found."
  echo "Install it from https://www.python.org/downloads/macos/ (or run: brew install python), then run this again."
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python $("$PY" -V) is too old: vivaah needs Python 3.9 or newer."
  exit 1
fi

echo "Creating .venv with $("$PY" -V) ..."
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[heic]"
chmod +x "mac/Vivaah Grade.command" 2>/dev/null || true

echo
echo "vivaah is installed. Looks available:"
vivaah looks
echo
echo "Next:"
echo "  - double-click 'mac/Vivaah Grade.command' to grade a folder, or"
echo "  - in Terminal: source .venv/bin/activate && vivaah grade /path/to/wedding --look signature --xmp"
