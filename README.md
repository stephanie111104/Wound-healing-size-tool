# Wound Healing Size Tool

Measures scratch-wound closure in wound healing assay images.

This repository holds two tools:

| Tool | Where | Use it for |
| --- | --- | --- |
| **Batch analysis tool (Python), current** | `run_wound_analysis.py`, `wound_tool/`, launchers | Whole plates at once, with the improved method: 0 h outline, alignment between time points, debris and marker handling |
| ImageJ/Fiji macro (2021, original) | `Wound_healing_size_tool_updated.ijm` + manual PDF | One image at a time with the original method; kept for reference |

## Batch analysis tool

### One-time setup

1. Install **Python 3.10 or newer** from [python.org/downloads](https://www.python.org/downloads/).
   On Windows, tick **"Add python.exe to PATH"** on the first installer screen.
2. On this GitHub page, choose the `edits-tool` branch, click **Code > Download ZIP**, and unzip it somewhere permanent (for example `Documents/WoundTool`).
3. In the unzipped folder, double-click **`setup_windows.bat`** (Windows) or **`setup_mac.command`** (Mac) and wait for "Setup complete".
   On a Mac, if it is blocked as coming from an unidentified developer, right-click the file, choose **Open**, then confirm.

### Each time

1. Put all images of one plate in one folder, named `<Well>_<Time>h`, for example `A1_0h.tif`, `A1_6h.tif`, `A1_24h.tif`.
   Every well needs exactly one `0h` image. A second field in the same well: `A1-2_0h.tif`.
   Optional: a `settings.txt` in the folder with `um_per_pixel = 1.0` adds areas in µm².
2. Double-click **`Run_Wound_Analysis.bat`** (Windows) or **`Run_Wound_Analysis.command`** (Mac) and choose the plate folder.
3. Results appear in a new `Results_<date>_<time>` folder inside the plate folder:
   - `wound_results.xlsx`: one row per image (`Results`), closure by well (`Summary`), run details (`Settings`)
   - `figures/`: per well, the original images with the cell-free area in blue below
   - `overlays/`: every image with the cell-free area (blue) and 0 h outline (red)
   - `outlines/`: the outlines as ImageJ `.roi` files (open in Fiji to check)
   - `run_log.txt`: alignment details and any warnings
4. Check only the rows whose `qc_flag` is not `OK`.

Command line (optional): `python run_wound_analysis.py "path/to/plate folder" [--workers N]`.

### Method

See the docstring at the top of `wound_tool/analysis.py`. Parameters are tuned on images 2576 px wide; other sizes are resized to that width for analysis and areas are reported in the original pixels.

## Original ImageJ macro (2021)

- **Macro:** `Wound_healing_size_tool_updated.ijm`
- **Manual:** `Manual_Wound_healing_size_tool_updated.pdf`

Earlier releases are kept as zip files for reference only: `Wound_healing_size_tool .zip.zip` (April 2020), `Wound_healing_size_tool.zip` (June 2020, with manual), `Wound_healing_size_tool_updated.zip` (June 2021). `RotateLogo.ijm` / `.zip` is a small logo macro; the `.jpg` / `.tif` files are sample images.
