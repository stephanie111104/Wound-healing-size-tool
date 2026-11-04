#!/bin/bash
# Run the Wound Healing batch analysis (Mac). Opens a window to choose the plate folder.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import numpy, scipy, skimage, PIL, openpyxl" >/dev/null 2>&1; then
  echo "The tool is not set up yet (or setup did not finish)."
  if [ -x .venv/bin/python ]; then echo "The tool's environment uses $(.venv/bin/python --version 2>&1), but its packages are missing."; fi
  echo
  echo "Fix: make sure Python 3.13 is installed (https://www.python.org/downloads/macos/ - scroll to 3.13.x),"
  echo "then double-click setup_mac.command and wait for 'Setup complete'."
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi
.venv/bin/python run_wound_analysis.py "$@"
echo
read -n 1 -s -r -p "Press any key to close."
