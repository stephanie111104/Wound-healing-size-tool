#!/bin/bash
# Run the Wound Healing batch analysis (Mac). Opens a window to choose the plate folder.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "The tool is not set up yet. Double-click setup_mac.command first."
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi
.venv/bin/python run_wound_analysis.py "$@"
echo
read -n 1 -s -r -p "Press any key to close."
