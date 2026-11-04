#!/bin/bash
# One-time setup for the Wound Healing batch tool (Mac).
# Creates a private Python environment in this folder and installs the packages it needs.
cd "$(dirname "$0")"
echo
echo "=== Wound Healing batch tool: setup ==="
echo
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer was not found."
  echo "Install it from https://www.python.org/downloads/ , then run this file again."
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi
echo "Creating the tool's Python environment..."
"$PY" -m venv .venv || { echo "Setup FAILED."; read -n 1 -s -r -p "Press any key to close."; exit 1; }
echo "Installing packages (this takes a few minutes)..."
.venv/bin/python -m pip install --upgrade pip
if ! .venv/bin/python -m pip install -r requirements.txt; then
  echo; echo "Setup FAILED. Check your internet connection, then run this file again."
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi
chmod +x Run_Wound_Analysis.command 2>/dev/null
echo
echo "=== Setup complete. You can now double-click Run_Wound_Analysis.command ==="
read -n 1 -s -r -p "Press any key to close."
