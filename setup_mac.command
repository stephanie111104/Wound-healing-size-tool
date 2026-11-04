#!/bin/bash
# One-time setup for the Wound Healing batch tool (Mac).
# Creates a private Python environment in this folder and installs the packages it needs.
cd "$(dirname "$0")"
echo
echo "=== Wound Healing batch tool: setup ==="
echo

# Use a Python version that every package has ready-made downloads for (3.10 to 3.14).
# Newer versions (3.15+) are skipped until the image-analysis packages support them.
PY=""
for v in 3.13 3.12 3.14 3.11 3.10; do
  for c in "python$v" "/Library/Frameworks/Python.framework/Versions/$v/bin/python$v" "/opt/homebrew/bin/python$v" "/usr/local/bin/python$v"; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break 2; fi
  done
done
if [ -z "$PY" ] && command -v python3 >/dev/null 2>&1; then
  if python3 -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)" 2>/dev/null; then PY=python3; fi
fi
if [ -z "$PY" ]; then
  FOUND=$(python3 --version 2>/dev/null)
  echo "No compatible Python found${FOUND:+ (this Mac has $FOUND)}."
  echo
  echo "The tool needs Python 3.10 to 3.14. Newer versions such as 3.15 are not supported yet,"
  echo "because one of the image-analysis packages has not released a version for them."
  echo
  echo "Fix: install Python 3.13 from https://www.python.org/downloads/macos/"
  echo "(scroll to 'Python 3.13.x', download the macOS 64-bit universal2 installer)."
  echo "You do not need to uninstall your current Python. Then run this file again."
  echo
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi
echo "Using $("$PY" --version) ($PY)"
echo "Creating the tool's Python environment..."
if ! "$PY" -m venv --clear .venv; then
  echo "Setup FAILED while creating the environment."; rm -rf .venv
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi
echo "Installing packages (this takes a few minutes)..."
.venv/bin/python -m pip install --upgrade pip
if ! .venv/bin/python -m pip install --only-binary=:all: -r requirements.txt; then
  rm -rf .venv
  echo
  echo "Setup FAILED while installing packages."
  echo "If the messages above mention 'No matching distribution', send a screenshot of this window."
  echo "Otherwise check your internet connection and run this file again."
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi
chmod +x Run_Wound_Analysis.command 2>/dev/null
echo
echo "=== Setup complete. You can now double-click Run_Wound_Analysis.command ==="
read -n 1 -s -r -p "Press any key to close."
