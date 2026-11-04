"""Wound healing batch analysis: entry point.

Double-click Run_Wound_Analysis (.bat on Windows, .command on Mac), or run:

    python run_wound_analysis.py                 # opens a folder picker
    python run_wound_analysis.py "path/to/Plate1"
    python run_wound_analysis.py "path/to/Plate1" --workers 2
"""
import argparse
import os
import subprocess
import sys
from multiprocessing import freeze_support


def pick_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = filedialog.askdirectory(title='Choose the plate folder (the folder with A1_0h, A1_6h, ... images)')
        root.destroy()
        return path or None
    except Exception:
        path = input('Drag the plate folder into this window (or type its path), then press Enter:\n> ')
        return path.strip().strip('"').strip("'") or None


def open_folder(path):
    try:
        if sys.platform.startswith('win'):
            os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.run(['open', path], check=False)
        else:
            subprocess.run(['xdg-open', path], check=False)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description='Batch wound-closure analysis for a plate folder.')
    ap.add_argument('folder', nargs='?', help='plate folder (omit to choose in a window)')
    ap.add_argument('--workers', type=int, default=None, help='wells processed in parallel (default: automatic)')
    ap.add_argument('--no-open', action='store_true', help='do not open the results folder when done')
    args = ap.parse_args()

    folder = args.folder or pick_folder()
    if not folder:
        print('No folder chosen.')
        return 1
    if not os.path.isdir(folder):
        print('Not a folder: %s' % folder)
        return 1

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from wound_tool.batch import run
    out = run(folder, workers=args.workers)
    if out and not args.no_open:
        open_folder(out)
    return 0 if out else 1


if __name__ == '__main__':
    freeze_support()
    sys.exit(main())
