@echo off
REM Run the Wound Healing batch analysis (Windows). Opens a window to choose the plate folder.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto notsetup
".venv\Scripts\python.exe" -c "import numpy, scipy, skimage, PIL, openpyxl" >nul 2>nul
if errorlevel 1 goto notsetup
".venv\Scripts\python.exe" run_wound_analysis.py %*
echo.
pause
exit /b 0

:notsetup
echo The tool is not set up yet (or setup did not finish).
echo Fix: make sure Python 3.13 is installed (open Command Prompt and type: py install 3.13),
echo then double-click setup_windows.bat and wait for "Setup complete".
pause
exit /b 1
